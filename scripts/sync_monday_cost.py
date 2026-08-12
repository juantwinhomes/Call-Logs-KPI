#!/usr/bin/env python3
"""Refresh the board-sourced figures in dashboard.html from the live monday.com board.

The dashboard shows two cost figures side by side for each mail format: what the
monday.com board carries, and what Red Stone actually invoiced. The board half
used to be transcribed by hand, which is how it drifted. This script reads it
straight off the board instead.

Two things get synced, and nothing else:

  MONDAY_COST   rewritten wholesale between its BEGIN/END markers.
  mail + status on each REDSTONE_JOBS entry, matched by job id.

A job's Mail Date decides which month its cost lands in, so a blank one hides
real spend: job 291483 sat outside every mailed total until someone filled in
its date on the board. status is derived, not read -- an item sitting in the
Mailed / Delivered group is mailed by definition, so there is no status column
to disagree with.

Deliberately NOT synced: qty and cost on REDSTONE_JOBS. cost there is the total
on the job's Red Stone invoice, which is a different number from the board's
Total Cost and the whole point of showing both. Piece counts are hand-maintained
too. The script asserts it left both untouched before it writes.

Usage:
    MONDAY_API_KEY=... python3 scripts/sync_monday_cost.py [--check] [--board ID]

    --check   report what would change and exit non-zero if anything would,
              without writing. Suitable for CI.

Columns and the target group are resolved by their human titles rather than by
monday's generated ids, so renaming a column in the UI is what breaks this --
not monday reshuffling ids underneath us.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

API = "https://api.monday.com/v2"
API_VERSION = "2024-10"

DEFAULT_BOARD = 18392647066

# Group and columns are matched on these, case-insensitively, as substrings.
GROUP_MATCH = "mailed"
COL_JOB_ID = "redstone job id"
COL_COST = "total cost"
COL_MAIL = "mail date"

BEGIN = "/* BEGIN GENERATED MONDAY_COST */"
END = "/* END GENERATED MONDAY_COST */"

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "dashboard.html"


class SyncError(RuntimeError):
    pass


def query(gql: str, token: str) -> dict:
    """POST a GraphQL query. Uses curl so the sandbox's proxy and CA bundle
    configuration applies without this script having to know about either."""
    proc = subprocess.run(
        [
            "curl", "-s", "--max-time", "60", API,
            "-H", f"Authorization: {token}",
            "-H", "Content-Type: application/json",
            "-H", f"API-Version: {API_VERSION}",
            "-d", json.dumps({"query": gql}),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SyncError(f"curl failed ({proc.returncode}): {proc.stderr.strip()}")
    if not proc.stdout.strip():
        raise SyncError("empty response from monday.com")
    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(f"response was not JSON: {exc}: {proc.stdout[:200]}") from exc
    if body.get("errors"):
        raise SyncError(f"monday.com returned errors: {body['errors']}")
    return body["data"]


def pick(titles: dict[str, str], want: str, what: str) -> str:
    """Resolve one column id from its title. Ambiguity is an error, not a guess.

    Exact title wins outright, so "Mail Date" is not confused with "Next Mail
    Date" -- picking the wrong one there would silently file every job's cost
    into the month after the one it was mailed in."""
    exact = [cid for cid, title in titles.items() if title.strip().lower() == want]
    if len(exact) == 1:
        return exact[0]
    hits = [cid for cid, title in titles.items() if want in title.lower()]
    if not hits:
        raise SyncError(
            f"no column titled like {want!r} on this board; saw: "
            + ", ".join(sorted(titles.values()))
        )
    if len(hits) > 1:
        raise SyncError(f"{what} is ambiguous: {hits} all match {want!r}")
    return hits[0]


def as_money(raw: str) -> float:
    cleaned = (raw or "").replace(",", "").replace("$", "").strip()
    if not cleaned:
        raise SyncError("cost cell is empty")
    return round(float(cleaned), 2)


def fetch(board: int, token: str) -> dict[str, dict]:
    meta = query(
        f"{{ boards(ids:[{board}]) {{ name groups {{ id title }} "
        f"columns {{ id title }} }} }}", token
    )["boards"]
    if not meta:
        raise SyncError(f"board {board} not visible to this API key")
    board_meta = meta[0]

    groups = [g for g in board_meta["groups"] if GROUP_MATCH in g["title"].lower()]
    if len(groups) != 1:
        raise SyncError(
            f"expected exactly one group matching {GROUP_MATCH!r}, found "
            + str([g["title"] for g in groups])
        )
    group = groups[0]

    titles = {c["id"]: c["title"] for c in board_meta["columns"]}
    col_job = pick(titles, COL_JOB_ID, "job id column")
    col_cost = pick(titles, COL_COST, "cost column")
    col_mail = pick(titles, COL_MAIL, "mail date column")

    print(f"board   : {board_meta['name']} ({board})")
    print(f"group   : {group['title']}")
    print(f"columns : {titles[col_job]!r}, {titles[col_cost]!r}, {titles[col_mail]!r}")

    rows: dict[str, dict] = {}
    cursor = None
    while True:
        page = (f'items_page(limit:100, cursor:"{cursor}")' if cursor
                else "items_page(limit:100)")
        data = query(
            f'{{ boards(ids:[{board}]) {{ groups(ids:["{group["id"]}"]) {{ {page} '
            f"{{ cursor items {{ id name column_values {{ id text }} }} }} }} }} }}",
            token,
        )
        block = data["boards"][0]["groups"][0]["items_page"]
        for item in block["items"]:
            cells = {c["id"]: c["text"] for c in item["column_values"]}
            job = (cells.get(col_job) or "").strip()
            if not job:
                print(f"  skipped (no job id): {item['name']}", file=sys.stderr)
                continue
            if job in rows:
                raise SyncError(f"job {job} appears on two items; board needs a fix")
            try:
                cost = as_money(cells.get(col_cost, ""))
            except SyncError as exc:
                raise SyncError(f"job {job} ({item['name']}): {exc}") from exc
            mail = (cells.get(col_mail) or "").strip()
            if mail and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", mail):
                raise SyncError(f"job {job}: unexpected Mail Date {mail!r}")
            rows[job] = {"cost": cost, "mail": mail or None}
        cursor = block.get("cursor")
        if not cursor:
            break

    if not rows:
        raise SyncError("group produced no costed items")
    return rows


JOB_LINE = re.compile(r'^(\s*\{name:"[^"]*",\s*job:"(\d+)".*)$', re.M)


def repad(field: str, old: str, new: str, gap: str) -> str:
    """Swap a field's value while keeping the columns after it lined up.

    REDSTONE_JOBS is written as an aligned table by hand. A shorter value would
    drag every following field left, so the run of spaces after the comma
    absorbs the difference."""
    width = len(old) + len(gap)          # value + comma + trailing spaces
    return f"{field}{new}," + " " * max(1, width - len(new) - 1)


def sync_jobs(text: str, rows: dict[str, dict]) -> tuple[str, list[str]]:
    """Update mail and status on each REDSTONE_JOBS entry the board knows about.

    Everything else on the line -- name, fmt, upload, qty, cost -- is left
    exactly as written."""
    notes: list[str] = []

    def one(m: re.Match) -> str:
        line, job = m.group(1), m.group(2)
        row = rows.get(job)
        if row is None:
            return line
        out = line

        want_mail = f'"{row["mail"]}"' if row["mail"] else "null"
        mm = re.search(r'(mail:)(null|"[\d-]{10}")(,\s*)', out)
        if mm and mm.group(2) != want_mail:
            notes.append(f"  {job} mail    {mm.group(2):<12} -> {want_mail}")
            out = out[:mm.start()] + repad(mm.group(1), mm.group(2), want_mail, mm.group(3)) + out[mm.end():]

        # In the Mailed / Delivered group, so mailed by definition.
        sm = re.search(r'(status:")([^"]*)(",\s*)', out)
        if sm and sm.group(2) != "Done":
            notes.append(f'  {job} status  {sm.group(2):<12} -> Done')
            out = (out[:sm.start()]
                   + repad(sm.group(1), sm.group(2) + '"', 'Done"', sm.group(3))
                   + out[sm.end():])
        return out

    return JOB_LINE.sub(one, text), notes


def field_snapshot(text: str) -> list[tuple[str, str, str]]:
    """(job, qty, cost) for every job line -- the fields this script must not move."""
    return re.findall(r'job:"(\d+)".*?qty:(\d+),\s*cost:([\d.]+)', text)


def render(costs: dict[str, float]) -> str:
    """Four pairs per line, matching the surrounding file's hand-written style."""
    items = [f'"{job}":{cost:>8.2f}' for job, cost in sorted(costs.items())]
    lines = ["const MONDAY_COST = {"]
    for i in range(0, len(items), 4):
        chunk = ", ".join(items[i:i + 4])
        lines.append(f"  {chunk}" + ("," if i + 4 < len(items) else ""))
    lines.append("};")
    return "\n".join(lines)


