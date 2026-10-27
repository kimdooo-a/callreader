"""Show where a quote sits on the poster or PDF page — and only if it really sits there.

Muse Spark grounds each verified quote to a box on the page image (0–1000 grid).
A box is a claim like any other: we crop it, have the crop read back, and keep the
highlight only when the read-back contains the quote. A highlight on the wrong
line would be worse than none.
"""
from __future__ import annotations

import difflib
import io
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from . import meta
from .axes import Card, State
from .verify import normalize

GROUND_PROMPT = """You are a text grounding expert. Find each quoted passage on this page image and give the bounding
box that covers exactly the lines of that passage. Passages:
{passages}
Coordinates are in the 0-1000 range (x left→right, y top→bottom). Omit a passage you cannot find."""

GROUND_SCHEMA = meta.obj({"items": {"type": "array", "items": meta.obj({
    "id": {"type": "integer"},
    "x_min": {"type": "integer"}, "y_min": {"type": "integer"},
    "x_max": {"type": "integer"}, "y_max": {"type": "integer"}})}})

READBACK_PROMPT = "Transcribe the text in this image exactly as written. Reply EMPTY if there is none."
MIN_READBACK = 0.6  # share of the quote's characters that must be recovered from the crop
LINE = 30  # about one text line on a page, in the 0–1000 grid


def _pages_with_images(sources: list[dict]) -> dict[tuple[str, int], tuple[dict, bytes]]:
    """(source id, 1-based page) → (source, image bytes) for every page we have pixels for."""
    out = {}
    for s in sources:
        for i, img in enumerate(s.get("images") or []):
            out[(s["id"], i + 1)] = (s, img)
    return out


def _matches(quote: str, sources: list[dict]) -> list[tuple[str, int]]:
    q = normalize(quote)
    return [(s["id"], i + 1) for s in sources for i, p in enumerate(s.get("pages", [])) if q and q in normalize(p)]


def _readback_ok(img: bytes, box: dict, quote: str) -> bool:
    im = Image.open(io.BytesIO(img)).convert("RGB")
    W, H = im.size
    pad = 8
    x1 = max(0, int(box["x_min"] / 1000 * W) - pad)
    y1 = max(0, int(box["y_min"] / 1000 * H) - pad)
    x2 = min(W, int(box["x_max"] / 1000 * W) + pad)
    y2 = min(H, int(box["y_max"] / 1000 * H) + pad)
    if x2 - x1 < 10 or y2 - y1 < 6:
        return False
    buf = io.BytesIO()
    im.crop((x1, y1, x2, y2)).save(buf, "PNG")
    text = meta.chat([{"role": "user", "content": [{"type": "text", "text": READBACK_PROMPT},
                                                   meta.image_part(buf.getvalue(), "image/png")]}], max_tokens=4000)
    got, want = normalize(text).replace(" ", ""), normalize(quote).replace(" ", "")
    if not want:
        return False
    sm = difflib.SequenceMatcher(None, want, got, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(want) >= MIN_READBACK


def locate(card: Card) -> None:
    """Attach verified boxes to findings: finding.boxes = [{source, page, kind, box}]."""
    pages = _pages_with_images(card.sources)
    want: dict[tuple[str, int], list[tuple[object, str, str]]] = {}
    for f in card.findings:
        f.boxes = []
        if f.state == State.UNREAD:
            continue
        for kind, quote in (("quote", f.quote), ("condition", f.condition_quote if f.condition_verified else "")):
            if not quote:
                continue
            for key in _matches(quote, card.sources):
                if key in pages:
                    want.setdefault(key, []).append((f, kind, quote))
                    break

    def ground(key):
        src, img = pages[key]
        items = want[key]
        passages = "\n".join(f'{n}. "{q}"' for n, (_, _, q) in enumerate(items, 1))
        raw = meta.chat([{"role": "user", "content": [{"type": "text", "text": GROUND_PROMPT.format(passages=passages)},
                                                      meta.image_part(img, "image/png")]}],
                        max_tokens=8000, schema=GROUND_SCHEMA)
        try:
            boxes = {b["id"]: b for b in meta.parse_json(raw)["items"]}
        except (ValueError, KeyError):
            return []
        kept = []
        for n, (f, kind, quote) in enumerate(items, 1):
            b = boxes.get(n)
            if not b:
                continue
            if not _readback_ok(img, b, quote):
                # Grounding on dense text pages is often one line off; widen by about a line each way and re-check.
                b = {"x_min": max(0, b["x_min"] - 10), "x_max": min(1000, b["x_max"] + 10),
                     "y_min": max(0, b["y_min"] - LINE), "y_max": min(1000, b["y_max"] + LINE)}
                if not _readback_ok(img, b, quote):
                    continue
            kept.append((f, {"source": key[0], "page": key[1], "kind": kind,
                             "box": [b["x_min"], b["y_min"], b["x_max"], b["y_max"]]}))
        return kept

    with ThreadPoolExecutor(max_workers=6) as pool:
        for kept in pool.map(ground, list(want)):
            for f, box in kept:
                f.boxes.append(box)
