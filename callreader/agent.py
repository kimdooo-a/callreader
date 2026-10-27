"""Explore loop: Muse Spark decides which linked pages hold the rules, code fetches them.

A call page is often a teaser. The binding text lives behind "Official rules",
"Full GenAI policy", "FAQ", "Terms". The agent reads the page, picks links,
we fetch them, and it can go one more hop. Every page it asked for but we could
not read is carried forward as UNREAD — the judge must not treat it as absent.
"""
from __future__ import annotations

import os

import requests

from . import meta
from .fetch import UA, all_links, fetch_call, fetch_linked_page, source_from_bytes
from .segment import find_qr_links

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
Skip social media, login, donation, newsletter, board/list index pages of other posts, and unrelated navigation.
Return JSON {{"urls": [...]}} with the URLs copied exactly, or {{"urls": []}} if nothing more is needed."""


def _start(target: str) -> dict:
    """A URL is fetched with its attachments; a local file (poster photo, PDF, HWP) becomes the first source."""
    if os.path.isfile(target):
        data = open(target, "rb").read()
        got = source_from_bytes(data, os.path.abspath(target), "file")
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        sources = [got["source"]] if "source" in got else []
        unread = [got["unread"]] if "unread" in got else []
        return {"title": os.path.basename(target), "sources": sources, "unread": unread, "html": None, "session": s, "from_file": True}
    return fetch_call(target)


def follow_qr_codes(got: dict, done: set, log=print) -> list[tuple[str, str]]:
    """SAM 3.1 finds QR codes on every poster; each decoded link is a page we must read.
    Returns new (url, html) pairs for the link-exploring hops."""
    frontier = []
    for src in [s for s in got["sources"] if s["kind"] == "image" and s.get("bytes")]:
        for n, qr in enumerate(find_qr_links(src["bytes"], src.get("mime", "image/jpeg")), 1):
            src.setdefault("qr", []).append(qr)
            if not qr["url"]:
                got["unread"].append({"url": f"{src['url']}#qr{n}", "reason": qr["reason"]})
                log(f"  qr: UNREAD on {src['id']} — {qr['reason']}")
                continue
            if qr["url"] in done or not qr["url"].startswith(("http://", "https://")):
                continue
            done.add(qr["url"])
            log(f"  qr: {src['id']} box {qr['box']['x1']},{qr['box']['y1']} -> {qr['url']}")
            try:
                sub = fetch_call(qr["url"], session=got["session"], prefix=f"{src['id']}.qr{n}.")
            except requests.RequestException as e:
                got["unread"].append({"url": qr["url"], "reason": f"QR link could not be fetched: {e.__class__.__name__}"})
                continue
            for s2 in sub["sources"]:
                if s2["url"] not in done:
                    done.add(s2["url"])
                    got["sources"].append(s2)
            got["unread"].extend(sub["unread"])
            frontier.append((qr["url"], sub["html"]))
            if got.get("from_file") and sub["title"]:
                got["title"] = sub["title"]  # the page behind the QR names the call better than a file name
    return frontier


def explore(url: str, hops: int = 2, per_hop: int = 4, log=print) -> dict:
    got = _start(url)
    sources, unread = got["sources"], got["unread"]
    session = got["session"]
    done = {url} | {s["url"] for s in sources}
    frontier = [(url, got["html"])] if got["html"] else []
    frontier += follow_qr_codes(got, done, log)
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
                done=", ".join(sorted(done))[:2000], k=per_hop)}], max_tokens=8000,
                schema=meta.obj({"urls": {"type": "array", "items": {"type": "string"}}}))
            try:
                picks = [u for u in meta.parse_json(raw)["urls"] if isinstance(u, str)]
            except (ValueError, KeyError):
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
