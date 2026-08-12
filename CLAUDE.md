# Call-Logs-KPI

`dashboard.html` is the Twin Home Buyer marketing KPI dashboard. Most of it is a
static snapshot: call records, the direct-mail board, and the mail jobs are all
literals in the file. Two of those blocks are **generated** and must not be
edited by hand.

## Read this before touching dashboard.html

**It is not publishable artifact source.** The file in this repo is the
*rendered* artifact — it opens with `<!doctype html><html><head><!-- frame-runtime -->`
and carries claude.ai's own runtime preamble. Artifact publishing supplies that
wrapper itself and expects content with no `<!doctype>`, `<html>`, `<head>` or
`<body>`.

So do **not** copy this file into an artifact wholesale. To move these changes
into an artifact, apply the *changes* to that artifact's source. They are
surgical — 11 hunks, ~390 lines, almost all insertions. Only four edits touch
existing code:

- `suppliedCost()` — carries a second cost total alongside the first
- `jobDate()` — a live mail date wins over the stored one
- the withheld note in `mailSection()` — says which cards are withheld, not the
  whole section
- one `REDSTONE_JOBS` line (job 291483) — its `mail` and `status`, which the
  monday sync maintains

## The two generated blocks

Both live between markers. A merge conflict inside them is resolved by
**re-running the generator**, never by hand-picking numbers — that is the whole
point of having them.

| Block | Source of truth | Refreshed by |
|---|---|---|
| `MONDAY_COST` | monday.com board `Total Cost` | `scripts/sync_monday_cost.py` |
| `REDSTONE_COST` | the invoice PDF on each Red Stone job | `scripts/sync_redstone_cost.py` |

They exist because the two systems can disagree, and the dashboard shows both
side by side rather than picking a winner. When they agree the cards match; when
they don't, the gap is visible instead of buried. Do not "simplify" them into
one number.

```bash
python3 scripts/sync_monday_cost.py            # rewrite MONDAY_COST
python3 scripts/sync_monday_cost.py --check    # report drift, exit 1, write nothing
python3 scripts/sync_redstone_cost.py          # rewrite REDSTONE_COST
python3 scripts/sync_redstone_cost.py --check  # same, for invoices
```

`--check` writes nothing and exits 1 when the file is stale, so it works as a CI
gate. Both scripts refuse to write a partial result.

`sync_monday_cost.py` also syncs `mail` and `status` on each `REDSTONE_JOBS`
entry. A job with no mail date is treated as not yet mailed and drops out of
every total — that once hid $1,947.49 of real spend until someone filled in a
date on the board.

### Environment

| Variable | Used by |
|---|---|
| `MONDAY_API_KEY` | `sync_monday_cost.py` |
| `REDSTONE_USER` | `sync_redstone_cost.py`, `serve.py` |
| `REDSTONE_PASS_B64` *(preferred)* | as above — base64 |
| `REDSTONE_PASS` | plain-text fallback |

The Red Stone password **begins with a space**. Config forms strip that, which
looks exactly like a wrong password, so prefer `REDSTONE_PASS_B64`.

Credentials never belong in `dashboard.html`. It is published to the whole
organisation; anything in it is readable by every viewer. `.cache/` holds
downloaded invoices, cookies and optionally saved credentials, and is gitignored.

## Deliberately not synced

- **Piece counts.** `qty` on `REDSTONE_JOBS` is hand-maintained. 13 of 16 jobs
  disagree with what Red Stone billed, always overstated (job 287106 reads 4,032
  against 3,132 billed). Left alone by explicit decision — cost first.
- **Jobs before May 2026.** Red Stone holds 39 earlier jobs, ~76,700 pieces,
  that the board and this dashboard do not cover. Scope is May-onward; widening
  it silently would misrepresent what the page claims.
- **`cost` on `REDSTONE_JOBS`.** A fallback for jobs missing from
  `REDSTONE_COST`. The generated block wins.

## What is live, and where

Live reads only work outside a published artifact. An artifact runs under a CSP
that blocks requests to any external host.

| | In a published artifact | Opened locally |
|---|---|---|
| Monday cost | snapshot | live — paste an API key into "Read monday.com live" |
| Red Stone cost | snapshot | live — via the local helper |

Monday can be read straight from the browser because `api.monday.com` sends
`access-control-allow-origin: *`. **Red Stone sends no CORS headers at all**, so
a browser will not hand the page any response from it, whatever credentials are
supplied. That is why the helper exists — not a gap in the code.

```bash
python3 scripts/serve.py        # serves the dashboard, adds a Red Stone refresh control
```

The helper signs in, downloads invoices, caches them, and answers the page.
Refreshes are incremental: only jobs without a cached invoice are fetched. It
discovers jobs from Red Stone's own export, so a new job appears without anyone
editing a file.

## Before republishing the artifact

Run both syncs first. Nothing refreshes on its own, and a republished artifact
freezes whatever numbers happen to be in the file at that moment.

```bash
python3 scripts/sync_monday_cost.py && python3 scripts/sync_redstone_cost.py
```

If either reports drift, the artifact you were about to publish was stale.
