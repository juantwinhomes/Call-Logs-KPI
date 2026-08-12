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

## Where every number comes from

**No sample data.** Every figure is counted from a real source.

### Call activity — the 2026 Call Logs folder

All 32 weekly workbooks, read 11 August 2026 **from the .xlsx export so every tab is
visible.** Each workbook holds **11 to 13 tabs**:

| Tab | Contents |
|---|---|
| `Daily Calls Overview` | the hand-entered weekly summary blocks |
| `Campaigns`, `Dispositions`, `Date mailed` | reference lists |
| **one tab per day** (`8.10`, `8.11` …) | **the per-call record — one row per call** |
| `Week` | a roll-up copy of the daily tabs |

**This app counts the daily tabs.** Three tabs carry call counts and they disagree:

| Source of the count | Calls | What it is |
|---|---|---|
| **Daily tabs (used)** | **5,162** | primary per-call record |
| `Week` tab | 5,095 | a copy; runs short in 5 of the 32 workbooks |
| `Daily Calls Overview` | 4,878 | hand-entered summary blocks |

The summary blocks run 284 below the daily tabs, and **196 of that gap is one week** —
27 July to 2 August — whose summary was never filled in while its daily tabs hold 196 calls.

**What counts as a call.** A row is a call when its `Date` cell holds a real date serial
and at least one of `Contact no.` / `Call Type` / `Call Disposition` is filled in. That
excludes 17 rows carrying only a date, which are layout residue. A row's date comes from
its own `Date` cell, never from the tab it sits on — one row inside tab `01.12` is dated
13 January and is counted there. The header row is located by `Start Time` in column 2,
which is the one header cell intact in all 224 daily tabs; columns 1–10 are positional and
an audit of every tab found no tab where the data columns are shifted.

| Measure | 2026 |
|---|---|
| Call records | **5,162** |
| Inbound / Outbound / Missed / blank | 1,574 / 3,582 / 3 / 3 |
| First Time / Repeat / Follow Up Responder | 1,012 / 920 / 3,187 |
| `Appointment Booked (H)` | 121 |
| Unique phone numbers | 2,042 |
| Distinct Lead Type values | 7 |
| Distinct Disposition values | 95 |

Every figure in that table was re-derived from the 32 `.xlsx` files on 11 August and
checked against what the app renders; the appointment dispositions were checked
individually (121 / 32 / 20 / 16 / 4 / 8).

**The Call Log page carries lead names and phone numbers.** Contact Name and
Contact no. are embedded as the sheets write them so the page can be searched and
filtered offline. That makes this file personal data — the published artifact is private
by default, and it is worth keeping it that way.

The page filters on search (contact name, phone number, campaign, disposition, agent),
direction, caller type, lead type, disposition and agent. Filters combine, each dropdown
shows the count behind every option and offers `(blank)` where the sheet left the field
empty, a phone search is matched digit for digit so `5103855660` finds `(510) 385-5660`,
and Export CSV writes exactly the filtered set with every field quoted so names
containing a comma survive.

Columns as the daily tabs label them: Date · Start Time · Contact Name · Contact no. ·
Lead Source · Campaign · Call Type · Caller Category · Lead Type · Call Disposition ·
Lead Status · System Call Duration · Talk Time · Wait Time · Agent Name · Address ·
Offer · Check no.

### Mail cost and volume — Red Stone Upload board (`18392647066`)

| Standard CRM Source | Jobs | Pieces | Cost | Per piece |
|---|---|---|---|---|
| Direct Mail - Postcard | 13 | 58,107 | $28,212.76 | $0.4855 |
| Direct Mail - Letter | 1 | 293 | $327.83 | $1.1189 |
| Direct Mail - Check | **none on the board** | — | — | — |
| _in production, not mailed_ | 1 | 3,515 | $1,947.49 | — |

