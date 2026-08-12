#!/usr/bin/env python3
"""Refresh the REDSTONE_COST block in dashboard.html from Red Stone's invoices.

The dashboard puts two cost figures side by side: what the monday.com board
says a job cost, and what Red Stone actually billed for it. sync_monday_cost.py
keeps the first honest. This keeps the second.

Red Stone publishes no API. The figures come the same way a person would get
them: log in, open each job, download the invoice attached to it, and read the
total off the bottom. The invoices are real PDFs with a text layer, so nothing
here does OCR or guesswork -- a total is either parsed exactly or the job is
reported as unresolved and left alone.

Scope follows the file. Only jobs already listed in REDSTONE_JOBS are fetched,
so this covers the May-onward campaign the dashboard reports on and does not
wander into the 39 earlier jobs Red Stone still holds.

Invoices are cached under .cache/redstone/ (gitignored) and re-used, so a
second run costs one login and one orders page rather than 30-odd downloads.
--refresh forces a re-fetch when an invoice has been reissued.

Usage:
    REDSTONE_USER=... REDSTONE_PASS=... python3 scripts/sync_redstone_cost.py
        [--check] [--refresh] [--job ID ...]

    --check    report drift and exit 1 without writing. Suitable for CI.
    --refresh  ignore the cache and re-download every invoice.
    --job      restrict to specific job ids (repeatable).

The password may carry leading or trailing whitespace that config forms like to
strip, so REDSTONE_PASS_B64 is accepted as a base64 alternative and preferred
when both are set.

Nothing here writes a credential into dashboard.html or anywhere else on disk.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
import subprocess
import sys
import zlib
from pathlib import Path

BASE = "https://redstonemail.com"
LOGIN = f"{BASE}/users/login"
ORDERS = f"{BASE}/orders"

BEGIN = "/* BEGIN GENERATED REDSTONE_COST */"
END = "/* END GENERATED REDSTONE_COST */"

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "dashboard.html"
CACHE = REPO / ".cache" / "redstone"

# Total sits after the administrative fee on every invoice seen so far.
RE_TOTAL = re.compile(r"Administrative Fee\s*3\.50%[\d,.]+\$([\d,]+\.\d{2})")
RE_JOBHASH = re.compile(r'data-jobid="(\d+)".{0,400}?orders/view/([0-9a-f]{20,})', re.S)
RE_INVOICE = re.compile(r'(https://redstonemail\.com/orders/getFile/[0-9a-f]+/[^"]*INV_\d+\.pdf)')


class SyncError(RuntimeError):
    pass


def curl(args: list[str], binary: bool = False):
    proc = subprocess.run(["curl", "-s", "--max-time", "90", *args],
                          capture_output=True)
    if proc.returncode != 0:
        raise SyncError(f"curl failed ({proc.returncode}): {proc.stderr.decode()[:200]}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def password() -> str:
    """REDSTONE_PASS_B64 wins: base64 survives forms that trim whitespace, and
    this password genuinely starts with a space."""
    b64 = os.environ.get("REDSTONE_PASS_B64", "").strip()
    if b64:
        try:
            return base64.b64decode(b64, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise SyncError(f"REDSTONE_PASS_B64 is not valid base64 text: {exc}") from exc
    raw = os.environ.get("REDSTONE_PASS")
    if raw is None:
        raise SyncError("set REDSTONE_PASS or REDSTONE_PASS_B64")
    return raw


def login(jar: Path) -> None:
    user = os.environ.get("REDSTONE_USER")
    if not user:
        raise SyncError("set REDSTONE_USER")
    jar.unlink(missing_ok=True)
    page = curl(["-c", str(jar), LOGIN])
    token = re.search(r'name="data\[User\]\[fu\]" value="([a-f0-9]+)"', page)
    if not token:
        raise SyncError("login form has no fu token; the page may have changed")
    out = curl(["-b", str(jar), "-c", str(jar), "-o", "/dev/null",
                "-w", "%{http_code}|%{redirect_url}", "-X", "POST", LOGIN,
                "--data-urlencode", "_method=POST",
                "--data-urlencode", f"data[User][username]={user}",
                "--data-urlencode", f"data[User][password]={password()}",
                "--data-urlencode", f"data[User][fu]={token.group(1)}"])
    code = out.split("|", 1)[0]
    if code != "302":
        raise SyncError(
            "login rejected (HTTP " + code + "). Check REDSTONE_USER, and note the "
            "password's leading space -- use REDSTONE_PASS_B64 if a form trimmed it.")


def job_hashes(jar: Path, wanted: set[str]) -> dict[str, str]:
    """Walk the orders list until every wanted job is located."""
    found: dict[str, str] = {}
    for page in range(1, 6):
        url = ORDERS if page == 1 else f"{BASE}/orders/index/page:{page}"
        html = curl(["-b", str(jar), url]).replace("\n", " ")
        if "users/login" in html[:400]:
            raise SyncError("session expired while paging the orders list")
        for job, digest in RE_JOBHASH.findall(html):
            if job in wanted:
                found.setdefault(job, digest)
        if wanted <= set(found):
            break
    return found


def invoice_pdf(jar: Path, job: str, digest: str, refresh: bool) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{job}.pdf"
    if cached.exists() and not refresh:
        return cached.read_bytes()
    page = curl(["-b", str(jar), f"{BASE}/orders/view/{digest}"]).replace("\n", " ")
    link = RE_INVOICE.search(page)
    if not link:
        raise SyncError("no C_invoice PDF attached to this job")
    body = curl(["-b", str(jar), link.group(1)], binary=True)
    if not body.startswith(b"%PDF"):
        raise SyncError("invoice download was not a PDF (session may have expired)")
    cached.write_bytes(body)
    return body


def pdf_text(raw: bytes) -> str:
    """Text out of the invoice. The strings are UTF-16BE hex inside compressed
    content streams -- there is a real text layer, so no OCR is involved."""
    out: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            body = zlib.decompress(match.group(1))
        except zlib.error:
            continue
        if b"TJ" not in body and b"Tj" not in body:
            continue
        for tok in re.finditer(rb"<([0-9A-Fa-f\s]+)>", body):
            hexes = re.sub(rb"\s", b"", tok.group(1))
            if len(hexes) % 2:
                hexes += b"0"
            try:
                out.append(binascii.unhexlify(hexes).decode("utf-16-be", "replace"))
            except binascii.Error:
                pass
    return "".join(out)


def invoice_total(raw: bytes, job: str) -> float:
    text = pdf_text(raw)
    if job not in text:
        raise SyncError(f"invoice does not mention job {job}; wrong file?")
    hits = RE_TOTAL.findall(text)
    if not hits:
        raise SyncError("no 'Administrative Fee 3.50% ... $total' line found")
    if len({h for h in hits}) > 1:
        raise SyncError(f"invoice has conflicting totals {sorted(set(hits))}")
    return round(float(hits[0].replace(",", "")), 2)


def jobs_in_dashboard(text: str) -> dict[str, float]:
    """job id -> the cost currently written in REDSTONE_JOBS."""
    return {m.group(1): float(m.group(2))
            for m in re.finditer(r'job:"(\d+)".*?cost:([\d.]+)', text)}


def existing(text: str) -> dict[str, float]:
    block = re.search(re.escape(BEGIN) + r"(.*?)" + re.escape(END), text, re.S)
    if not block:
        return {}
    return {m.group(1): float(m.group(2))
            for m in re.finditer(r'"(\d+)"\s*:\s*([\d.]+)', block.group(1))}


def render(costs: dict[str, float]) -> str:
    items = [f'"{job}":{cost:>8.2f}' for job, cost in sorted(costs.items())]
    lines = ["const REDSTONE_COST = {"]
    for i in range(0, len(items), 4):
        lines.append("  " + ", ".join(items[i:i + 4]) + ("," if i + 4 < len(items) else ""))
    lines.append("};")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--job", action="append", default=[])
    args = ap.parse_args()

    text = TARGET.read_text()
    listed = jobs_in_dashboard(text)
    if not listed:
        print("error: no REDSTONE_JOBS entries found in dashboard.html", file=sys.stderr)
        return 2
    wanted = set(args.job) if args.job else set(listed)
    unknown = wanted - set(listed)
    if unknown:
        print(f"error: not in REDSTONE_JOBS: {sorted(unknown)}", file=sys.stderr)
        return 2

    jar = CACHE.parent / "redstone.cookies"
    jar.parent.mkdir(parents=True, exist_ok=True)
    try:
        login(jar)
        print(f"signed in as {os.environ['REDSTONE_USER']}")
        hashes = job_hashes(jar, wanted)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    missing = sorted(wanted - set(hashes))
    costs: dict[str, float] = {}
    failed: list[str] = []
    for job in sorted(wanted):
        if job not in hashes:
            continue
        try:
            costs[job] = invoice_total(invoice_pdf(jar, job, hashes[job], args.refresh), job)
        except SyncError as exc:
            failed.append(f"  {job}: {exc}")

    print(f"{len(costs)} of {len(wanted)} jobs resolved to an invoice total")
    if missing:
        print(f"  not found on the orders list: {', '.join(missing)}")
    for line in failed:
        print(line)
    if not costs:
        print("error: nothing resolved; refusing to write", file=sys.stderr)
        return 2

    before = existing(text)
    changed = sorted(j for j in set(costs) & set(before) if abs(costs[j] - before[j]) > 0.005)
    added = sorted(set(costs) - set(before))
    for job in changed:
        print(f"  changed {job}  {before[job]:>10,.2f} -> {costs[job]:>10,.2f}")
    for job in added:
        print(f"  added   {job}  {costs[job]:>10,.2f}")

    # What the hand-written REDSTONE_JOBS cost says, for comparison only.
    drift = sorted(j for j in costs if abs(costs[j] - listed[j]) > 0.005)
    if drift:
        print("\ninvoice totals that differ from the cost hand-written in REDSTONE_JOBS:")
        for job in drift:
            print(f"  {job}  file {listed[job]:>10,.2f}  invoice {costs[job]:>10,.2f}")

    if not (changed or added):
        print("  invoices and dashboard agree; nothing to write")
        return 0
    if args.check:
        print("\n--check: dashboard is out of date")
        return 1

    if BEGIN in text:
        updated = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END),
                         BEGIN + "\n" + render(costs) + "\n" + END,
                         text, count=1, flags=re.S)
    else:
        anchor = "const MAILED_JOBS   = REDSTONE_JOBS.filter"
        if anchor not in text:
            print("error: cannot find an anchor to insert the block", file=sys.stderr)
            return 2
        updated = text.replace(
            anchor,
            "/* Invoiced total per job, read off the PDF attached to that job in the\n"
            "   Red Stone portal. GENERATED - run scripts/sync_redstone_cost.py.\n"
            "   Do not hand-edit between the markers. */\n"
            + BEGIN + "\n" + render(costs) + "\n" + END + "\n" + anchor, 1)

    if jobs_in_dashboard(updated) != listed:
        print("error: refusing to write - REDSTONE_JOBS changed", file=sys.stderr)
        return 3

    TARGET.write_text(updated)
    print(f"\nwrote {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
