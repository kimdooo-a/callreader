"""Fetch a call page and every attachment it links to.

The rule that decides an entry is often not on the page — it sits in an attached
PDF. And a download that "succeeded" can be a 157-byte error page saved under a
.pdf name. So every attachment is sniffed by its bytes, not its extension, and
anything we cannot open is reported as unread instead of silently skipped.
"""
from __future__ import annotations

import io
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
ATTACH_EXT = re.compile(r"\.(pdf|png|jpe?g|webp)(\?|$)", re.IGNORECASE)
ATTACH_HINT = re.compile(r"(download|attach|file|첨부|다운로드|요강|guideline|rules)", re.IGNORECASE)
MAX_ATTACH = 12


def decode(resp: requests.Response) -> str:
    """requests falls back to ISO-8859-1 when the header has no charset; Japanese/Korean pages then turn to mojibake.
    Trust the <meta charset>, then byte detection, before that fallback."""
    ctype = resp.headers.get("content-type", "").lower()
    if "charset=" in ctype:
        return resp.text
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", resp.content[:4096], re.IGNORECASE)
    enc = m.group(1).decode() if m else (resp.apparent_encoding or "utf-8")
    try:
        return resp.content.decode(enc, errors="replace")
    except LookupError:
        return resp.content.decode("utf-8", errors="replace")


def _text_from_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    title = (soup.title.string or "").strip() if soup.title else ""
    # Keep attribute text that carries meaning (alt, title) — strip-tags loses it.
    extras = [el.get(a) for el in soup.find_all(True) for a in ("alt", "title") if el.get(a)]
    text = soup.get_text("\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return title, text + ("\n" + "\n".join(extras) if extras else "")


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
    if b"<html" in data[:512].lower() or b"<!doctype" in data[:512].lower():
        return "html"
    return "unknown"


def pdf_pages(data: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    return [(p.extract_text() or "") for p in reader.pages]


def find_attachments(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        label = (a.get_text(" ") or "") + " " + a["href"]
        if ATTACH_EXT.search(href) or ATTACH_HINT.search(label):
            if urlparse(href).scheme in ("http", "https") and href not in urls and href != base:
                urls.append(href)
    for img in soup.find_all("img", src=True):
        src = urljoin(base, img["src"])
        # Posters are usually large images inside the article body; icons are noise.
        w = img.get("width")
        if (w is None or (str(w).isdigit() and int(w) >= 300)) and ATTACH_EXT.search(src):
            if src not in urls:
                urls.append(src)
    return urls[:MAX_ATTACH]


def fetch_call(url: str, timeout: int = 30) -> dict:
    """Return {title, sources: [{id, kind, url, pages|image}], unread: [{url, reason}]}."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    r = s.get(url, timeout=timeout)
    r.raise_for_status()
    html = decode(r)
    title, text = _text_from_html(html)
    sources = [{"id": "page", "kind": "html", "url": url, "pages": [text]}]
    unread: list[dict] = []
    for i, au in enumerate(find_attachments(html, url), 1):
        try:
            ar = s.get(au, timeout=timeout, headers={"Referer": url})
            ar.raise_for_status()
            kind = sniff(ar.content)
            sid = f"att{i}"
            if kind == "pdf":
                pages = pdf_pages(ar.content)
                if not any(p.strip() for p in pages):
                    # Scanned PDF: no text layer. Needs vision OCR, not silence.
                    sources.append({"id": sid, "kind": "pdf-scan", "url": au, "pages": pages, "bytes": ar.content})
                else:
                    sources.append({"id": sid, "kind": "pdf", "url": au, "pages": pages})
            elif kind in ("png", "jpeg", "webp"):
                sources.append({"id": sid, "kind": "image", "url": au, "pages": [""], "bytes": ar.content, "mime": f"image/{kind}"})
            elif kind == "html" and ATTACH_EXT.search(au):
                unread.append({"url": au, "reason": f"server returned an HTML page ({len(ar.content)} bytes) instead of the file — likely needs a session or referer"})
            elif kind == "html":
                continue  # an ordinary linked page, not an attachment
            else:
                unread.append({"url": au, "reason": f"unsupported file type ({kind})"})
        except requests.RequestException as e:
            unread.append({"url": au, "reason": f"download failed: {e.__class__.__name__}"})
    return {"title": title, "sources": sources, "unread": unread, "html": html, "session": s}


def all_links(html: str, base: str, limit: int = 150) -> list[dict]:
    """Every outgoing link with its visible label — the menu the agent chooses from."""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        if urlparse(href).scheme not in ("http", "https") or href in seen or href == base:
            continue
        seen.add(href)
        label = " ".join((a.get_text(" ") or a.get("title") or "").split())[:80]
        out.append({"label": label, "url": href})
        if len(out) >= limit:
            break
    return out


def fetch_linked_page(session: requests.Session, url: str, referer: str, sid: str, timeout: int = 30) -> dict:
    """Fetch a page the agent chose to follow. Returns a source, or an unread record."""
    try:
        r = session.get(url, timeout=timeout, headers={"Referer": referer})
        r.raise_for_status()
    except requests.RequestException as e:
        return {"unread": {"url": url, "reason": f"download failed: {e.__class__.__name__}"}}
    kind = sniff(r.content)
    if kind == "pdf":
        return {"source": {"id": sid, "kind": "pdf", "url": url, "pages": pdf_pages(r.content)}}
    if kind == "html":
        html = decode(r)
        title, text = _text_from_html(html)
        if len(text.strip()) < 200:
            # Script-rendered pages arrive empty; that is "unread", not "no rules".
            return {"unread": {"url": url, "reason": "page body is empty without JavaScript (client-rendered)"}}
        return {"source": {"id": sid, "kind": "html", "url": url, "pages": [text], "title": title}, "html": html}
    return {"unread": {"url": url, "reason": f"unsupported file type ({kind})"}}
