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

from .docs import extract

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


MIN_POSTER_SIDE = 300  # logos and icons are not posters


def _image_size(data: bytes) -> tuple[int, int]:
    from PIL import Image
    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:
        return (0, 0)


def image_key(data: bytes) -> int:
    """64-bit average hash: a poster and its thumbnail get the same key."""
    from PIL import Image
    g = Image.open(io.BytesIO(data)).convert("L").resize((8, 8))
    px = list(g.tobytes())
    avg = sum(px) / 64
    return sum(1 << i for i, v in enumerate(px) if v > avg)


def _same_image(a: int, b: int) -> bool:
    return bin(a ^ b).count("1") <= 4


def source_from_bytes(data: bytes, url: str, sid: str) -> dict:
    """{"source": …} | {"unread": …} | {"skip": reason} | {"html": …} for one downloaded file."""
    kind, pages, why = extract(data)
    if kind in ("pdf", "pdf-scan", "hwpx", "hwp"):
        if pages is None:
            return {"unread": {"url": url, "reason": why}}
        src = {"id": sid, "kind": kind, "url": url, "pages": pages}
        if kind in ("pdf", "pdf-scan"):
            src["bytes"] = data  # read twice: text layer here, rendered pages by vision later
        return {"source": src}
    if kind in ("png", "jpeg", "webp"):
        w, h = _image_size(data)
        if min(w, h) < MIN_POSTER_SIDE:
            return {"skip": f"small image {w}x{h}"}
        return {"source": {"id": sid, "kind": "image", "url": url, "pages": [""], "bytes": data,
                           "mime": f"image/{kind}", "size": [w, h]}}
    if kind == "html":
        return {"html": data}
    return {"unread": {"url": url, "reason": f"unsupported file type ({kind})"}}


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


def _viewer_image(html: bytes, base: str) -> str | None:
    """Some boards link posters through an HTML viewer (view_image.php); take the one big <img> inside."""
    soup = BeautifulSoup(html, "html.parser")
    imgs = [urljoin(base, i["src"]) for i in soup.find_all("img", src=True)]
    imgs = [u for u in imgs if ATTACH_EXT.search(u)]
    return imgs[0] if len(imgs) == 1 else None


def fetch_call(url: str, timeout: int = 30, session: requests.Session | None = None, prefix: str = "") -> dict:
    """Return {title, sources: [{id, kind, url, pages|image}], unread: [{url, reason}]}."""
    s = session or requests.Session()
    s.headers.update({"User-Agent": UA})
    r = s.get(url, timeout=timeout)
    r.raise_for_status()
    html = decode(r)
    title, text = _text_from_html(html)
    sources = [{"id": f"{prefix}page", "kind": "html", "url": url, "pages": [text]}]
    unread: list[dict] = []
    images: list[dict] = []
    for i, au in enumerate(find_attachments(html, url), 1):
        sid = f"{prefix}att{i}"
        try:
            ar = s.get(au, timeout=timeout, headers={"Referer": url})
            ar.raise_for_status()
            got = source_from_bytes(ar.content, au, sid)
            if "html" in got and ATTACH_EXT.search(au):
                inner = _viewer_image(got["html"], au)
                if inner:
                    ar = s.get(inner, timeout=timeout, headers={"Referer": au})
                    got = source_from_bytes(ar.content, inner, sid)
                if "html" in got:
                    got = {"unread": {"url": au, "reason": f"server returned an HTML page ({len(ar.content)} bytes) "
                                                          "instead of the file — likely needs a session or referer"}}
            if "source" in got:
                src = got["source"]
                if src["kind"] == "image":
                    # The same poster is often linked twice (viewer + thumbnail); keep the largest copy only.
                    src["key"] = image_key(src["bytes"])
                    twin = next((o for o in images if _same_image(o["key"], src["key"])), None)
                    if twin:
                        if src["size"][0] > twin["size"][0]:
                            twin.update({k: src[k] for k in ("url", "bytes", "mime", "size")})
                        continue
                    images.append(src)
                sources.append(src)
            elif "unread" in got:
                unread.append(got["unread"])
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
    got = source_from_bytes(r.content, url, sid)
    if "source" in got or "unread" in got:
        return got
    if "skip" in got:
        return {"unread": {"url": url, "reason": got["skip"]}}
    html = decode(r)
    title, text = _text_from_html(html)
    if len(text.strip()) < 200:
        # Script-rendered pages arrive empty; that is "unread", not "no rules".
        return {"unread": {"url": url, "reason": "page body is empty without JavaScript (client-rendered)"}}
    return {"source": {"id": sid, "kind": "html", "url": url, "pages": [text], "title": title}, "html": html}