def existing(text: str) -> dict[str, float]:
    block = re.search(re.escape(BEGIN) + r"(.*?)" + re.escape(END), text, re.S)
    if not block:
        raise SyncError(f"{TARGET.name} has no MONDAY_COST markers")
    return {m.group(1): float(m.group(2))
            for m in re.finditer(r'"(\d+)"\s*:\s*([\d.]+)', block.group(1))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1 if any; do not write")
    ap.add_argument("--board", type=int, default=DEFAULT_BOARD)
    args = ap.parse_args()

    token = os.environ.get("MONDAY_API_KEY")
    if not token:
        print("MONDAY_API_KEY is not set", file=sys.stderr)
        return 2

    text = TARGET.read_text()
    try:
        before = existing(text)
        rows = fetch(args.board, token)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    costs = {job: row["cost"] for job, row in rows.items()}
    staged, job_notes = sync_jobs(text, rows)

    added = sorted(set(costs) - set(before))
    removed = sorted(set(before) - set(costs))
    changed = sorted(j for j in set(costs) & set(before)
                     if abs(costs[j] - before[j]) > 0.005)

    print(f"\n{len(costs)} costed jobs on the board")
    for job in changed:
        print(f"  changed {job}  {before[job]:>10,.2f} -> {costs[job]:>10,.2f}")
    for job in added:
        print(f"  added   {job}  {costs[job]:>10,.2f}")
    for job in removed:
        print(f"  removed {job}  (was {before[job]:,.2f})")

    if job_notes:
        print("\nmail date / status on REDSTONE_JOBS:")
        for note in job_notes:
            print(note)

    if not (added or removed or changed or job_notes):
        print("  board and dashboard agree; nothing to write")
        return 0

    if args.check:
        print("\n--check: dashboard is out of date")
        return 1

    updated = re.sub(
        re.escape(BEGIN) + r".*?" + re.escape(END),
        BEGIN + "\n" + render(costs) + "\n" + END,
        staged, count=1, flags=re.S,
    )

    # Refuse to write if the edit moved a piece count or an invoiced cost.
    if field_snapshot(updated) != field_snapshot(text):
        print("error: refusing to write - qty/cost changed, which this script "
              "must never do", file=sys.stderr)
        return 3

    TARGET.write_text(updated)
    print(f"\nwrote {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
