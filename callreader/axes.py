"""The axes a reader must settle before anyone spends a week on an entry.

Order matters: most calls die on eligibility, not on prize size, so the
card is read top-down and prize comes last.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    PASS = "PASS"      # rule read, entrant can proceed on this axis
    FAIL = "FAIL"      # rule read, it blocks this entrant
    UNREAD = "UNREAD"  # not verified — never folded into PASS


@dataclass(frozen=True)
class Axis:
    key: str
    question: str
    fail_means: str


AXES: list[Axis] = [
    Axis("eligibility", "Who may enter? Any nationality, residency, age, student-only or affiliation limits?",
         "the entrant described in the profile is excluded"),
    Axis("fee", "Is there an entry or submission fee?", "a fee is required"),
    Axis("cash", "Is the prize paid in cash, or only credits/vouchers/goods?", "no cash prize"),
    Axis("onsite", "Is physical attendance, a device, a team, or a local partner required?",
         "the entrant cannot satisfy an on-site/team/partner requirement"),
    Axis("ai_use", "What does the call say about using generative AI to make the entry?",
         "AI-made entries are banned or disqualified"),
    Axis("authorship", "Must the entry be the entrant's own work, and who owns the rights afterwards?",
         "authorship or rights terms the entrant cannot meet"),
    Axis("deadline", "What is the exact submission deadline, with time zone?", "the deadline has passed"),
]


@dataclass
class Finding:
    axis: str
    state: State
    answer: str = ""
    quote: str = ""
    condition: str = ""          # the "unless / only if" clause that governs the verdict
    condition_quote: str = ""    # verbatim source text for the condition
    condition_verified: bool | None = None  # None = no condition; False = the model asserted it without a quote we found
    source_id: str | None = None
    page: int | None = None
    note: str = ""               # why it was demoted, if it was
    model_state: str | None = None  # what the model claimed before verification


@dataclass
class Card:
    url: str
    title: str = ""
    findings: list[Finding] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # [{id, kind, url, pages}]
    unread_sources: list[dict] = field(default_factory=list)  # attachments we could not open

    @property
    def verdict(self) -> State:
        states = [f.state for f in self.findings]
        if State.FAIL in states:
            return State.FAIL
        if State.UNREAD in states or len(states) < len(AXES):
            return State.UNREAD
        return State.PASS