Every mailed job now carries a real Mail Date on the board, so nothing is attributed by
its upload date any more. Job 291195 (`THB_Postcard_Relaunch_12`) had its mail date filled
in as **2026-08-04** on 11 August, which moves its 3,171 pieces and $1,672.15 out of July
and into August: July postcard volume reads 15,625 pieces / $7,250 and August reads
7,754 pieces / $3,887.

### Direct-mail pipeline — monday.com board `18421423765`

Board **📬 Direct Mail & Postcard Leads**, in the *Equity Track Iriga – Operating System*
workspace, read 11 August 2026. 452 items, one per property. This is the only source in
the app for acquisitions and cancelled contracts.

| Lead Stage | Postcard | Checks | Letters | All |
|---|---|---|---|---|
| New | 20 | 50 | 0 | 70 |
| Contacted | 39 | 40 | 0 | 79 |
| Interested | 10 | 2 | 0 | 12 |
| Appointment | 2 | 6 | 0 | 8 |
| Under Contract | 0 | 0 | 0 | **0** |
| **Acquired** | **1** | **2** | 0 | **3** |
| **Cancelled Contract** | 0 | **2** | 0 | **2** |
| Dead | 97 | 180 | 1 | 278 |
| **All stages** | **169** | **282** | **1** | **452** |

The board's `Source` labels map onto the Standard CRM Sources one for one:
`Direct Mail (Postcard)` → `Direct Mail - Postcard`, `Direct Mail (Checks)` →
`Direct Mail - Check`, `Direct Mail (Letters)` → `Direct Mail - Letter`. A fourth label,
`Direct Mail (Call-in)`, exists on the board but no item uses it and no Standard CRM
Source has been given for it, so it is left unmapped.

**The five deal-stage items, quoted as the board holds them:**

| Property | Lead Stage | Board group | Source | Campaign / List | Date Received | Revenue |
|---|---|---|---|---|---|---|
| 1464 Sunrise Pkwy, Petaluma — Denise Marks | Acquired | ✅ Closed / Won | Postcard | Postcard | 2026-03-10 | **$35,918.75** |
| 8227 Ney Ave, Oakland — Dianne Andrews | Acquired | ✅ Closed / Won | Checks | Sorted Ugly Houses EQT (Checks) | 2026-02-09 | — |
| 1932 Chestnut Ave, Antioch | Acquired | ✅ Closed / Won | Checks | _blank_ | 2026-03-06 | — |
| 33025 Wildomar Rd, Lake Elsinore — Jose Espinoza | Cancelled Contract | 📝 Under Contract | Checks | Postcard | 2026-01-28 | — |
| 7400 Rudsdale St #7G, Oakland — Damond Dixon | Cancelled Contract | 🚫 Cancelled Contract | Checks | Liens | 2026-03-18 | — |

The Rudsdale note on the board reads: *"Jul 13: CONTRACT CANCELLED (per Juan) — was Under
Contract at $166,000 → renegotiated to $120,000, then cancelled. Moved out of Under
Contract."*

**Four things about this board constrain what the app can say:**

1. **`Lead Stage` has no date.** It records where a lead stands *now*, not when it got
   there. The period filter therefore runs on `Date Received`, the only date the board
   carries, so a lead received in March and acquired in July counts in March. The
   Outcomes tables are shown whole-board rather than period-filtered for the same reason.
2. **One item is in two outcomes at once.** 33025 Wildomar Rd sits in the 📝 Under
   Contract group while its `Lead Stage` reads Cancelled Contract. Both readings are
   shown; neither is picked.
3. **An acquired property has no campaign or lead name.** 1932 Chestnut Ave (Acquired,
   Checks) has `Campaign / List` and the lead link both blank. Its `Date Received` was
   filled in on the board on 11 August and now reads 2026-03-06, so all three
   acquisitions are dated and period counts reach 3.
4. **`Revenue` is filled on 1 of the 3 acquisitions** — $35,918.75 on the Petaluma
   postcard deal. That figure **is** reported, on a *Revenue From …* card per mail format,
   and every place it appears states how many of the period's acquisitions it covers, so
   it is never read as the period's full revenue. Both check acquisitions are blank.

