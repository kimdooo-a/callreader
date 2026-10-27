"""Self-contained HTML card: verdicts on the left, the page images with verified highlights on the right."""
from __future__ import annotations

import base64
import html
import io
import os
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image

from . import meta
from .axes import Card, State
from .verify import normalize, quote_context

LABEL = {"eligibility": "Eligibility", "fee": "Entry fee", "cash": "Cash prize", "onsite": "On-site / team",
         "ai_use": "AI use", "authorship": "Authorship & rights", "deadline": "Deadline"}

CSS = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--card:#fff;
--pass:#1f7a4d;--pass-bg:#e5f3ea;--fail:#b3261e;--fail-bg:#fbe6e4;--unread:#8a6100;--unread-bg:#fbf1d6;--hl:#2f6fed}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--mut:#9b9a93;--line:#2c2c29;--card:#1c1c1a;
--pass:#6fd39b;--pass-bg:#173326;--fail:#ff8a80;--fail-bg:#3a1c1a;--unread:#f1c75b;--unread-bg:#3a2f10;--hl:#7fa8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
header{padding:20px 24px;border-bottom:1px solid var(--line)}h1{font-size:19px;margin:0 0 4px}
.meta{color:var(--mut);font-size:13px}.verdict{display:inline-block;font-weight:700;padding:2px 10px;border-radius:99px;margin-right:8px}
main{display:grid;grid-template-columns:minmax(320px,460px) 1fr;gap:0}@media (max-width:860px){main{grid-template-columns:1fr}}
main.solo{grid-template-columns:minmax(320px,760px)}main.solo .left{border-right:0}
.left{padding:16px 20px;border-right:1px solid var(--line)}.right{padding:16px 20px}
.f{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;cursor:pointer}
.f.active{outline:2px solid var(--hl)}.f h3{margin:0 0 4px;font-size:14px;display:flex;gap:8px;align-items:center}
.st{font-size:11px;font-weight:700;padding:1px 8px;border-radius:99px;letter-spacing:.03em}
.PASS{color:var(--pass);background:var(--pass-bg)}.FAIL{color:var(--fail);background:var(--fail-bg)}.UNREAD{color:var(--unread);background:var(--unread-bg)}
blockquote{margin:6px 0;padding:4px 10px;border-left:3px solid var(--line);font-size:14px}
.src,.note{color:var(--mut);font-size:12px}.cond{font-size:13px;margin-top:4px}.warn{color:var(--unread)}
.page{position:relative;margin:0 0 18px;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}
.page img{display:block;width:100%;height:auto}.cap{font-size:12px;color:var(--mut);margin:0 0 4px}
.box{position:absolute;border:2px solid var(--hl);background:color-mix(in srgb,var(--hl) 14%,transparent);border-radius:3px;transition:box-shadow .2s}
.box.cond{border-style:dashed}.box.on{box-shadow:0 0 0 4px color-mix(in srgb,var(--hl) 45%,transparent)}
.ctx{font-size:12.5px;color:var(--mut);margin-top:6px;padding:6px 8px;background:var(--bg);border-radius:6px}
mark{background:color-mix(in srgb,var(--hl) 25%,transparent);color:var(--fg);padding:0 1px}
.unread-list{font-size:13px}.unread-list li{margin-bottom:4px}
"""

JS = """
document.querySelectorAll('.f').forEach(el=>el.addEventListener('click',()=>{
 document.querySelectorAll('.f').forEach(x=>x.classList.remove('active'));el.classList.add('active');
 document.querySelectorAll('.box').forEach(b=>b.classList.toggle('on',b.dataset.axis===el.dataset.axis));
 const b=document.querySelector('.box[data-axis="'+el.dataset.axis+'"]');if(b)b.scrollIntoView({behavior:'smooth',block:'center'});}));
