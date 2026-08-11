# Monday + Google Sheets Sync

A Windows desktop application that reads a Monday.com board and keeps a Google
Sheet in step with it. One button does the work:

```
Open MondayGoogleSync.exe  →  Click REFRESH / CHECK FOR UPDATES  →  the sheet is updated
```

It connects to **Monday.com** and **Google Sheets** and nothing else. It only ever
*reads* from Monday.com, and only ever *writes* to the one worksheet tab named in
Settings.

---

## Contents

1. [What it does](#what-it-does)
2. [Download the built application](#download-the-built-application)
3. [Install for daily use](#install-for-daily-use)
4. [Google setup](#google-setup) — needed once per organisation
5. [Monday.com setup](#mondaycom-setup)
6. [First-time configuration](#first-time-configuration)
7. [Daily use](#daily-use)
8. [How duplicates are prevented](#how-duplicates-are-prevented)
9. [Where your files are](#where-your-files-are)
10. [Security](#security)
11. [Building the .exe](#building-the-exe)
12. [Running from source](#running-from-source)
13. [Testing](#testing)
14. [Project layout](#project-layout)
15. [Troubleshooting](#troubleshooting)

---

## What it does

Pressing **Refresh** runs this sequence, showing each stage as it goes:

| Stage shown on screen | What happens |
|---|---|
| `Verifying Monday.com connection...` | the stored credential is checked, and renewed if it can be |
| `Verifying Google Sheets connection...` | same for Google |
| `Connecting to Monday.com...` | the configured board is opened, proving it still exists |
| `Reading Monday.com updates...` | items changed since the last successful sync are fetched |
| `Checking Google Sheets...` | the worksheet is read, header row included |
| `Comparing records...` | each item is decided: new, changed, or already in step |
| `Updating Google Sheets...` | new rows appended, changed rows updated |
| `Sync Complete` | the checkpoint is saved and the results are shown |

Afterwards you get a summary:

```
SYNC COMPLETE
New Monday.com Items:      3
Updated Monday.com Items:  5
Google Sheet Rows Added:   3
Google Sheet Rows Updated: 5
Duplicates Skipped:        2
Errors:                    0
Last Checked:              August 11, 2026 - 3:08 PM
```

If nothing changed it says **No new updates found**. That is a normal result, not
an error.

---

## Download the built application

You do not need Python, or this source code, to run the app. Every build is
produced on a Windows machine by GitHub Actions and attached to the run:

1. Open <https://github.com/juantwinhomes/Call-Logs-KPI/actions/workflows/build-windows-exe.yml>
2. Click the most recent green run.
3. Scroll to **Artifacts** at the bottom and download one of:

   | Artifact | What it is |
   |---|---|
   | `MondayGoogleSync-windows-folder` | a zip — unzip anywhere and run `MondayGoogleSync.exe` inside. **Recommended**: starts faster, and a missing file is visible rather than mysterious. |
   | `MondayGoogleSync-windows-single-exe` | one single `.exe`. Simplest to hand around; slower to start because it unpacks itself each launch. |

You must be signed in to GitHub to download an artifact — that is a GitHub rule,
not a choice this project made. If a run was started by hand with
**Run workflow**, the same two downloads are also published under
[Releases](https://github.com/juantwinhomes/Call-Logs-KPI/releases), which needs
no sign-in.

Windows SmartScreen will warn the first time, because the executable is not
code-signed: click **More info → Run anyway**. To remove the warning for good,
sign the `.exe` with your organisation's certificate (see
[Building the .exe](#building-the-exe)).

Nothing is published unless it passes: the workflow runs the full test suite,
launches the built `.exe` to confirm it starts and creates its database, and
checks its log for anything credential-shaped. Any of those failing fails the
build.

---

## Install for daily use

1. Copy the whole `MondayGoogleSync` folder from the download (or from `dist\`
   if you built it yourself) to the computer, for
   example to `C:\Apps\MondayGoogleSync`. The `.exe` needs the files beside it.
2. Put `client_secrets.json` in `%LOCALAPPDATA%\MondayGoogleSync\`
   (see [Google setup](#google-setup)). Create the folder if it does not exist.
3. Make a Start Menu or desktop shortcut to `MondayGoogleSync.exe`.
4. Launch it and connect the two accounts.

No Python, Command Prompt or PowerShell is needed to run it.

---

## Google setup

Done once for the organisation. Someone with access to the Google Cloud console
needs about ten minutes.

1. Go to <https://console.cloud.google.com/> and create a project, or pick an
   existing one — for example `Monday Sheets Sync`.
2. **APIs & Services → Library** and enable **two** APIs:
   - **Google Sheets API** — reading and writing the spreadsheet.
   - **Google Drive API** — listing folder and file *names* so staff can browse
     to their spreadsheet instead of pasting a link. Skip it and everything still
     works; only the **Browse Google Drive** button stops working, and the app
     says so plainly rather than failing oddly.
3. **APIs & Services → OAuth consent screen**
   - User type: **Internal** if you use Google Workspace, otherwise **External**.
   - App name: `Monday + Google Sheets Sync`, and fill in the support email.
   - Scopes: add these two, which are the only ones the app ever asks for:
     - `https://www.googleapis.com/auth/spreadsheets`
     - `https://www.googleapis.com/auth/drive.metadata.readonly`
   - If you chose **External**, add each member of staff under **Test users**, or
     publish the app.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: `MondayGoogleSync desktop`
   - **Create**, then **Download JSON**.
5. Rename the downloaded file to `client_secrets.json` and place it in
   `%LOCALAPPDATA%\MondayGoogleSync\` on each machine that will run the app.
   A read-only network share works too — point `GOOGLE_CLIENT_SECRETS_FILE` at it
   in a `.env` file beside the `.exe`.

The About screen inside the app shows the exact folder, and has a button to open
it.

Finally, **share the spreadsheet with the Google account each person will connect
with, giving it Editor access.** The app cannot write to a sheet the account can
only view.

### Why these two scopes

**`spreadsheets`** allows reading and writing the spreadsheets the signed-in
account can already reach. The narrower `drive.file` scope only covers files the
app itself created, so it cannot open your existing tracker; the broader `drive`
scope would hand over every file in the account. `spreadsheets` is the least
privilege that does the job.

**`drive.metadata.readonly`** allows listing *names* — folders, and which files
are spreadsheets. It carries no ability to read file contents at all. The app can
see that a document called "Payroll 2026" exists; it cannot see a single cell of
it. That is what makes the folder browser possible without asking for access to
everything in the account.

Neither `drive` nor `drive.readonly` is ever requested, and there is a test that
fails the build if one ever is.

---

## Monday.com setup

There are two ways to connect, and the app supports both.

### Personal API token (recommended, no setup)

1. In Monday.com, click your avatar (bottom left) → **Developers**.
2. **My access tokens** → **Show** → copy the token.
3. In the app: **Connect Monday.com**, then paste the token.

The token is checked immediately, stored encrypted, and never displayed again.
It carries that person's own permissions, so they can only sync boards they can
already see.

### Browser sign-in (optional)

If your organisation prefers OAuth, register a Monday app with the
`boards:read` and `me:read` scopes and a redirect URI of
`http://127.0.0.1:8731/monday/callback`, then put its credentials in a `.env`
file beside the executable:

```
MONDAY_CLIENT_ID=...
MONDAY_CLIENT_SECRET=...
```

The Connect button will then offer browser sign-in as well as the token option.

---

## First-time configuration

Open **Settings**:

**Monday.com**
1. **Load workspaces**, pick one (or leave *All workspaces*).
2. **Load boards**, pick the board. The Board ID fills itself in; you can also
   type one directly.
3. **Load columns**.

**Google Sheets**
4. Press **Browse Google Drive...** and find your spreadsheet by folder. The
   left-hand list holds the four places a sheet can live — **My Drive**,
   **Shared with me**, **Starred** and **Shared drives** — and the trail across
   the top walks back out of a folder. Double-click a folder to go in, a
   spreadsheet to choose it. Search finds a sheet by name anywhere the account
   can see, which beats clicking through a deep tree.

   Or paste the spreadsheet link from your browser's address bar into the box
   below and press **Open spreadsheet**. The app pulls the ID out of the URL.
5. Choose the worksheet tab.

**Column mappings**
6. Tick each Monday column you want copied, and give it the heading it should
   live under in the sheet. **Suggest headings** fills in sensible names for the
   blanks. A heading that does not exist yet is created for you.

   A typical map:

   | Monday.com column | Google Sheets heading |
   |---|---|
   | Item Name | Property Name |
   | Status | Status |
   | Owner | Assigned To |
   | Date | Due Date |
   | Monday Item ID *(required)* | Monday Item ID |

**When Refresh finds changes**
7. Leave **Automatically write detected updates to Google Sheets** switched
   **off** to begin with. Refresh will then list what it found and wait for you
   to press **Apply Updates**. Turn it on once you trust it.

8. **Save Settings**. This is remembered, so it only needs doing once.

---

## Daily use

1. Open `MondayGoogleSync.exe`.
2. Check both connections read **Connected**.
3. Press **REFRESH / CHECK FOR UPDATES**.
4. Read the summary.

The button disables itself while a refresh is running, so a double click cannot
start two syncs. Opening the application a second time brings the existing window
forward and tells you it is already running, for the same reason.

Opening the app never changes anything on its own. The Refresh button is the only
thing that writes.

---

## How duplicates are prevented

The worksheet carries five columns the app maintains:

| Column | Purpose |
|---|---|
| `Monday Board ID` | which board the row came from |
| `Monday Item ID` | **the key** — how a row is recognised again |
| `Last Monday Modified Date` | the item's `updated_at` when it was last written |
| `Last Synced Date` | when the app last touched the row |
| `Sync Status` | `Synced` |

On every refresh, for each changed Monday item:

- **`Monday Item ID` not in the sheet** → append a new row.
- **In the sheet, and a monitored value differs** → update that row in place.
- **In the sheet, and nothing differs** → skip it.

Two consequences worth knowing:

- **Only mapped columns and tracking columns are rewritten.** Anything else in
  the row — a notes column somebody keeps by hand — is carried through untouched.
- **A second row with the same `Monday Item ID` is left alone** and counted under
  *Duplicates Skipped*, with a warning. The app will not guess which of two
  copies is the real one.

The **checkpoint** — the timestamp the next refresh reads from — is only saved
after a refresh in which nothing failed, and is deliberately rewound by a minute
so an edit made while the sync was running cannot slip through the gap. If the
application is closed halfway through, the items already written stay written and
the rest are simply picked up next time.

---

## Where your files are

Everything writable lives under `%LOCALAPPDATA%\MondayGoogleSync\`:

| File | Contents |
|---|---|
| `sync_state.sqlite3` | settings, per-item sync state, run history, error history |
| `credentials.enc` | the encrypted tokens |
| `keyfile.bin` | only if the OS credential store is unavailable — see below |
| `logs\app.log` | the technical log, rotated at 2 MB, five kept |
| `client_secrets.json` | the Google OAuth client you placed there |

The About screen lists these paths and can open the folder.

---

## Security

- **No passwords are stored.** Monday.com uses an API token or OAuth; Google uses
  OAuth. Neither flow gives the application your password.
- **Tokens are encrypted at rest** with Fernet (AES-128-CBC with an HMAC). The
  key lives in the Windows Credential Manager via `keyring`. Where no credential
  store is available the key falls back to a file with owner-only permissions,
  and that downgrade is written to the log — a key beside its ciphertext protects
  a copied file, not a user who can read the folder.
- **Nothing secret is written to the log.** Every log record passes through a
  redaction filter that scrubs tokens, refresh tokens, client secrets, API keys,
  `Authorization` headers and anything shaped like a JWT. There are tests for
  this.
- **Nothing secret reaches the screen.** Errors carry two messages: a plain
  sentence for the user and a technical detail that only goes to the log.
- **No credentials in the source.** Client identifiers come from
  `client_secrets.json` or the environment. There is nothing to leak in this
  repository.
- **Least privilege.** `boards:read` and `me:read` on Monday.com, and the single
  `spreadsheets` scope on Google. The app cannot modify your Monday board at all.

---

## Building the .exe

On a Windows machine with Python 3.10 or newer on PATH:

```bat
cd monday_google_sync
build.bat
```

That creates the virtual environment, installs dependencies, runs the tests and
produces:

```
dist\MondayGoogleSync\MondayGoogleSync.exe
```

Hand over the **whole `dist\MondayGoogleSync` folder** — the executable needs the
files beside it.

Other options:

```bat
build.bat onefile      REM one single .exe, slower to start
build.bat clean        REM delete build output
```

The one-folder build is recommended for production: it starts faster, and when
something is missing it is visible rather than mysterious. No Command Prompt
window appears when the application opens, in either shape.

### Notes

- UPX compression is deliberately off. It saves a few megabytes and triggers
  antivirus far more often than it is worth.
- Qt modules the app does not use (WebEngine, Quick, Multimedia, 3D, Charts) are
  excluded, which keeps the build to a sensible size.
- To sign the executable, run `signtool` against
  `dist\MondayGoogleSync\MondayGoogleSync.exe` after the build. An unsigned
  executable will show a SmartScreen warning the first few times it is run on a
  new machine.

---

## Running from source

```bash
cd monday_google_sync
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
python main.py
```

The application works on macOS and Linux too — the packaging is Windows-specific,
the code is not.

---

## Testing

```bash
python -m pytest tests -q
```

129 tests, no network access required. They cover:

- **The sync engine** against in-memory Monday and Sheets doubles: first run,
  repeat runs, change detection, five consecutive refreshes producing no
  duplicates, a cleared checkpoint producing no duplicates, unrelated columns
  surviving an update, duplicate `Monday Item ID` rows being reported rather than
  rewritten, hand-entered rows being left alone, the checkpoint not advancing
  after a failed write, and the failed item being retried on the next run.
- **Preview mode**: nothing written, then a reviewed plan applied.
- **Log redaction**, with real log records written to a file and inspected.
- **The encrypted store**, including that the token is not present in the file on
  disk, and that a credential store which accepts writes but keeps nothing falls
  back to a key file instead of silently corrupting itself.
- **Error mapping**: every HTTP status to the right class and a readable
  sentence, with the technical detail kept out of the user-facing message.
- **Drive folder browsing** against an in-memory Drive: folder contents, the
  folder-before-file ordering, subfolder navigation, non-spreadsheet files being
  hidden, trashed items excluded, Shared with me / Starred / Shared drives,
  shortcuts resolving to their target, the breadcrumb path, a malformed parent
  chain not looping forever, search, and an apostrophe in a name not breaking the
  query.
- **The scope upgrade path**: an old connection that predates folder browsing is
  detected, asks for a reconnect in plain words, and keeps syncing meanwhile. A
  test also fails the build if the full Drive scope is ever requested.
- **Diagnostics**: every failing check carries an instruction, a missing scope is
  a warning rather than a failure, and the report survives the log redaction
  filter unchanged.
- **Two threading regressions**, each with a test: a task result must arrive on
  the GUI thread, and a task must not be lost to garbage collection.
- **The GUI**, built headless: every page renders, the Refresh button stays
  disabled until both services are connected and the configuration is complete,
  the locked button reads `CHECKING FOR UPDATES...`, the result panel fills in
  from a real sync result, "no updates" is not shown as an error, and the review
  dialog lists the right changes.

Manual checks worth doing before rollout, since they need real accounts:

1. Connect both services on a fresh machine and confirm the statuses.
2. Press Refresh with Preview mode on; confirm the counts match the board and
   that the spreadsheet is untouched.
3. Press Apply Updates; confirm the rows appear with the tracking columns filled.
4. Press Refresh again; confirm **No new updates found** and no new rows.
5. Change a status in Monday.com, refresh, and confirm exactly that cell changes.
6. Add a note in an unmapped column, change the same item in Monday, refresh, and
   confirm your note survives.
7. Turn off the network, refresh, and confirm a readable message rather than a
   crash.
8. Rename the worksheet tab, refresh, and confirm the app says the tab is missing
   and points at Settings.
9. Launch the `.exe` twice and confirm the second instance says it is already
   running.

---

## Project layout

```
monday_google_sync/
├── main.py                     entry point, single-instance guard, startup order
├── ui/
│   ├── main_window.py          dashboard, connections, refresh, results, navigation
│   ├── settings_window.py      board, spreadsheet and column mapping
│   ├── sync_history.py         recent runs and recent errors
│   ├── logs_view.py            app.log viewer
│   ├── about.py                versions, file locations, app-update check
│   ├── preview_dialog.py       Review Changes before writing
│   ├── drive_picker.py         browse Drive by folder and pick a spreadsheet
│   ├── diagnostics_dialog.py   Check my setup
│   ├── widgets.py              cards, metrics, status pills, connection rows
│   ├── workers.py              background threads
│   └── style.py                the stylesheet
├── services/
│   ├── auth_service.py         Monday token/OAuth, Google OAuth, token refresh, scopes
│   ├── monday_service.py       GraphQL client, read only
│   ├── google_sheets_service.py Sheets read, append, update in place
│   ├── google_drive_service.py folder browsing, metadata only
│   ├── sync_service.py         plan / apply, the comparison rules
│   ├── diagnostics.py          the Check my setup report
│   └── errors.py               one error type, user sentence and technical detail
├── database/
│   ├── database.py             SQLite connection and migrations
│   └── models.py               settings, synced items, run history, error log
├── utils/
│   ├── config.py               paths, constants, environment
│   ├── logger.py               rotating log with credential redaction
│   └── security.py             encrypted credential store
├── tests/                      129 tests, no network needed
├── resources/app.ico
├── requirements.txt
├── .env.example
├── MondayGoogleSync.spec
├── build.bat
└── README.md
```

Authentication, API access, the user interface, the database and the
synchronisation rules are each in their own place. `sync_service.py` is the only
module that decides what to write, and it does not know how to make an HTTP
request; the API clients know how to talk to their services and nothing about the
rules.

---

## Troubleshooting

**"Your Google authentication has expired. Please reconnect your account."**
The refresh token was revoked or has gone stale — someone removing the app under
their Google Account's third-party access will do it. Press
**Reconnect Google Sheets**.

**"This Google account does not have permission to edit that spreadsheet."**
Share the spreadsheet with the connected account as an **Editor**.

**"The Google Sheets API is not enabled for this project."**
Step 2 of [Google setup](#google-setup).

**"Google Sheets is not set up on this installation yet."**
`client_secrets.json` is missing. The About screen shows where it belongs.

**"That Monday.com board no longer exists, or this account cannot see it."**
The board was deleted or renamed, or the token belongs to someone without access.
Choose the board again in Settings.

**"The worksheet tab ... no longer exists in that spreadsheet."**
The tab was renamed or deleted. Pick it again in Settings.

**"Monday.com is limiting requests right now."**
Monday's per-minute complexity budget. The app already backs off and retries;
wait a minute and press Refresh again.

**Duplicates Skipped is not zero.**
The worksheet has more than one row carrying the same `Monday Item ID`. The app
keeps the first up to date and leaves the copies alone. Delete the extra rows by
hand and the count returns to zero.

**Rows are not updating even though Monday changed.**
The changed column may not be mapped — only mapped columns are compared. Check
the mappings in Settings, or use **Re-check every item on the next refresh** to
force a full comparison.

**A refresh went wrong and I need to know why.**
**Logs** shows `app.log`, and **Sync History** lists every run with its technical
error. Neither contains credentials, so both are safe to send on.

**Browse Google Drive says it needs an extra permission.**
The connection was made before folder browsing existed and carries only the
Sheets scope. Press **Reconnect Google Sheets** and approve the request. Syncing
works fine without it — only the Browse button needs it — and pasting a link
needs nothing new.

**"The Google Drive API is not enabled for this project."**
Step 2 of [Google setup](#google-setup) — enable the Drive API alongside the
Sheets API. Or ignore it and paste spreadsheet links instead.

**The folder browser is empty, or a spreadsheet I can see in Drive is missing.**
It only lists folders and Google Sheets; other files are hidden deliberately.
If the sheet lives in someone else's Drive, look under **Shared with me** rather
than **My Drive**, or use search.

**Something is wrong and I do not know what.**
Press **Check my setup** on the dashboard. It walks every prerequisite in order
and says which one is failing and what to do about it. **Copy to clipboard** or
**Save as a text file** produces a report with no credentials in it, safe to send
to whoever supports the app.

**Everything looks broken after moving machines.**
Credentials are encrypted with a key held in that machine's credential store, so
`credentials.enc` does not travel. Reconnect both services on the new machine.
Settings and history do travel: copy `sync_state.sqlite3`.
