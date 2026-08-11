# Twin Home Buyer — Marketing KPI Dashboard

Executive marketing KPI reporting for Twin Home Buyer. Select a marketing source,
select a month, compare month-to-month, and read the funnel that matters:

```
Marketing Spend → Calls → Leads → Qualified Leads → Appointments
→ Offers → Contracts → Acquired Properties → Revenue → ROI
```

Built from the Final Master Build Prompt. **Phase 1 is complete and awaiting review.**

## Run it

Open `index.html` in a browser. No build step, no server, no dependencies — it is a
single self-contained file.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| **1** | Dashboard prototype, filters, KPI cards, charts, source table | **complete — awaiting approval** |
| 2 | Google OAuth, Call Logs 2026 folder read, normalization, field mapping, sync | not started |
| 3 | Monday.com auth, board selection, column mapping, lead matching, duplicate detection | not started |
| 4 | Real KPI engine — replace sample volumes with live records, validate every formula | not started |
| 5 | Scheduled sync, error logging, sync history, data-quality warnings | not started |

Per the build prompt, Phase 2 does not begin until the dashboard is approved.
Nothing in this app connects to Google Drive or Monday.com yet.

## What is real and what is sample

This distinction is enforced in the code and stated on the screen. It matters:
mixing the two silently would produce KPIs that look authoritative and are not.

**Real data**

- **Postcard cost and mail volume** — the 15 postcard jobs from the Red Stone Upload
  board (Monday.com board `18392647066`): 61,622 pieces, $30,160.25, blended
  $0.4894 per piece. Quantity and cost exactly as recorded.

**Sample data**

- Lead, call, appointment, offer, contract and acquisition volumes
- All revenue figures
- Every source's volume profile

Volumes are anchored to Twin Home Buyer's real 2026 shape so the prototype reads as
familiar — roughly 119 leads and 610 calls a month, ~4 acquisitions a month — but they
are generated, not measured.

Because of this split, any postcard KPI that divides real cost by sample revenue
(ROI, Cost Per Acquisition) is structurally correct but not yet a true number. The
Postcard Performance page says so directly.

## Marketing cost is supplied, never modelled

No cost figure is invented anywhere in this app. Cost lives in one registry,
`SOURCE_COSTING`, keyed by source and month:

```js
SOURCE_COSTING["Postcard"] = { supplied: true, byMonth: { "2026-07": {cost, pieces} } }
```

Currently **1 of 10 sources has costing** (Postcard, from Red Stone). The other nine
are awaiting figures and behave accordingly:

- Marketing Spend, Cost Per Lead, Cost Per Qualified Lead, Cost Per Acquisition and
  ROI render an em dash with an "awaiting costing" chip — **never a zero**, because
  zero is a claim about spend and an empty registry entry is not
- **All Sources shows no spend total at all.** A company figure built from one
  channel's costing would understate the real number, so it is withheld rather than
  computed
- Cost columns in the source table stay blank for those sources, so it is obvious at
  a glance which channels have verified cost

To add a source's costing, set `supplied: true` and fill `byMonth`. Nothing else
changes — every KPI, the source table and the ROI maths read from that registry.

## Pages

| Page | Purpose |
|---|---|
| Dashboard | Executive KPIs, postcard KPIs, eight trend charts, source table |
| Source Performance | Full funnel per source; sort, search, paginate, CSV export |
| Postcard Performance | All 13 postcard KPIs plus the Red Stone job log |
| Calls | Call-log reporting by month, agent, direction and caller type |
| Deals | Lead-to-acquisition funnel and the deal register |
| Data Sync | Google Drive and Monday.com sync shell — disabled until Phase 2 |
| Admin Settings | Connections, source costing, field mapping, source aliases |

## Design and correctness notes

- **KPI polarity is business-aware.** A rising cost reads red and a falling cost reads
  green. Colour never means "up". Every delta pairs an arrow glyph and a signed number
  with the colour, so the meaning survives colour-blindness and greyscale printing.
- **Chart palette is validated,** not eyeballed: worst all-pairs CVD ΔE 9.2, worst
  normal-vision ΔE 24.0. The aqua series sits below 3:1 against the surface, so it
  ships with direct labels and a table view as relief.
- **A missing value is never a zero.** `safeDiv` returns null for an undefined ratio,
  null renders as an em dash, and a chart with no plottable values shows an explicit
  empty state rather than a degenerate axis.
- **The report month defaults to the most recent complete month.** Opening on a
  part-month makes every card read red against a full prior month — a reporting
  artefact, not a performance change. Partial months stay selectable and are labelled.
- **Trends always span the full report year** with the selected month emphasized. A
  one-bar trend is not a trend.
- **The funnel cannot invert.** Acquisition nests inside contract inside offer inside
  appointment inside qualified, in the data generator and in the aggregate.

## Data-quality issues found in the 2026 Call Logs folder

These are carried into Phase 2 as sync warnings rather than allowed to distort a KPI.
They are listed on the Data Sync page.

1. **Column layout changes mid-year.** A Postcard column appears from April onward,
   shifting the lead-total column. Mapping must be header-driven, never positional.
2. **27 July – 2 August 2026 is entirely blank** — must read as no data, not zero.
3. **6 – 12 July 2026 does not reconcile** — source columns sum to 23, reported lead
   total says 43.
4. **25 of 32 weekly sheets label their totals `Week 3 TOTAL`** regardless of week.
5. **One agent's Total formula is missing**, showing blank where others show a value.
6. **An August 12 block carries an August 10 date** in the appointments table.
7. **Postcards mailed are not attributed.** 61,915 pieces went out through Red Stone
   while the call logs attribute only a handful of leads to Postcard — responses are
   most likely landing under Direct Mail. This one materially distorts postcard
   response rate and needs a decision before Phase 4.

## Open questions

1. **Costing per source** — sending them per source and per month drops them straight
   into the registry.
2. **`THB_Letter_01` is currently excluded from postcard KPIs.** It sits on the same
   Red Stone board but is a letter, not a postcard, at $1.1189 per piece against the
   $0.4894 postcard rate — and the build prompt says letters are not to be promoted.
   Confirm, or say the word and it counts inside Postcard.
3. **Two postcard jobs have no mail date** (`Relaunch_12`, `Relaunch_13`). They are
   currently attributed by upload date. Filling the mail dates in on the board fixes
   it automatically.
4. **The ROI example in the prompt does not reconcile with the stated formula.**
   §5 defines ROI as `(Net Revenue − Marketing Cost) ÷ Marketing Cost × 100`. The §8
   worked example shows spend $42,580 and net revenue $268,994 against ROI 645.40%,
   but that formula yields 531.5% (and `Net ÷ Cost` yields 631.7%). The stated formula
   is implemented. Confirm which is intended.
5. **Postcard response attribution** — see data-quality item 7.

## Repository

Single file by design for Phase 1, so the prototype can be opened and reviewed with
no tooling. Phase 2 introduces a backend for OAuth and sync, at which point the
JavaScript splits into modules.