"""


def _label(sid: str | None, card: Card) -> str:
    """att3 → 'hwpx · [공고문] ….hwpx' — the reader should see which file the rule came from."""
    src = next((s for s in card.sources if s["id"] == sid), None)
    if not src:
        return sid or ""
    q = parse_qs(urlparse(src["url"]).query)
    name = (q.get("user_file_nm") or q.get("filename") or q.get("fileName") or [""])[0]
    if not name:
        name = _shown(src["url"]) if not src["url"].startswith("http") else unquote(urlparse(src["url"]).path.rsplit("/", 1)[-1])
    kind = src["kind"].replace("-vision", " (vision)").replace("image-ocr", "poster")
    return f"{kind} · {name}" if name else kind


def _shown(url: str) -> str:
    """Local paths carry the user name; a report may be shared, so show only the file name."""
    return url if url.startswith(("http://", "https://")) else os.path.basename(url.split("#")[0])


def _jpeg_data_url(png: bytes, max_w: int = 1200) -> str:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def render(card: Card) -> str:
    e = html.escape
    v = card.verdict.value
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>CallReader — {e(card.title or card.url)[:80]}</title><style>{CSS}</style></head><body>",
             f"<header><h1>{e(card.title or card.url)}</h1><div class='meta'><span class='verdict {v}'>{v}</span>"
             f"{e(_shown(card.url))} · model cost ${meta.cost_usd():.3f}</div></header><main><section class='left'>"]
    for f in card.findings:
        s = f.state.value
        parts.append(f"<div class='f' data-axis='{f.axis}'><h3><span class='st {s}'>{s}</span>{LABEL.get(f.axis, f.axis)}</h3>"
                     f"<div>{e(f.answer)}</div>")
        if f.quote and f.state != State.UNREAD:
            parts.append(f"<blockquote>{e(f.quote)}</blockquote><div class='src'>{e(_label(f.source_id, card))} · p.{f.page}"
                         f"{' · highlighted' if any(b['kind'] == 'quote' for b in f.boxes) else ''}</div>")
            if not any(b["kind"] == "quote" for b in f.boxes):
                # No page image (HTML, HWPX, HWP): show the quote marked inside the text it was cut from.
                ctx = quote_context(f.quote, card.sources, before=220, after=160)
                q = normalize(f.quote)
                if ctx and q in ctx:
                    i = ctx.index(q)
                    parts.append(f"<div class='ctx'>…{e(ctx[:i])}<mark>{e(ctx[i:i + len(q)])}</mark>{e(ctx[i + len(q):])}…</div>")
        if f.condition:
            if f.condition_verified:
                parts.append(f"<div class='cond'>Condition: {e(f.condition)}</div><blockquote>{e(f.condition_quote)}</blockquote>")
            else:
                parts.append(f"<div class='cond warn'>Condition (unverified — no quote found): {e(f.condition)}</div>")
        if f.note:
            parts.append(f"<div class='note'>{e(f.note)}</div>")
        parts.append("</div>")
    if card.unread_sources:
        parts.append("<h3>Could not read</h3><ul class='unread-list'>" + "".join(
            f"<li>{e(_shown(u['url']))}<br><span class='note'>{e(u['reason'])}</span></li>" for u in card.unread_sources) + "</ul>")
    parts.append("</section><section class='right'>")
    boxes_by_page: dict[tuple[str, int], list] = {}
    for f in card.findings:
        for b in getattr(f, "boxes", []):
            boxes_by_page.setdefault((b["source"], b["page"]), []).append((f.axis, b))
    shown = [k for k in boxes_by_page] or []
    srcs = {s["id"]: s for s in card.sources}
    for key in sorted(shown):
        src = srcs.get(key[0])
        if not src or not src.get("images"):
            continue
        img = src["images"][key[1] - 1]
        parts.append(f"<p class='cap'>{e(_shown(src['url']))} · page {key[1]}</p><div class='page'><img src='{_jpeg_data_url(img)}' alt=''>")
        for axis, b in boxes_by_page[key]:
            x1, y1, x2, y2 = b["box"]
            parts.append(f"<div class='box{' cond' if b['kind'] == 'condition' else ''}' data-axis='{axis}' title='{LABEL.get(axis, axis)}' "
                         f"style='left:{x1 / 10}%;top:{y1 / 10}%;width:{(x2 - x1) / 10}%;height:{(y2 - y1) / 10}%'></div>")
        parts.append("</div>")
    parts.append(f"</section></main><script>{JS}</script></body></html>")
    out = "".join(parts)
    if not shown:
        # Nothing to show on a page image (HTML, HWPX, HWP sources): one column, quotes marked in their text.
        out = out.replace("<main>", "<main class='solo'>", 1).replace("<section class='right'></section>", "")
    return out
