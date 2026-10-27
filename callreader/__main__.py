"""CLI: python -m callreader <url> [--json]"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import meta
from .agent import explore
from .axes import Card, State
from .judge import DEFAULT_PROFILE, judge
from .locate import locate
from .report import render

ICON = {State.PASS: "PASS  ", State.FAIL: "FAIL  ", State.UNREAD: "UNREAD"}


def main() -> int:
    ap = argparse.ArgumentParser(prog="callreader")
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE,
                    help="who is entering — eligibility is judged against this (age, nationality, residence, team)")
    ap.add_argument("--html", metavar="FILE", help="also write a self-contained report with quotes highlighted on the pages")
    ap.add_argument("--hops", type=int, default=2, help="how many link hops the agent may follow (0 = page + attachments only)")
    args = ap.parse_args()

    got = explore(args.url, hops=args.hops, log=lambda m: print(m, file=sys.stderr))
    card = Card(url=args.url, title=got["title"], sources=got["sources"], unread_sources=got["unread"])
    judge(card, profile=args.profile)
    if args.html:
        locate(card)
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render(card))
        print(f"report: {args.html}", file=sys.stderr)

    if args.json:
        out = asdict(card)
        for s in out["sources"]:
            s.pop("bytes", None)
            s.pop("images", None)
        out["verdict"] = card.verdict.value
        out["cost_usd"] = round(meta.cost_usd(), 4)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"\n{card.title or card.url}\n{'=' * 60}")
    for f in card.findings:
        print(f"[{ICON[f.state]}] {f.axis:<11} {f.answer}")
        if f.quote and f.state != State.UNREAD:
            print(f"           “{f.quote}”  — {f.source_id} p.{f.page}")
        if f.condition:
            tag = "condition" if f.condition_verified else "condition (UNVERIFIED — no quote found)"
            print(f"           {tag}: {f.condition}")
            if f.condition_verified:
                print(f"             “{f.condition_quote}”")
        if f.note:
            print(f"           note: {f.note}")
    for u in card.unread_sources:
        print(f"[UNREAD] attachment {u['url']}\n           {u['reason']}")
    print(f"\nOVERALL: {card.verdict.value}   (model cost ${meta.cost_usd():.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
