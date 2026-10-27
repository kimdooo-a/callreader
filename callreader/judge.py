"""Read a call with Muse Spark, then let code decide which verdicts survive."""
from __future__ import annotations

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
4. If the rule has a condition ("unless", "only if", "except", "단,", "ただし"), put that clause in "condition".
   A verdict without its governing condition is wrong.
5. "answer" is a one-line plain-English summary for the entrant.

Axes:
{axes}

6. For the deadline axis also return "deadline_iso": the deadline as ISO 8601 with UTC offset
   (e.g. "2026-11-30T23:59:00-05:00"); if no time zone is stated write the date only. Do not judge whether it
   has passed — code compares it with the current time ({now}).

Return ONLY a JSON array: [{{"axis": key, "verdict": ..., "answer": ..., "quote": ..., "source": id, "page": n, "condition": ..., "deadline_iso": ...}}]

SOURCES:
{sources}
"""

DEFAULT_PROFILE = "An individual (solo, not a student) living in South Korea, entering online, who uses generative AI tools."


def ocr_images(card_sources: list[dict]) -> None:
    """Image sources (posters) and scanned PDFs have no text layer: transcribe them with vision."""
    for src in card_sources:
        if src["kind"] in ("image",) and src.get("bytes"):
            text = meta.chat([{"role": "user", "content": [
                {"type": "text", "text": TRANSCRIBE_PROMPT},
                meta.image_part(src["bytes"], src.get("mime", "image/png")),
            ]}])
            src["pages"] = ["" if text.strip() == "EMPTY" else text]
            src["kind"] = "image-ocr"


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
        raw = meta.chat([{"role": "user", "content": prompt}], max_tokens=32000)
        try:
            items = meta.parse_json(raw)
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
                    condition=it.get("condition", "") or "", model_state=claimed)
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
    return card