**Stage counts are current state, not events.** New, Contacted, Interested, Appointment
and Under Contract are stages a lead passes through, so each is only what stands there
today — not how many leads ever reached it. Acquired, Cancelled Contract and Dead are
where leads stop, so those three hold. The cards are worded accordingly: *Still At
Appointment Stage From Checks*, *Still Under Contract From Postcard*.

**Appointments on the dashboard come from the call logs, not from this board.** The card
reads **Appointment Booked From Postcard / Checks / Letters** and counts calls whose
`Call Disposition` is `Appointment Booked (H)` — 15 for postcard, 12 for checks, 0 for
letters across 2026. The board's own `Appointment` Lead Stage reads 2 / 6 / 0 for the same
formats, and that figure lives on the Outcomes page where it is labelled as board state.

The two will not match, and are not supposed to: the sheets count **calls** while the
board counts **properties**; Lead Stage shows only where a lead stands now, so anything
that has moved on no longer reads Appointment; and the two are different populations —
452 board properties against 1,942 direct-mail calls for 2026, neither a subset of the
other. The app says this inline, with both live figures in the text.

### Not reported, and not estimated

On the Outcomes page, each with the reason stated: Qualified Leads · Offers Made ·
Contracts Signed · Acquired Properties outside direct mail · Net Revenue · ROI ·
Cost Per Lead · Cost Per Qualified Lead · Cost Per Acquisition.

Three of these moved off the withheld list for direct mail on 11 August 2026:
**Acquired Properties**, **Cancelled Contracts** and **Revenue** now come from the
monday.com direct-mail board above. Revenue is reported at the coverage the board
actually has — 1 of 3 acquisitions, stated on screen every time the figure appears.
**Net Revenue** stays withheld because the board records no purchase price, rehab,
holding or closing cost, so a net cannot be worked out from the gross. They stay withheld everywhere else, because that board covers
direct mail only and no board has been given for PPC, PPL, referral, TV, organic search,
outbound or MLS.

**Cost Per Acquisition is still withheld even though acquisitions now exist**, because
the acquisitions and the mail cost do not line up in time: the three acquired properties
were received in February and March 2026, while the Red Stone mail cost on record starts
16 May 2026. Dividing one by the other would charge a cost against acquisitions it could
not have produced. A rule for which mail spend belongs to which acquisition is needed
first.

**Contracts Signed** cannot be counted at all from the board, because `Lead Stage` holds
a current stage rather than a dated event. What the board does support — how many leads
stand at each stage right now — is what the pipeline cards show.

Three columns exist but cannot carry a KPI:

- **`Offer`** — 41 of 5,162 rows (0.8%), free text rather than numbers
- **`Lead Status`** — blank in 5,161 of 5,162 rows; the one populated cell holds a person's name
- **`Check no`** — 4 rows, carrying values like `Layout0, Layout4` rather than check numbers

`Qualified` has no column. The daily tabs record **Lead Type** (Existing Interested Lead,
Unresponsive, New Interested Lead, Invalid, Not Interested, Do not Mail, Neutral) and no
rule has been given for which of those counts as qualified.

Appointment dispositions are reported separately by their exact sheet wording rather than
summed into one figure the sheets do not define.

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

**Nothing is guessed.** Run against the real logs, the classifier places **2,962 of 5,132
records (57.7%)** and holds **2,170 (42.3%)** for review:

| Reason | Records |
|---|---|
| No CRM Source defined for paid search with no platform named | **1,498** |
| No campaign signal | 526 |
| Contact method unknown — Call or Web Form | 118 |
| Outbound channel not identified | 15 |
| Campaign is empty | 7 |
| No CRM Source defined for Facebook / Meta Ads | 3 |
| No CRM Source defined for direct mail with no format named | 3 |

What it does place:

| Standard CRM Source | Records |
|---|---|
| Direct Mail - Check | 1,298 |
| PPL - Property Leads | 759 |
| Direct Mail - Postcard | 474 |
| Direct Mail - Letter | 165 |
| PPL - Motivated Leads | 128 |
| TV - Commercial - Call | 124 |
| Outbound - Homeowner Text | 13 |
| Organic - SEO - Call | 1 |

**Unclassified records still count.** All Master Sources includes the review bucket, so the
record total stays true — 5,132, reconciling exactly against the raw record count and the
sum of every master source. The dashboard states how many are unclassified rather than
quietly dropping them.

**Manual overrides win.** Assigning a source on the Classification page is an override
that auto-classification never touches. Only the explicit "Re-run auto-classification"
button clears them, and it says how many it will discard.

The rule table is listed on the Classification page in priority order, alongside the
campaigns still awaiting a rule.

### Decisions needed before coverage improves

1. **PPC cannot be split.** All 1,498 paid-search records — almost all one campaign,
   `PPC LEAD THB` — name no platform. `PPC - Google - Call`, `PPC - Google - Web Form`,
   `PPC - Bing - Call` and `PPC - Bing - Web Form` are unfillable from these sheets. That
   is 29.2% of all records.
2. **526 records are direct mail with no format in the campaign name** — `Liens Area510`,
   `NOD Area415`, `Liens Verification EQT 2` and 30 others. Their Lead Source column says
   Direct Mail but nothing says postcard, letter or check.
3. **`Equity Track INC (Website)`** has no CRM source in the map.
4. **`Realtor` as a bare campaign** — Referral - Realtor / Professional, or
   Outbound - Realtor Email? The campaign name alone does not say.
5. **Facebook / Meta Ads (3 records)** has no CRM source in the map.
6. **TV has sub-sources.** An earlier instruction said TV Commercial must have none; the
   final map gives it Call and Web Form, and the final map is implemented. 118 TV records
   sit in review because an outbound follow-up call does not reveal how the lead first
   arrived. MLS / Redfin is now the only master with a single CRM source.

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
| Dashboard | Call KPIs for the selected source, mail performance and pipeline cards under Direct Mail, six trend charts, source table |
| Source Performance | Funnel per source; toggle master vs Standard CRM Source, sort, search, CSV export |
| Call Log | Every call with contact name and phone number, filterable by search, direction, caller type, lead type, disposition and agent, with CSV export of the filtered set |
| Outcomes | Appointment dispositions, direct-mail deal outcomes from the monday.com board, Lead Type and disposition breakdowns, and the withheld list with reasons |
| Classification | Review queue, rule table, manual overrides, classifier self-test |
| Data Sync | Every source read, which tab each number came from, and every problem found |
| Admin Settings | Connections, source costing, taxonomy, field mapping |

Mail performance and pipeline cards appear only when the Master Source is **Direct
Mail** — selecting any other master hides them rather than showing zeroes.

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
2. **The 27 July – 2 August 2026 summary block is blank**, but the week is not empty —
   its daily tabs hold 196 calls. Only the hand-entered summary was never filled in.
3. **The `Week` roll-up tab runs short in 5 of the 32 workbooks** — Jan wk1 305 against
   310, Jan wk2 150 against 157, May wk3 70 against 76, Jun wk1 134 against 153. The
   daily tabs are treated as primary.
4. **6 – 12 July 2026 does not reconcile** — source columns sum to 23, reported lead
   total says 43.
5. **25 of 32 weekly sheets label their totals `Week 3 TOTAL`** regardless of week.
6. **One agent's Total formula is missing**, showing blank where others show a value.
7. **An August 12 block carries an August 10 date** in the appointments table.
8. **30 calls carry a `Start Time` that is text rather than a time** — values like
   `:27 PM`, `, 3:38 PM`, `7, 4:10 PM`, across 19 days. They are real calls with a real
   date, contact number and disposition, so they are counted; only their time of day is
   unusable. An earlier build of this app dropped them, which is why its total read 5,132
   instead of 5,162.
