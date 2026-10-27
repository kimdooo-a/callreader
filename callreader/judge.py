"""Read a call with Muse Spark, then let code decide which verdicts survive."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

from . import meta
from .axes import AXES, Card, Finding, State
from .verify import check_quote

TRANSCRIBE_PROMPT = (
    "Transcribe every piece of text visible in this image exactly as written, in its original language. "
    "Keep line breaks. Do not translate, summarize, or add anything. If there is no text, reply EMPTY."
)

JUDGE_PROMPT = """You are reading an open call (contest, grant, hackathon) for this entrant:
{profile}

Below are the call's sources, each tagged with an id and page number. Answer each axis.

Rules — they are strict:
1. For every axis return a verdict PASS, FAIL, or UNREAD.
2. PASS or FAIL requires a "quote": copy one sentence (or a short span) from the sources CHARACTER-FOR-CHARACTER,
   in its ORIGINAL language. Never translate, paraphrase, or merge sentences. Code will search for your quote;
   if it is not found verbatim, your verdict is discarded.
3. If the sources do not state the rule, answer UNREAD with quote "". Silence is not permission:
   "no mention of AI" is UNREAD unless you have read every source end to end.
4. If the rule has a condition ("unless", "only if", "except", "단,", "ただし"), put that clause in "condition"
   and copy the source text that states it, verbatim, into "condition_quote". A verdict without its governing
   condition is wrong; a condition you cannot quote will be shown to the entrant as unverified.
5. "answer" is a one-line plain-English summary for the entrant.

Axes:
{axes}

6. For the deadline axis also return "deadline_iso": the deadline as ISO 8601 with UTC offset
   (e.g. "2026-11-30T23:59:00-05:00"); if no time zone is stated write the date only. Do not judge whether it
   has passed — code compares it with the current time ({now}).

Return JSON: {{"items": [{{"axis": key, "verdict": ..., "answer": ..., "quote": ..., "source": id, "page": n, "condition": ..., "condition_quote": ..., "deadline_iso": ...}}]}}
Use "" for empty strings and 0 for an unknown page.

SOURCES:
{sources}
"""

SUPPORT_PROMPT = """You are auditing verdicts about an open call. For each item, decide ONLY from the quoted text
(and its condition quote) whether the quote by itself establishes the verdict for this entrant:
{profile}

A quote that names a topic does not establish a rule about it. "The theme is generative AI" says nothing
about whether AI tools may be used to make the entry. A quote about one thing does not settle another.
Judge the verdict on the axis question only; extra detail in the summary that the quote does not cover is
not by itself a reason for false. When unsure, answer false.

Items:
{items}

