"""CLI: python -m callreader <url> [--json]"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import meta
from .axes import Card, State
from .fetch import fetch_call
from .judge import judge

ICON = {State.PASS: "PASS  ", State.FAIL: "FAIL  ", State.UNREAD: "UNREAD"}


def main() -> int:
    ap = argparse.ArgumentParser(prog="callreader")
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    got = fetch_call(args.url)
    card = Card(url=args.url, title=got["title"], sources=got["sources"], unread_sources=got["unread"])
    judge(card)

    if args.json:
        out = asdict(card)
        for s in out["sources"]:
            s.pop("bytes", None)
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
            print(f"           condition: {f.condition}")
        if f.note:
            print(f"           note: {f.note}")
    for u in card.unread_sources:
        print(f"[UNREAD] attachment {u['url']}\n           {u['reason']}")
    print(f"\nOVERALL: {card.verdict.value}   (model cost ${meta.cost_usd():.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