9. **17 rows hold a date and nothing else** — no contact number, call type or
   disposition. Layout residue, not calls, and excluded.
10. **5 daily tabs have a corrupted header cell.** `6.2` reads `0.0`, `6.3` reads `9.0`
    and `6.4` reads `8.0` where `Date` belongs; `7.1` has a blank `Date` header and its
    `Campaign` header overwritten with the data value `PPC LEAD THB`. The data columns
    underneath are intact.
11. **Tab `6.21` has no header row and no calls** — the whole tab is blank, so 21 June
    2026 has no per-call record.
12. **Tab `6.15` misspells a header** — `Call Dsiposition`.
13. **`Agent Name` and `Offer` move between tabs.** `Agent Name` is column 16 in 222 tabs
    and column 15 in one; `Offer` is column 19 in 210 tabs and column 18 in 13. Both are
    located by header name, never by position.
14. **The `Lead Status` column only exists in 128 of the 224 daily tabs**, and across all
    5,162 call rows it holds exactly one value — `Tyagi, Rajnish`, a person's name.
15. **`Check no` holds layout names** — 4 rows, carrying `Layout0`, `Layout1`,
    `Layout0, Layout6`, `Layout0, Layout4, Layout3`.
16. **`Address` is populated on 553 of 5,162 rows.**
17. **502 rows have the contact name `View`** — but the cell is a hyperlink, and the link
    is the useful part: column C of every daily tab points at
    `my.reiblackbook.com/contacts/<id>`, on 4,793 of the 5,110 rows. So the caller's real
    name and property can be read off the CRM lead even where the sheet only says
    &ldquo;View&rdquo;, and that is how the postcard response-time table is built. What
    is *not* recoverable is a lead created with a phone number and nothing else, which is
    what most postcard callers are: of the 70 postcard first-time callers since the
    programme began, only 37 have a full name anywhere and 21 an address.
18. **11 different spellings mean "no caller name"** — `No Caller Name`, `No caller name`,
    `, No Caller Name`, `, No caller Name`, `Caller name not provided`, `, No Info`,
    `, No info provided` and more, so they do not group.
19. **One call has no phone number at all**; every other row carries one.
20. **21 daily tabs hold no calls**, including every day after 10 August, which is as far
    as the logs go.
21. **Postcards mailed are not attributed.** 61,622 pieces went out through Red Stone
   while the call logs attribute 227 inbound calls to Postcard by campaign — much of the
   response is landing in the unclassified bucket. This materially distorts postcard
   response rate and needs a decision before Phase 4.
22. **The inbound call rate was measuring two different periods.** It divided a full
   year of postcard inbound calls by three months of postcard pieces: 227 calls against
   61,622 pieces read 3.68 per 1,000, when 122 of those calls came in before 16 May and
   no postcard in this programme had been posted. Inbound is now counted from three days
   after the first batch in the period went out — 106 calls, **0.17% or 1.72 per 1,000**.
23. **Days from mailing to call was capped by the mailing cadence.** The old method
   credited every postcard first-time caller to the most recent batch mailed before
   their call. Batches go out roughly weekly, so the longest lag it could ever report
   was 17 days, and a homeowner who rang two months after their postcard was booked as a
   few days after somebody else's. It is now measured only on callers whose own piece is
   known — matched on address + ZIP, on street, on the property address written into the
   agent's call summary, or on their name in the mailed list — which gives 14 homeowners,
   a **20.5-day average, a 17-day median and a spread of 3 to 62 days**. Four of the 14
   rang more than a month after their postcard.
24. **The `Summary` column holds what the structured columns are missing.** Agents write
   the property and the caller into the prose — *"Spoke with Sean regarding 2988 Orchid
   Street, Fairfield, CA 94533"* — so it is mined as a third source after `Address` and
   the CRM lead. Bob Mendez was found that way, 26 days after his postcard, with nothing
   but a phone number in the columns. A **name** taken from the prose is only used where
   the caller has none of their own: otherwise it finds whoever else the agent mentioned,
   which put Chris Paratore against a piece posted to Luther Martin.