Return JSON: {{"items": [{{"axis": key, "supported": true|false, "why": "one short sentence"}}]}}"""

_S = {"type": "string"}
VERDICTS_SCHEMA = meta.obj({"items": {"type": "array", "items": meta.obj({
    "axis": {"type": "string", "enum": [a.key for a in AXES]},
    "verdict": {"type": "string", "enum": ["PASS", "FAIL", "UNREAD"]},
    "answer": _S, "quote": _S, "source": _S, "page": {"type": "integer"},
    "condition": _S, "condition_quote": _S, "deadline_iso": _S})}})
SUPPORT_SCHEMA = meta.obj({"items": {"type": "array", "items": meta.obj({
    "axis": _S, "supported": {"type": "boolean"}, "why": _S})}})

DEFAULT_PROFILE = "An individual (solo, not a student) living in South Korea, entering online, who uses generative AI tools."


VISION_MAX_PDF_PAGES = 20


def _transcribe(png: bytes, mime: str) -> str:
    text = meta.chat([{"role": "user", "content": [
        {"type": "text", "text": TRANSCRIBE_PROMPT}, meta.image_part(png, mime)]}])
    return "" if text.strip() == "EMPTY" else text


def _render_pdf(data: bytes, limit: int) -> list[bytes]:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    return [doc[i].get_pixmap(dpi=150).tobytes("png") for i in range(min(limit, doc.page_count))]


def ocr_images(card_sources: list[dict]) -> None:
    """Posters have no text layer, and a PDF's text layer can be out of reading order or missing.
    Muse Spark vision reads both; a PDF keeps its text layer AND gains a vision transcription as a sibling
    source, so a quote is verified against whichever reading preserved it."""
    jobs = []  # (target source, page index, image bytes, mime)
    extra = []
    for src in card_sources:
        if src["kind"] == "image" and src.get("bytes"):
            jobs.append((src, 0, src["bytes"], src.get("mime", "image/png")))
            src["kind"] = "image-ocr"
        elif src["kind"] in ("pdf", "pdf-scan") and src.get("bytes"):
            pngs = _render_pdf(src["bytes"], VISION_MAX_PDF_PAGES)
            sib = {"id": f"{src['id']}.vision", "kind": "pdf-vision", "url": src["url"], "pages": [""] * len(pngs)}
            extra.append(sib)
            jobs += [(sib, i, png, "image/png") for i, png in enumerate(pngs)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = list(pool.map(lambda j: _transcribe(j[2], j[3]), jobs))
    for (src, i, _, _), text in zip(jobs, texts):
        src["pages"][i] = text
    card_sources.extend(extra)


def _render_sources(sources: list[dict], budget_chars: int = 180_000) -> str:
    out, used = [], 0
    for src in sources:
        for i, page in enumerate(src.get("pages", []), 1):
            if not page.strip():
                continue
            chunk = f"\n=== source={src['id']} kind={src['kind']} page={i} ===\n{page}\n"
            if used + len(chunk) > budget_chars:
                out.append(f"\n=== (truncated: remaining text of {src['id']} not sent) ===\n")
                return "".join(out)
            out.append(chunk)
            used += len(chunk)
    return "".join(out)


def _deadline_state(iso: str | None, now: datetime) -> tuple[State, str]:
    if not iso:
        return State.UNREAD, "no machine-readable deadline"
    try:
        if len(iso) == 10:
            # Date only, no zone: assume the latest zone on Earth (AoE, UTC-12) so we never close a live call early.
            dl = datetime.fromisoformat(iso + "T23:59:59").replace(tzinfo=timezone(timedelta(hours=-12)))
            zone_note = " (no time zone stated — assumed AoE)"
        else:
            dl = datetime.fromisoformat(iso)
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone(timedelta(hours=-12)))
            zone_note = ""
    except ValueError:
        return State.UNREAD, f"unparseable deadline {iso!r}"
    left = dl - now
    if left.total_seconds() <= 0:
        return State.FAIL, f"deadline passed {-left.days} day(s) ago{zone_note}"
    return State.PASS, f"{left.days} day(s) left, until {dl.astimezone(UTC).isoformat(timespec='minutes')}{zone_note}"


def judge(card: Card, profile: str = DEFAULT_PROFILE) -> Card:
    ocr_images(card.sources)
    axes_txt = "\n".join(f"- {a.key}: {a.question} (FAIL means: {a.fail_means})" for a in AXES)
    now = datetime.now(UTC)
    prompt = JUDGE_PROMPT.format(
        profile=profile, axes=axes_txt, sources=_render_sources(card.sources), now=now.isoformat(timespec="minutes"))
    items = None
    for attempt in range(2):
        # Reasoning tokens count against max_tokens; a long call can exhaust the budget before any JSON is written.
        raw = meta.chat([{"role": "user", "content": prompt}], max_tokens=32000, schema=VERDICTS_SCHEMA)
        try:
            items = meta.parse_json(raw)["items"]
            break
        except ValueError:
            continue
    if items is None:
        card.findings = [Finding(a.key, State.UNREAD, note="model returned no parsable verdicts") for a in AXES]
        return card
    by_axis = {it.get("axis"): it for it in items if isinstance(it, dict)}
    card.findings = []
    for a in AXES:
        it = by_axis.get(a.key)
        if not it:
            card.findings.append(Finding(a.key, State.UNREAD, note="model returned nothing for this axis"))
            continue
        claimed = str(it.get("verdict", "UNREAD")).upper()
        f = Finding(axis=a.key, state=State.UNREAD, answer=it.get("answer", ""), quote=it.get("quote", "") or "",
                    condition=it.get("condition", "") or "", condition_quote=it.get("condition_quote", "") or "",
                    model_state=claimed)
        if f.condition:
            f.condition_verified = bool(f.condition_quote) and check_quote(f.condition_quote, card.sources).found
        if claimed in ("PASS", "FAIL"):
            chk = check_quote(f.quote, card.sources)
            if chk.found:
                f.state = State(claimed)
                f.source_id, f.page = chk.source_id, chk.page
            else:
                f.note = f"demoted from {claimed}: {chk.reason}"
        else:
            f.note = "model could not find the rule"
        if a.key == "deadline" and f.state != State.UNREAD:
            # The model reads the date; code decides whether it has passed.
            f.state, extra = _deadline_state(it.get("deadline_iso"), now)
            f.note = (f.note + "; " if f.note else "") + extra
        if f.state != State.FAIL and card.unread_sources and a.key in ("ai_use", "eligibility", "fee"):
            # A rule can hide in an attachment we could not open. Never call it PASS while one is unread.
            if f.state == State.PASS:
                f.state = State.UNREAD
                f.note = (f.note + "; " if f.note else "") + f"{len(card.unread_sources)} attachment(s) unread"
        card.findings.append(f)
    audit_support(card, profile)
    return card


def audit_support(card: Card, profile: str) -> None:
    """Second reading: the quote exists, but does it say what the verdict claims? Only ever demotes."""
    claimed = [f for f in card.findings if f.state in (State.PASS, State.FAIL) and f.axis != "deadline"]
    if not claimed:
        return
    question = {a.key: f"{a.question} (FAIL means: {a.fail_means})" for a in AXES}
    items = "\n".join(json.dumps({"axis": f.axis, "question": question[f.axis], "verdict": f.state.value,
                                  "summary": f.answer, "quote": f.quote,
                                  "condition_quote": f.condition_quote}, ensure_ascii=False) for f in claimed)
    try:
        raw = meta.chat([{"role": "user", "content": SUPPORT_PROMPT.format(profile=profile, items=items)}],
                        max_tokens=16000, schema=SUPPORT_SCHEMA)
        rows = {r.get("axis"): r for r in meta.parse_json(raw)["items"] if isinstance(r, dict)}
    except (ValueError, KeyError):
        return  # audit unavailable: keep code-verified verdicts, but say so
    for f in claimed:
        r = rows.get(f.axis)
        if r and r.get("supported") is False:
            f.note = (f.note + "; " if f.note else "") + f"demoted from {f.state.value}: quote does not establish it — {r.get('why', '')}"
            f.state = State.UNREAD
