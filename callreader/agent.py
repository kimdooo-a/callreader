"""Explore loop: Muse Spark decides which linked pages hold the rules, code fetches them.

A call page is often a teaser. The binding text lives behind "Official rules",
"Full GenAI policy", "FAQ", "Terms". The agent reads the page, picks links,
we fetch them, and it can go one more hop. Every page it asked for but we could
not read is carried forward as UNREAD — the judge must not treat it as absent.
"""
from __future__ import annotations

from . import meta
from .fetch import all_links, fetch_call, fetch_linked_page

PICK_PROMPT = """You are helping decide whether an entrant can enter an open call.
Goal: find every page that may contain binding rules — official rules, terms and conditions,
eligibility, FAQ, AI/GenAI policy, submission guidelines, prizes, or deadlines.

Text already read (truncated):
---
{text}
---

Candidate links (label | url):
{links}

Already fetched: {done}

Pick at most {k} links that most likely contain binding rules NOT already covered.
Skip social media, login, donation, newsletter, and unrelated navigation.
Return ONLY a JSON array of URLs (copy them exactly), or [] if nothing more is needed."""


def explore(url: str, hops: int = 2, per_hop: int = 4, log=print) -> dict:
    got = fetch_call(url)
    sources, unread = got["sources"], got["unread"]
    session = got["session"]
    frontier = [(url, got["html"])]
    done = {url} | {s["url"] for s in sources}
    n = 0
    for hop in range(hops):
        next_frontier = []
        for base, html in frontier:
            links = [l for l in all_links(html, base) if l["url"] not in done]
            if not links:
                continue
            text = "\n".join(p for s in sources for p in s["pages"])[:12000]
            raw = meta.chat([{"role": "user", "content": PICK_PROMPT.format(
                text=text, links="\n".join(f"{l['label']} | {l['url']}" for l in links),
                done=", ".join(sorted(done))[:2000], k=per_hop)}], max_tokens=4000)
            try:
                picks = [u for u in meta.parse_json(raw) if isinstance(u, str)]
            except ValueError:
                picks = []
            allowed = {l["url"] for l in links}
            for u in picks[:per_hop]:
                if u not in allowed or u in done:
                    continue  # the model may only follow links that exist on the page
                done.add(u)
                n += 1
                res = fetch_linked_page(session, u, base, f"link{n}")
                if "source" in res:
                    sources.append(res["source"])
                    log(f"  hop {hop + 1}: read  {u}")
                    if "html" in res:
                        next_frontier.append((u, res["html"]))
                else:
                    unread.append(res["unread"])
                    log(f"  hop {hop + 1}: UNREAD {u} — {res['unread']['reason']}")
        frontier = next_frontier
        if not frontier:
            break
    return {"title": got["title"], "sources": sources, "unread": unread}
