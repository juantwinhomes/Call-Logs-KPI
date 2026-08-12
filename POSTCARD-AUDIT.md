# Direct Mail — Postcard pipeline: accuracy audit

Scope: the **Direct Mail — Postcard** pipeline only. Nothing else on the
dashboard was changed or reviewed.

Sources checked:

- `index.html` on branch `claude/google-sheet-access-iqbkcb` (the dashboard)
- `DM_BOARD` — monday.com board "📬 Direct Mail & Postcard Leads" (452 items,
  read 2026-08-11), embedded in `index.html`
- `REAL` — the call-log dataset embedded in `index.html` (5,201 records)
- The 32 weekly workbooks in the 2026 Call Logs Drive folder

---

## Verdict: the postcard numbers are correct

Every tile on the Postcard pipeline reconciles exactly to its source.

| Tile | Shows | Source | Reconciles |
|---|---|---|---|
| Direct Mail Leads On The Board (Postcard) | 169 | board items with `Source = Direct Mail (Postcard)` | yes |
| Dead / Not Interested From Postcard | 97 | board `Lead Stage = Dead` | yes |
| Acquired Properties From Postcard | 1 | board `Lead Stage = Acquired` | yes |
| Still Under Contract From Postcard | 0 | board `Lead Stage = Under Contract` | yes |
| Cancelled Contracts From Postcard | 0 | board `Lead Stage = Cancelled Contract` | yes |
| Appointment Booked From Postcard | 15 | call logs, `Call Disposition = "Appointment Booked (H)"` | yes |
| Revenue From Postcard | $35,919 | board revenue on the one acquisition | yes |

The board's 169 postcard items partition cleanly by Lead Stage:

```
Dead        97
Contacted   39
New         20
Interested  10
Appointment  2
Acquired     1
           ---
           169
```

The classifier is sound. `index.html:430` states the rule and
`index.html:459` implements it:

```js
[/\bpost ?cards?\b/, "Direct Mail", "Direct Mail - Postcard", "postcard"]
```

Classification is by **campaign**, deliberately ignoring the sheets' Lead
Source column. That is the right call — the sheets' Lead Source column is
unreliable (it carries `Postcard` and `Direct Mail` as sibling values, and in
places contains phone numbers), and the classifier is immune to all of it. It
also matches `Post Card` written as two words.

Monthly postcard call volumes behind the Trends charts:

```
Jan  9   Feb 39   Mar 123   Apr 61
May 24   Jun 89   Jul  98   Aug 36     (479 total)
```

---

## Open items

These are not miscounts. Each is a decision nobody has recorded yet.

### 1. The Appointment tile counts a different thing from the tiles beside it

Six tiles in the row count **properties on the monday.com board**. `apptBooked`
counts **calls in the Google Sheets** (`index.html:871`):

```js
if (r.disposition === "Appointment Booked (H)") a.apptBooked++;
```

So "15" is fifteen phone calls sitting in a row of property counts — a
different unit, from a different system, over a different population. One lead
that books twice counts twice.

`index.html:1011` explains the reasoning, and a note on screen covers it, but
the tile is visually identical to its neighbours. This is why the row does not
sum to 169 and never can.

**Decision needed:** keep it as a call count with clearer labelling, or switch
it to a board-based property count.

### 2. Only one appointment disposition string is counted

The match is exact. Postcard rows also carry:

| Disposition | Calls | Counted |
|---|---|---|
| `Appointment Booked (H)` | 15 | yes |
| `Confirmed Appointment` | 3 | no |
| `Pending Appointment` | 2 | no |
| `Appointment Confirmed` | 1 | no |

`Confirmed Appointment` and `Appointment Confirmed` are almost certainly the
same thing entered two ways. Whether either should count depends on whether
they mean a *new* booking or a confirmation of one already counted — counting
a confirmation would double-count the booking.

**Decision needed:** which of these four strings represent a booked
appointment. If the two "Confirmed" variants count, the tile reads 19.

### 3. The board's Source and Campaign fields disagree on 120 of 169 items

Of the 169 items sourced `Direct Mail (Postcard)`, the Campaign field says:

| Campaign | Items |
|---|---|
| *(blank)* | 44 |
| Liens | 36 |
| Ugly House | 27 |
| Tax Delinquent | 9 |
| High Equity | 2 |
| 70% Distress | 1 |
| NOD / Foreclosure | 1 |
| Postcard\* (any postcard campaign) | 49 |

The two halves of the dashboard use **opposite rules**: the call-log side
classifies by campaign, the board side classifies by source. They agree today
by coincidence, not design.

This is a live hazard. Aligning the board to campaign-based logic — the rule
used everywhere else — would drop postcard from **169 to 49**.

**Decision needed:** on the board, is Source or Campaign authoritative for
format?

### 4. A cancelled postcard contract is filed under Checks

`33025 Wildomar Rd, Lake Elsinore, CA 92530`:

- Campaign: `Postcard`
- Source: `Direct Mail (Checks)` ← attributed to Check, not Postcard
- Lead Stage: `Cancelled Contract`
- Board group: `📝 Under Contract` ← conflicts with its own stage

So "Cancelled Contracts From Postcard = 0" understates if this is a postcard
deal. The stage/group conflict is already detected by
`DM_STAGE_GROUP_CONFLICT` (`index.html:422`) but does not reach the tile.

**Decision needed:** is this a postcard deal or a check deal, and which of
stage/group is right?

---

## Separate: repository state

- All five release tags (`v1.0.0` … `v1.3.1`, including "Twin Call Tracker
  v1.3.1") point at commit `9187edd`, which contains only `README.md` and
  `.gitignore`. **The releases ship no application.**
- The dashboard exists solely on the unmerged branch
  `claude/google-sheet-access-iqbkcb`. `main` has no code. If that branch is
  deleted, the work is gone.

---

## Data hygiene, low priority

Neither affects the tile figures — the classifier tolerates both.

- **Campaign name variants.** One South Bay campaign appears as
  `Postcard Ugly Houses - South Bay (669)`, `Postcard Ugly Houses- South Bay
  (669)`, `Postcard Ugly Houses- SB(669)` and `Postcard Ugly Houses- South
  Bay(669)`. All four still classify as postcard, but per-campaign breakdowns
  split them.
- **The one acquisition is recorded under four names.** Call logs carry
  `Dennis Marks`, `Marks, Dennis` and `Marks, Denise`; the board says
  `Denise Marks`. Counting is unaffected (unique callers key on the last ten
  phone digits), but name lookups will miss.
- **June 15–18 in the Week 3 workbook.** In that block the Date and Start Time
  cells came through as raw serial values (`46188`–`46191`, times as
  `0.4604166667`) rather than as dates, unlike every other sheet. Worth
  confirming the cell formatting in that tab.

---

## Method note

An independent re-parse of the 32 workbooks was attempted as a cross-check. It
is **not** a reliable second opinion and none of its figures are quoted above:
the Drive text export truncates large files mid-row — 12 of 32 workbooks were
cut short — so counts taken from it are undercounts. An earlier claim from that
parse, that June 16–19 held no call log, was wrong; those days are present in
the workbook and were simply beyond the truncation point.

Figures in this document come from the dashboard's own embedded datasets
(`DM_BOARD`, `REAL`) and from the board itself, which are complete.
