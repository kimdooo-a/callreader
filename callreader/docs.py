"""Turn attachment bytes into page texts — PDF, HWPX, HWP 5.

Korean public calls attach the binding rules as PDF or as Hancom documents
(.hwpx / .hwp). A reader that skips Hancom files skips the rules. And a PDF text
layer extracted in the wrong reading order ("년2026 월 9 일 14") breaks every
quote we later try to verify, so PDF text comes from PyMuPDF in sorted order.
"""
from __future__ import annotations

import io
import re
import struct
import zipfile
import zlib


def sniff(data: bytes) -> str:
    head = data[:16]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "unknown"
        return "hwpx" if any(n.startswith("Contents/section") for n in names) else "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"  # HWP 5 (or a legacy Office file) — decided by its streams
    if b"<html" in data[:512].lower() or b"<!doctype" in data[:512].lower():
        return "html"
    return "unknown"


def pdf_pages(data: bytes) -> list[str]:
    try:
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
        return [page.get_text("text", sort=True) for page in doc]
    except ImportError:
        from pypdf import PdfReader
        return [(p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages]


def hwpx_pages(data: bytes) -> list[str]:
    """One page per section; paragraphs from <hp:p>, runs from <hp:t>."""
    z = zipfile.ZipFile(io.BytesIO(data))
    sections = sorted((n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                      key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    pages = []
    for name in sections:
        xml = z.read(name).decode("utf-8", errors="replace")
        paras = []
        for p in re.findall(r"<hp:p\b.*?</hp:p>", xml, re.DOTALL):
            runs = re.findall(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", p, re.DOTALL)
            text = "".join(re.sub(r"<[^>]+>", "", r) for r in runs)
            text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
            if text.strip():
                paras.append(text)
        pages.append("\n".join(paras))
    return pages


_PARA_TEXT = 67  # HWPTAG_BEGIN(16) + 51


def _hwp_records(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        header = struct.unpack_from("<I", data, pos)[0]
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        pos += 4
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        yield tag, data[pos:pos + size]
        pos += size


def _hwp_para_text(payload: bytes) -> str:
    """UTF-16LE with inline control codes: 8-unit controls must be skipped whole."""
    out, i = [], 0
    n = len(payload) // 2
    while i < n:
        c = struct.unpack_from("<H", payload, i * 2)[0]
        if c in (10, 13):
            out.append("\n")
            i += 1
        elif c == 9:
            out.append("\t")
            i += 8
        elif c < 32:
            i += 8 if c in (1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23) else 1
        else:
            out.append(chr(c))
            i += 1
    return "".join(out)


def hwp5_pages(data: bytes) -> list[str] | None:
    """None if this OLE file is not HWP 5 or is encrypted/distribution-locked."""
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))
    if not ole.exists("FileHeader"):
        return None
    header = ole.openstream("FileHeader").read()
    flags = struct.unpack_from("<I", header, 36)[0]
    compressed, encrypted, distribution = flags & 1, flags & 2, flags & 4
    if encrypted or distribution:
        return None
    pages = []
    sections = sorted((e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText"),
                      key=lambda e: int(re.sub(r"\D", "", e[1]) or 0))
    for entry in sections:
        raw = ole.openstream(entry).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        paras = [_hwp_para_text(p) for tag, p in _hwp_records(raw) if tag == _PARA_TEXT]
        pages.append("\n".join(t for t in paras if t.strip()))
    return pages


def extract(data: bytes) -> tuple[str, list[str] | None, str]:
    """(kind, pages or None, reason when None)."""
    kind = sniff(data)
    if kind == "pdf":
        pages = pdf_pages(data)
        if not any(p.strip() for p in pages):
            return "pdf-scan", pages, ""
        return "pdf", pages, ""
    if kind == "hwpx":
        return "hwpx", hwpx_pages(data), ""
    if kind == "ole":
        try:
            pages = hwp5_pages(data)
        except Exception as e:  # damaged stream
            return "hwp", None, f"HWP file could not be parsed ({e.__class__.__name__})"
        if pages is None:
            return "hwp", None, "HWP file is encrypted or distribution-locked, or not an HWP 5 document"
        return "hwp", pages, ""
    return kind, None, ""
