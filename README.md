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

- **Direct mail cost and volume** — from the Red Stone Upload board (Monday.com board
  `18392647066`), quantity and cost exactly as recorded, re-verified against a live
  board read. These map to two Standard CRM Sources:

  | Standard CRM Source | Jobs | Pieces | Cost | Per piece |
  |---|---|---|---|---|
  | **Direct Mail - Postcard** | 13 | **58,107** | **$28,212.76** | **$0.4855** |
  | **Direct Mail - Letter** | 1 | 293 | $327.83 | $1.1189 |
  | _in production, not mailed_ | 1 | 3,515 | $1,947.49 | $0.5541 |

  The letter job is a costed CRM source in its own right, not an exclusion — at 2.3× the
  postcard rate it simply must not be blended into postcard KPIs.

  Only **mailed** jobs feed Pieces Mailed and the cost KPIs. `Relaunch_13` sits in
  🚀 Uploaded / In Production, so its spend is committed but its pieces have not gone
  out; counting it would have overstated August by 3,515 pieces — 77% above the 4,583
  actually mailed. It moves into the mailed figure automatically once the board marks
  it Done with a mail date.

  By month mailed: May 3,752 / $2,130.56 · Jun 30,976 / $14,945.56 ·
  Jul 18,796 / $8,922.26 · Aug 4,583 / $2,214.38.

**Not supplied at all — and not estimated**

- **Revenue.** No revenue figure exists in any source read so far: the call logs carry
  calls, leads, dispositions and appointments but no money, and the Red Stone board
  carries quantity and cost only. Gross Revenue, Net Revenue and ROI therefore report
  "awaiting revenue data" rather than a number. Acquisition and contract *counts* still
  report, because those are volumes rather than money.

  Set `REVENUE_SUPPLIED = true` and populate `gross_revenue` / `net_revenue` on the
  records once real figures arrive. Nothing else changes.

**Sample data**

- Lead, call, appointment, offer, contract and acquisition volumes
- Every source's volume profile

Volumes are anchored to Twin Home Buyer's real 2026 shape so the prototype reads as
familiar — roughly 119 leads and 610 calls a month, ~4 acquisitions a month — but they
are generated, not measured.

Because of this split, any KPI that divides real cost by sample volumes — Response Rate,
Cost Per Call, Cost Per Acquisition — is structurally correct but not yet a true number.
The Direct Mail page says so directly, at the top.

## Lead Source is derived from the Campaign

The Google Sheet's own Lead Source column is **not trusted**. Classification reads the
**Campaign** field and resolves it against the supplied Master Source → Standard CRM
Source map: **8 master sources, 19 Standard CRM Sources.**

Priority order: campaign name → platform indicators → contact method → the sheet's own
Lead Source as supporting information only.

**Two axes.** Direct Mail, PPL, Referral, Outbound and MLS/Redfin resolve from the
campaign alone. PPC, Organic Search and TV each split **Call vs Web Form**, which a
campaign name often cannot settle on its own — those need a recorded contact method
(read from the campaign wording where present, otherwise the `contact_method` field).
Without one, the lead lands in the review queue rather than being assigned a guess.

**Nothing is guessed.** A campaign that cannot settle a Standard CRM Source waits on the
Classification page with the reason stated. Current sample data holds 81 such leads:

| Reason | Leads |
|---|---|
| No CRM Source defined for Reddit Ads | 16 |
| Campaign is empty | 15 |
| Contact method unknown — Call or Web Form | 14 |
| No campaign signal | 13 |
| No CRM Source defined for Facebook / Meta Ads | 12 |
| No CRM Source defined for direct mail with no format named | 11 |

**Unclassified leads still count.** All Master Sources includes the review bucket, so the
company lead total stays true (835 for 2026, reconciling exactly against the raw record
count and the sum of every master source). The dashboard states how many are unclassified
rather than quietly dropping them.

**Manual overrides win.** Assigning a source on the Classification page is an override
that auto-classification never touches. Only the explicit "Re-run auto-classification"
button clears them, and it says how many it will discard.

**Classifier self-test** runs at load: all 54 sample campaigns must resolve to their
expected Standard CRM Source, and the result is shown on the Classification page. The
rule table is listed there in priority order.

### Two decisions needed from you

1. **Facebook / Meta Ads and Reddit Ads have no Standard CRM Source** in the supplied map,
   but campaigns for both exist. They are held for review rather than folded into
   PPC - Google or PPC - Bing. Add leaves, or confirm they should be excluded.
2. **TV now has sub-sources.** An earlier instruction said TV Commercial must have no
   Sub-Source dropdown; the final map gives TV two (Call and Web Form). The final map is
   implemented. MLS / Redfin is now the only master with a single CRM source, so its
   Standard CRM Source control is suppressed instead.

## Marketing cost is supplied, never modelled

No cost figure is invented anywhere in this app. Cost lives in one registry,
`COSTING`, keyed by source and month:

```js
COSTING["Direct Mail - Postcard"] = { supplied: true, byMonth: { "2026-07": {cost, pieces} } }
```

Currently **2 of 19 Standard CRM Sources have costing** — `Direct Mail - Postcard` and
`Direct Mail - Letter`, both from the Red Stone board. The other 17 are awaiting figures
and behave accordingly:

- Marketing Spend, Cost Per Lead, Cost Per Qualified Lead, Cost Per Acquisition and
  ROI render an em dash with an "awaiting costing" chip — **never a zero**, because
  zero is a claim about spend and an empty registry entry is not
- **A total is withheld whenever any source inside the selection lacks a figure.** That
  applies at every level: All Master Sources shows no spend, and even Direct Mail shows
  none, because `Direct Mail - Check` has no costing yet. Postcard and Letter each report
  in full. A partial total would understate real spend
- Cost columns in the source table stay blank for those sources, so it is obvious at
  a glance which channels have verified cost

To add costing, fill the registry entry for that Standard CRM Source. Nothing else
changes — every KPI, the source table and the ROI maths read from that one registry.

## Pages

| Page | Purpose |
|---|---|
| Dashboard | Executive KPIs, postcard KPIs, eight trend charts, source table |
| Source Performance | Funnel per source; toggle master vs Standard CRM Source, sort, search, CSV export |
| Direct Mail | Postcard and Letter KPIs plus the Red Stone job log |
| Calls | Call-log reporting by month, agent, direction and caller type |
| Deals | Lead-to-acquisition funnel and the deal register |
| Classification | Review queue, rule table, manual overrides, classifier self-test |
| Data Sync | Google Drive and Monday.com sync shell — disabled until Phase 2 |
| Admin Settings | Connections, source costing, taxonomy, field mapping |

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
3. **`Relaunch_12` is marked Done and sits in 📬 Mailed / Delivered but has no mail
   date.** It is treated as mailed and attributed by its upload date (2026-07-30),
   which puts its 3,171 pieces in July. If it actually dropped in August, July is
   overstated and August understated. Filling the date in on the board settles it.
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