25. **The mailed lists carry no owner telephone number.** Across all 32 tabs the only
   columns are First, Last, Owner, Full Name, address, city, state, ZIP and `profit_dial`,
   so a caller's number cannot be matched against them. Name and address are the only keys
   the data allows.

### On the monday.com direct-mail board

9. **`Lead Stage` has no date**, so the period filter can only run on `Date Received`.
10. **33025 Wildomar Rd is in two outcomes at once** — 📝 Under Contract group,
    Cancelled Contract stage.
11. **1932 Chestnut Ave is Acquired with no `Date Received`**, campaign or lead name, so
    it falls in no period.
12. **`Revenue` is 0.2% populated** — 1 of 452 items.
13. **`Campaign / List` is blank on 76 items** — 44 postcard, 32 check — so mail cost
    cannot be traced to a Red Stone job for them.
14. **There is no Red Stone job ID column on the board.** It records `Campaign / List`
    but not the mail batch, so a lead cannot be tied to the job that mailed it. The
    scaffold board `18421418228` in the Twin Home Buyer workspace does have a
    *Mail Batch (Redstone Job ID)* column, but it holds 1 empty item.
15. **Two items name a mail format that contradicts their `Source`** — 33025 Wildomar Rd
    is `Direct Mail (Checks)` with campaign `Postcard`, and one unnamed item is
    `Direct Mail (Checks)` with campaign `Free Inspection Report EQT (Letter)`.
16. **236 of 452 items are named `No property address`.**

## Open questions

1. **Costing per source** — sending them per source and per month drops them straight
   into the registry.
2. **`THB_Letter_01` is currently excluded from postcard KPIs.** It sits on the same
   Red Stone board but is a letter, not a postcard, at $1.1189 per piece against the
   $0.4894 postcard rate — and the build prompt says letters are not to be promoted.
   Confirm, or say the word and it counts inside Postcard.
3. ~~`Relaunch_12` has no mail date.~~ **Settled** — filled in on the board as
   2026-08-04, so its 3,171 pieces now sit in August. No job is attributed by upload
   date any more.
4. **The ROI example in the prompt does not reconcile with the stated formula.**
   §5 defines ROI as `(Net Revenue − Marketing Cost) ÷ Marketing Cost × 100`. The §8
   worked example shows spend $42,580 and net revenue $268,994 against ROI 645.40%,
   but that formula yields 531.5% (and `Net ÷ Cost` yields 631.7%). The stated formula
   is implemented. Confirm which is intended.
5. **Postcard response attribution** — see data-quality item 8.
6. **Check costing is still missing.** Checks are the largest mail format on both
   sources — 1,298 calls in the logs and 282 of the 452 board items, including 2 of the
   3 acquisitions and both cancelled contracts — and the Red Stone board has no check
   jobs, so `Direct Mail - Check` shows no piece count, no cost and no cost ratios.
7. **Which mail spend belongs to which acquisition?** Needed before Cost Per
   Acquisition can be produced — see the note under *Not reported*.
8. **Should the board's `Lead Stage` or its group win** where they disagree, as on
   33025 Wildomar Rd? Both are shown for now.
9. **Revenue on the two check acquisitions.** The board has it for the Petaluma postcard
   deal ($35,918.75) and that is reported; 8227 Ney Ave and 1932 Chestnut Ave are blank,
   so the revenue shown covers 1 of 3 acquisitions. Filling those two in makes the
   revenue figure complete.
10. **Deal costs** — purchase price, rehab, holding, closing — would turn the gross
    revenue into Net Revenue, and with the answer to question 7 would produce ROI.

## Repository

Single file by design for Phase 1, so the prototype can be opened and reviewed with
no tooling. Phase 2 introduces a backend for OAuth and sync, at which point the
JavaScript splits into modules.
