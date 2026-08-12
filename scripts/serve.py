#!/usr/bin/env python3
"""Local helper that lets the dashboard refresh Red Stone costs on demand.

A browser page cannot sign into Red Stone itself. Red Stone sends no CORS
headers, so the browser refuses to hand the page any response from it, and a
page has no way to run a program or write a cache file besides. This is the
piece that does have those abilities: a small server on your own machine that
the page can ask.

    dashboard.html  ->  this helper  ->  redstonemail.com

Run it, open the printed address, and the page gains a "Refresh Redstone"
control. Everything else about the dashboard is unchanged, and the file it
serves is the same one that works offline.

    python3 scripts/serve.py [--port 8787] [--no-browser]

Credentials never reach the browser. They come from REDSTONE_USER and
REDSTONE_PASS / REDSTONE_PASS_B64, or are posted once to this helper and kept
here. With "remember" they are written to .cache/credentials.json, owner-read
only and gitignored -- on your machine, not in a browser profile, and not in
the page.

Refreshes are incremental. Every invoice already downloaded is reused, so a
refresh costs one login plus one download per genuinely new job. Jobs are
discovered from the Red Stone portal rather than from dashboard.html, so a job
Red Stone has run shows up without anyone adding it to a file first.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_redstone_cost as rs                                    # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "dashboard.html"
CACHE = REPO / ".cache"
CREDS = CACHE / "credentials.json"

# The dashboard reports the May-onward campaign. Red Stone still holds 39
# earlier jobs; discovering those would quietly widen what the page claims to
# cover, so they stay out until someone asks for them.
FROM_DATE = date(2026, 5, 1)

EXPORT = (rs.BASE + "/orders/exportCSV_old/?company.7633%5B%24nin%5D%5B1%5D="
          "&company.7633%5B%24nin%5D%5B2%5D=0&cur_status%5B%24nin%5D%5B0%5D=Inactive"
          "&TYPE=CLIENT")

_lock = threading.Lock()          # one refresh at a time; they share a cookie jar


def stored_credentials() -> dict:
    """Environment wins over the remembered file, so an explicit run can
    override without editing anything."""
    user = os.environ.get("REDSTONE_USER")
    pw = os.environ.get("REDSTONE_PASS_B64") or os.environ.get("REDSTONE_PASS")
    if user and pw:
        return {"user": user, "source": "environment"}
    if CREDS.exists():
        try:
            return {"user": json.loads(CREDS.read_text())["user"], "source": "remembered"}
        except (json.JSONDecodeError, KeyError, OSError):
            return {}
    return {}


KEYS = ("REDSTONE_USER", "REDSTONE_PASS", "REDSTONE_PASS_B64")


def apply_credentials(user: str | None, password: str | None):
    """Stage credentials where sync_redstone_cost looks for them, and hand back
    a rollback.

    Staging has to be undoable. A typo posted once would otherwise overwrite a
    working environment for the life of the process, so every later refresh
    fails too and the only cure is restarting the helper. Nothing is committed
    or remembered until a login has actually succeeded."""
    prev = {k: os.environ.get(k) for k in KEYS}

    def rollback():
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    if user and password:
        os.environ["REDSTONE_USER"] = user
        os.environ["REDSTONE_PASS"] = password
        os.environ.pop("REDSTONE_PASS_B64", None)
        return rollback
    if not (os.environ.get("REDSTONE_USER")
            and (os.environ.get("REDSTONE_PASS") or os.environ.get("REDSTONE_PASS_B64"))):
        if CREDS.exists():
            try:
                saved = json.loads(CREDS.read_text())
                os.environ["REDSTONE_USER"] = saved["user"]
                os.environ["REDSTONE_PASS"] = saved["password"]
                os.environ.pop("REDSTONE_PASS_B64", None)
            except (json.JSONDecodeError, KeyError, OSError):
                pass
    return rollback


def remember_credentials(user: str, password: str) -> None:
    """Only ever called after a login has succeeded."""
    CACHE.mkdir(parents=True, exist_ok=True)
    CREDS.write_text(json.dumps({"user": user, "password": password}))
    CREDS.chmod(0o600)


def discover(jar: Path) -> dict[str, str]:
    """job id -> ISO date, for every Red Stone job from FROM_DATE onward.

    Read from the portal's own export rather than from dashboard.html, so a new
    job appears here the moment Red Stone has it."""
    body = rs.curl(["-b", str(jar), EXPORT])
    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows:
        raise rs.SyncError("the orders export came back empty")
    out = {}
    for r in rows:
        created = (r.get("export_created") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", created):
            continue
        if date.fromisoformat(created) >= FROM_DATE:
            out[r["job_id"]] = created
    return out


def refresh(force: bool) -> dict:
    jar = CACHE / "redstone.cookies"
    CACHE.mkdir(parents=True, exist_ok=True)
    rs.login(jar)
    jobs = discover(jar)
    hashes = rs.job_hashes(jar, set(jobs))

    costs, fetched, reused, failed = {}, [], [], []
    for job in sorted(jobs):
        cached = (CACHE / "redstone" / f"{job}.pdf").exists()
        if job not in hashes:
            failed.append({"job": job, "why": "not on the orders list"})
            continue
        try:
            pdf = rs.invoice_pdf(jar, job, hashes[job], force)
            costs[job] = rs.invoice_total(pdf, job)
            (reused if cached and not force else fetched).append(job)
        except rs.SyncError as exc:
            failed.append({"job": job, "why": str(exc)})
    return {"costs": costs, "fetched": fetched, "reused": reused,
            "failed": failed, "from": FROM_DATE.isoformat()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):                      # one line per request
        sys.stderr.write("  %s %s\n" % (self.command, self.path.split("?")[0]))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Lets a dashboard.html opened straight off disk talk to this helper.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/dashboard.html"):
            if not PAGE.exists():
                return self._json(404, {"error": "dashboard.html is missing"})
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            cached = sorted(p.stem for p in (CACHE / "redstone").glob("*.pdf"))
            return self._json(200, {"ok": True, "helper": True,
                                    "credentials": stored_credentials(),
                                    "cachedInvoices": cached,
                                    "from": FROM_DATE.isoformat()})
        self._json(404, {"error": "no such endpoint"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/redstone/refresh":
            return self._json(404, {"error": "no such endpoint"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "body must be JSON"})

        if not _lock.acquire(blocking=False):
            return self._json(409, {"error": "a refresh is already running"})
        try:
            user, password = body.get("user"), body.get("password")
            rollback = apply_credentials(user, password)
            if not os.environ.get("REDSTONE_USER"):
                rollback()
                return self._json(401, {"error": "no credentials",
                                        "needCredentials": True})
            try:
                result = refresh(bool(body.get("force")))
            except rs.SyncError:
                rollback()            # a bad try leaves nothing behind
                raise
            if user and password and body.get("remember"):
                remember_credentials(user, password)
            result["ok"] = True
            return self._json(200, result)
        except rs.SyncError as exc:
            return self._json(502, {"error": str(exc),
                                    "needCredentials": "login rejected" in str(exc)})
        except Exception as exc:                          # never take the helper down
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            _lock.release()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not PAGE.exists():
        print(f"error: {PAGE} not found", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{args.port}/"
    creds = stored_credentials()
    print(f"dashboard  {url}")
    print(f"credentials {creds.get('source', 'not set - the page will ask')}")
    print(f"invoices   {len(list((CACHE / 'redstone').glob('*.pdf')))} cached, "
          f"jobs from {FROM_DATE.isoformat()}")
    print("ctrl-c to stop\n")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
