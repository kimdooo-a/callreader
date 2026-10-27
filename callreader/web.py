"""Minimal web front: paste a call URL or drop a poster/PDF/HWP, get the card back.

python -m callreader.web            # http://127.0.0.1:8765
Standard library only; one reading at a time (each takes a minute or two of model calls).
"""
from __future__ import annotations

import html
import os
import sys
import tempfile
import threading
import uuid
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from . import meta
from .agent import explore
from .axes import Card
from .judge import DEFAULT_PROFILE, judge
from .locate import locate
from .report import CSS, render

FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CallReader</title><style>{css}
.wrap{{max-width:720px;margin:40px auto;padding:0 16px}}label{{display:block;font-weight:600;margin:18px 0 6px}}
input[type=text],textarea{{width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}}
button{{margin-top:18px;padding:10px 18px;border:0;border-radius:8px;background:var(--hl);color:#fff;font-weight:600;cursor:pointer}}
.hint{{color:var(--mut);font-size:13px}}</style></head><body><div class="wrap">
<h1>CallReader</h1><p class="hint">Reads an open call — page, attachments, poster QR codes — and only tells you what it can quote.</p>
<form method="post" action="/read" enctype="multipart/form-data">
<label>Call page URL</label><input type="text" name="url" placeholder="https://…">
<label>…or a poster photo, PDF, HWPX or HWP</label><input type="file" name="file" accept=".jpg,.jpeg,.png,.webp,.pdf,.hwpx,.hwp">
<label>Who is entering</label><textarea name="profile" rows="2">{profile}</textarea>
<button>Read the call</button></form><p class="hint">Takes one to three minutes. Every PASS/FAIL is backed by a quote found verbatim in a source we fetched.</p>
</div></body></html>"""

WAIT = """<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="4"><title>Reading…</title>
<style>{css}.wrap{{max-width:720px;margin:60px auto;padding:0 16px}}</style><div class="wrap"><h1>Reading…</h1>
<p class="hint">{log}</p></div>"""

_jobs: dict[str, dict] = {}
_lock = threading.Lock()  # one reading at a time: rate limits are per team, and the cost tally is global


def _run(job_id: str, target: str, profile: str) -> None:
    job = _jobs[job_id]
    with _lock:
        meta.usage_log.clear()
        meta.sam_images = 0
        try:
            got = explore(target, hops=2, log=lambda m: job["log"].append(m.strip()))
            job["log"].append("judging…")
            card = Card(url=job.get("name") or target, title=got["title"], sources=got["sources"], unread_sources=got["unread"])
            judge(card, profile=profile)
            job["log"].append("placing quotes on the pages…")
            locate(card)
            job["html"] = render(card)
        except Exception as e:  # show the failure instead of a page that never finishes
            job["html"] = f"<!doctype html><meta charset='utf-8'><pre>{html.escape(repr(e))}</pre>"
        finally:
            if job.get("tmp"):
                os.unlink(job["tmp"])


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, status: int = 200, headers: dict | None = None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/job/"):
            job = _jobs.get(self.path[5:])
            if not job:
                return self._send("not found", 404)
            if "html" in job:
                return self._send(job["html"])
            return self._send(WAIT.format(css=CSS, log="<br>".join(html.escape(l) for l in job["log"][-12:]) or "starting…"))
        self._send(FORM.format(css=CSS, profile=html.escape(DEFAULT_PROFILE)))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 40 * 1024 * 1024:
            return self._send("file too large (40 MB max)", 413)
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        fields, upload = {}, None
        if ctype.startswith("multipart/form-data"):
            msg = BytesParser(policy=default).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if part.get_filename():
                    data = part.get_payload(decode=True)
                    if data:
                        upload = (part.get_filename(), data)
                else:
                    fields[name] = part.get_content().strip()
        else:
            fields = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        target, tmp = fields.get("url", ""), None
        if upload:
            suffix = os.path.splitext(upload[0])[1][:6]
            fd, tmp = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as fh:
                fh.write(upload[1])
            target = tmp
        if not target.startswith(("http://", "https://")) and not tmp:
            return self._send(FORM.format(css=CSS, profile=html.escape(DEFAULT_PROFILE)), 400)
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"log": [], "tmp": tmp, "name": os.path.basename(upload[0]) if upload else None}
        threading.Thread(target=_run, args=(job_id, target, fields.get("profile") or DEFAULT_PROFILE), daemon=True).start()
        self._send("", 303, {"Location": f"/job/{job_id}"})

    def log_message(self, *args):
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"CallReader on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
