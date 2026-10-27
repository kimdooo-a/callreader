"""Citation verifier: a verdict survives only if its quote exists in the source text.

The model may paraphrase, translate, or invent a quote. None of those count.
A quote is accepted only when, after whitespace/punctuation normalization, it is
a substring of the document it claims to come from. Anything else is demoted to
UNREAD — not FAIL, not PASS — because "we could not verify" is a different fact
from "the rule is absent".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_QUOTE_CHARS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "「": '"', "」": '"', "『": '"', "』": '"',
    "–": "-", "—": "-", " ": " ",
}


def normalize(text: str) -> str:
    """Fold forms that differ between PDF extraction and model output."""
    text = unicodedata.normalize("NFKC", text)
    for a, b in _QUOTE_CHARS.items():
        text = text.replace(a, b)
    # PDF text often breaks lines mid-sentence and hyphenates words.
    # A line-end hyphen may be a real hyphen ("award-\nwinning") or a word split
    # ("con-\ntest"); dropping every hyphen makes both forms compare equal.
    text = re.sub(r"-\s*\n\s*", "-", text).replace("-", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _weight(s: str) -> int:
    """CJK characters carry about a word each; count them double so a 10-character Japanese clause is not 'too short'."""
    return sum(2 if ord(ch) >= 0x2E80 else 1 for ch in s)


@dataclass
class QuoteCheck:
    found: bool
    source_id: str | None = None
    page: int | None = None
    reason: str = ""


def check_quote(quote: str, sources: list[dict], min_len: int = 12) -> QuoteCheck:
    """Look for `quote` in any source. Each source: {id, pages: [str, ...]}.

    Returns where it was found, or why it was rejected.
    """
    q = normalize(quote or "")
    # Strip ellipses the model adds when it trims a sentence; each fragment must match.
    fragments = [f.strip(" .") for f in re.split(r"\.\.\.|…", q) if f.strip(" .")]
    if not fragments:
        return QuoteCheck(False, reason="empty quote")
    if sum(_weight(f) for f in fragments) < min_len:
        return QuoteCheck(False, reason=f"quote shorter than {min_len} chars — too weak to prove anything")
    for src in sources:
        for i, page in enumerate(src.get("pages", [])):
            p = normalize(page)
            if all(f in p for f in fragments):
                return QuoteCheck(True, source_id=src["id"], page=i + 1)
    return QuoteCheck(False, reason="quote not found verbatim in any fetched source")
