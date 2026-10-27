"""SAM 3.1 finds the QR codes on a poster; code decodes them.

Many calls reach people as a single poster image. The poster is a teaser; the rules
sit behind its QR code. A reader that transcribes the poster and stops there has
read the advert, not the call. So every QR region SAM finds becomes a link we must
follow — or an UNREAD record if we cannot decode it.
"""
from __future__ import annotations

import base64
import io
import json
import re

import requests
from PIL import Image

from . import meta

BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")

def segment(image: bytes, mime: str, concept: str, timeout: int = 120) -> list[dict]:
    """Boxes (in source pixels) for every instance of `concept`. Masks are ignored — we only crop."""
    key = meta.api_key()
    data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    r = requests.post(
        "https://api.meta.ai/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "sam-3.1", "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_image", "image_url": data_url}]}]},
        timeout=timeout)
    r.raise_for_status()
    meta.sam_images += 1
    text = "".join(
        c.get("text", "") for item in r.json().get("output", []) for c in item.get("content", []))
    boxes, seen = [], set()
    for m in BOX.finditer(text):
        x1, y1, x2, y2, w, h = map(int, m.groups())
        if (x1, y1, x2, y2) in seen:
            continue
        seen.add((x1, y1, x2, y2))
        boxes.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": w, "h": h})
    return boxes


def _decode(img: Image.Image) -> list[str]:
    import zxingcpp
    return [r.text for r in zxingcpp.read_barcodes(img) if r.text]


def find_qr_links(image: bytes, mime: str) -> list[dict]:
    """[{box, url|None, reason}] — one record per QR region SAM found."""
    img = Image.open(io.BytesIO(image)).convert("RGB")
    out = []
    for b in segment(image, mime, "QR code"):
        pad = max(8, (b["x2"] - b["x1"]) // 6)  # the quiet zone matters for decoding
        crop = img.crop((max(0, b["x1"] - pad), max(0, b["y1"] - pad),
                         min(img.width, b["x2"] + pad), min(img.height, b["y2"] + pad)))
        texts = _decode(crop) or _decode(crop.resize((crop.width * 3, crop.height * 3), Image.NEAREST))
        if texts:
            out.append({"box": b, "url": texts[0], "reason": ""})
        else:
            out.append({"box": b, "url": None, "reason": "QR code found by SAM 3.1 but could not be decoded"})
    return out


if __name__ == "__main__":  # python -m callreader.segment poster.jpg
    import sys
    path = sys.argv[1]
    raw = open(path, "rb").read()
    print(json.dumps(find_qr_links(raw, "image/png" if path.lower().endswith(".png") else "image/jpeg"),
                     ensure_ascii=False, indent=2))
