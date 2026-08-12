"""Tests for Drive folder browsing, the scope upgrade path, and diagnostics."""
from __future__ import annotations

import pytest

from services.google_drive_service import (ROOT_MY_DRIVE, ROOT_SHARED_DRIVES,
                                           ROOT_SHARED_WITH_ME, GoogleDriveService,
                                           _escape, _to_item)
from utils.config import (GOOGLE_SCOPE_DRIVE_METADATA, GOOGLE_SCOPE_SHEETS, MIME_FOLDER,
                          MIME_SHORTCUT, MIME_SPREADSHEET)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeGoogleAuth:
    """Just enough of GoogleAuth for the Drive service: a scope list."""

    def __init__(self, scopes: list[str] | None = None) -> None:
        self._scopes = list(scopes if scopes is not None
                            else [GOOGLE_SCOPE_SHEETS, GOOGLE_SCOPE_DRIVE_METADATA])

    def scopes(self) -> list[str]:
        return list(self._scopes)

    def has_scope(self, scope: str) -> bool:
        return scope in self._scopes

    def missing_scopes(self) -> list[str]:
        from utils.config import GOOGLE_SCOPES
        return [s for s in GOOGLE_SCOPES if s not in self._scopes]

    def credentials(self):
        return object()

    def check(self):
        from services.auth_service import ConnState, ConnStatus
        return ConnStatus(ConnState.CONNECTED, "tester@example.com", "")


class FakeDriveApi:
    """Stands in for the googleapiclient Drive resource.

    Holds a tree of items and answers files().list() by interpreting the subset of
    the Drive query language the service actually sends.
    """

    def __init__(self, items: list[dict], drives: list[dict] | None = None,
                 about: dict | None = None) -> None:
        self.items = items
        self.drives_list = drives or []
        self.about_payload = about if about is not None else {
            "user": {"emailAddress": "tester@example.com", "displayName": "Tester"}}
        self.queries: list[str] = []

    # -- the shape googleapiclient exposes ---------------------------------- #
    def files(self):
        return self

    def drives(self):
        return _DrivesEndpoint(self)

    def about(self):
        return _AboutEndpoint(self)

    def list(self, **kw):
        self.queries.append(kw.get("q", ""))
        return _Request(self._answer(kw.get("q", "")))

    def get(self, **kw):
        wanted = kw.get("fileId")
        for it in self.items:
            if it["id"] == wanted:
                return _Request(dict(it))
        from googleapiclient.errors import HttpError
        raise HttpError(_Resp(404), b"not found")

    # -- query interpretation ----------------------------------------------- #
    def _answer(self, q: str) -> dict:
        out = []
        for it in self.items:
            if "trashed = false" in q and it.get("trashed"):
                continue
            if "sharedWithMe" in q:
                if not it.get("sharedWithMe"):
                    continue
            elif "starred = true" in q:
                if not it.get("starred"):
                    continue
            elif "in parents" in q:
                parent = q.split("'")[1]
                if parent not in (it.get("parents") or []):
                    continue
            if "name contains" in q:
                needle = q.split("name contains '")[1].split("'")[0].lower()
                if needle not in it["name"].lower():
                    continue
            if "mimeType = '" in q:
                allowed = {seg.split("'")[0] for seg in q.split("mimeType = '")[1:]}
                if it["mimeType"] not in allowed:
                    continue
            out.append(dict(it))
        return {"files": out}


class _DrivesEndpoint:
    def __init__(self, api: FakeDriveApi) -> None:
        self.api = api

    def list(self, **_kw):
        return _Request({"drives": list(self.api.drives_list)})


class _AboutEndpoint:
    def __init__(self, api: FakeDriveApi) -> None:
        self.api = api

    def get(self, **kw):
        self.api.queries.append("about:" + str(kw.get("fields", "")))
        return _Request(dict(self.api.about_payload))


class _Request:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self, **_kw) -> dict:
        return self.payload


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "test"


def drive_with(items, drives=None, scopes=None) -> GoogleDriveService:
    svc = GoogleDriveService(FakeGoogleAuth(scopes))
    svc._svc = FakeDriveApi(items, drives)
    return svc


def folder(fid, name, parents=None, **extra):
    return {"id": fid, "name": name, "mimeType": MIME_FOLDER,
            "parents": parents or [], "modifiedTime": "2026-08-01T10:00:00Z", **extra}


def sheet(fid, name, parents=None, **extra):
    return {"id": fid, "name": name, "mimeType": MIME_SPREADSHEET,
            "parents": parents or [], "modifiedTime": "2026-08-02T10:00:00Z",
            "owners": [{"displayName": "Juan"}], **extra}


# --------------------------------------------------------------------------- #
# Listing a folder
# --------------------------------------------------------------------------- #

def test_a_folder_lists_its_subfolders_and_spreadsheets():
    svc = drive_with([
        folder("f1", "2026 Call Logs", ["root"]),
        folder("f2", "Archive", ["root"]),
        sheet("s1", "Lead Tracker", ["root"]),
        sheet("s2", "Buried deeper", ["f1"]),
    ])
    listing = svc.list_folder("root")

    assert [f.name for f in listing.folders] == ["2026 Call Logs", "Archive"]
    assert [s.name for s in listing.sheets] == ["Lead Tracker"]
    assert not listing.is_empty


def test_folders_sort_before_spreadsheets_and_each_sorts_by_name():
    svc = drive_with([
        sheet("s1", "zebra", ["root"]), sheet("s2", "apple", ["root"]),
        folder("f1", "Yellow", ["root"]), folder("f2", "blue", ["root"]),
    ])
    items = svc.list_folder("root").all_items()

    assert [i.name for i in items] == ["blue", "Yellow", "apple", "zebra"]


def test_going_into_a_subfolder_shows_only_its_contents():
    svc = drive_with([
        folder("f1", "2026 Call Logs", ["root"]),
        sheet("s1", "top level", ["root"]),
        sheet("s2", "inside", ["f1"]),
    ])
    inner = svc.list_folder("f1")

    assert [s.name for s in inner.sheets] == ["inside"]
    assert not inner.folders


def test_documents_and_other_files_are_not_offered():
    """A folder full of PDFs should look empty to a spreadsheet picker."""
    svc = drive_with([
        {"id": "d1", "name": "Contract.pdf", "mimeType": "application/pdf", "parents": ["root"]},
        {"id": "d2", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
         "parents": ["root"]},
    ])
    listing = svc.list_folder("root")

    assert listing.is_empty


def test_trashed_items_are_excluded():
    svc = drive_with([sheet("s1", "live", ["root"]),
                      sheet("s2", "deleted", ["root"], trashed=True)])
    assert [s.name for s in svc.list_folder("root").sheets] == ["live"]


def test_shared_with_me_is_a_place_you_can_open():
    svc = drive_with([sheet("s1", "From accounting", [], sharedWithMe=True),
                      sheet("s2", "My own", ["root"])])
    listing = svc.list_folder(ROOT_SHARED_WITH_ME)

    assert [s.name for s in listing.sheets] == ["From accounting"]


def test_starred_is_a_place_you_can_open():
    svc = drive_with([sheet("s1", "Important", ["root"], starred=True),
                      sheet("s2", "Ordinary", ["root"])])
    assert [s.name for s in svc.list_folder("__starred__").sheets] == ["Important"]


def test_shared_drives_appear_as_top_level_folders():
    svc = drive_with([], drives=[{"id": "dr1", "name": "Operations"},
                                 {"id": "dr2", "name": "Acquisitions"}])
    listing = svc.list_folder(ROOT_SHARED_DRIVES)

    assert [f.name for f in listing.folders] == ["Acquisitions", "Operations"]
    assert all(f.is_shared_drive and f.is_folder for f in listing.folders)


# --------------------------------------------------------------------------- #
# Shortcuts
# --------------------------------------------------------------------------- #

def test_a_shortcut_to_a_spreadsheet_is_offered_as_the_spreadsheet():
    """Shared trackers are often reached through a shortcut."""
    raw = {"id": "sc1", "name": "Lead Tracker (shortcut)", "mimeType": MIME_SHORTCUT,
           "parents": ["root"],
           "shortcutDetails": {"targetId": "real123", "targetMimeType": MIME_SPREADSHEET}}
    item = _to_item(raw)

    assert item is not None
    assert item.is_spreadsheet
    assert item.id == "real123", "must point at the real file, not the shortcut"
    assert item.name == "Lead Tracker (shortcut)", "keeps the name the user sees in Drive"


def test_a_shortcut_to_a_folder_is_offered_as_a_folder():
    item = _to_item({"id": "sc2", "name": "Shared logs", "mimeType": MIME_SHORTCUT,
                     "shortcutDetails": {"targetId": "folder9", "targetMimeType": MIME_FOLDER}})
    assert item is not None and item.is_folder and item.id == "folder9"


def test_a_shortcut_to_something_irrelevant_is_dropped():
    assert _to_item({"id": "sc3", "name": "A PDF", "mimeType": MIME_SHORTCUT,
                     "shortcutDetails": {"targetId": "x", "targetMimeType": "application/pdf"}}) is None
    assert _to_item({"id": "sc4", "name": "Broken", "mimeType": MIME_SHORTCUT}) is None


# --------------------------------------------------------------------------- #
# Breadcrumb
# --------------------------------------------------------------------------- #

def test_the_breadcrumb_walks_from_my_drive_down_to_the_folder():
    svc = drive_with([
        folder("f1", "2026", ["root"]),
        folder("f2", "August", ["f1"]),
        folder("f3", "Week 2", ["f2"]),
    ])
    trail = svc.breadcrumb("f3")

    assert [i.name for i in trail] == ["My Drive", "2026", "August", "Week 2"]


def test_the_breadcrumb_of_a_pseudo_root_is_just_itself():
    svc = drive_with([])
    assert [i.name for i in svc.breadcrumb(ROOT_SHARED_WITH_ME)] == ["Shared with me"]
    assert [i.name for i in svc.breadcrumb(ROOT_MY_DRIVE)] == ["My Drive"]


def test_the_breadcrumb_cannot_loop_forever_on_a_cycle():
    """A malformed parent chain must not hang the dialog."""
    svc = drive_with([folder("a", "A", ["b"]), folder("b", "B", ["a"])])
    trail = svc.breadcrumb("a")
    assert 0 < len(trail) <= 4


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def test_search_finds_spreadsheets_anywhere_by_name():
    svc = drive_with([
        sheet("s1", "Lead Tracker 2026", ["root"]),
        sheet("s2", "Old lead notes", ["f1"]),
        sheet("s3", "Payroll", ["root"]),
        folder("f1", "Leads folder", ["root"]),
    ])
    found = svc.search_spreadsheets("lead")

    names = {i.name for i in found}
    assert names == {"Lead Tracker 2026", "Old lead notes"}
    assert all(i.is_spreadsheet for i in found), "search must not return folders"


def test_an_empty_search_returns_nothing_rather_than_everything():
    svc = drive_with([sheet("s1", "Anything", ["root"])])
    assert svc.search_spreadsheets("") == []
    assert svc.search_spreadsheets("   ") == []


@pytest.mark.parametrize("text,expected", [
    ("Juan's sheet", "Juan\\'s sheet"),
    ("back\\slash", "back\\\\slash"),
    ("plain", "plain"),
])
def test_a_quote_in_a_search_cannot_break_the_query(text: str, expected: str):
    assert _escape(text) == expected


def test_an_apostrophe_in_a_folder_name_is_escaped_in_the_query():
    svc = drive_with([sheet("s1", "x", ["root"])])
    svc.search_spreadsheets("Ana's")
    assert "Ana\\'s" in svc._svc.queries[-1]


# --------------------------------------------------------------------------- #
# The scope upgrade path
# --------------------------------------------------------------------------- #

def test_an_old_connection_without_the_drive_scope_cannot_browse():
    svc = GoogleDriveService(FakeGoogleAuth([GOOGLE_SCOPE_SHEETS]))
    assert not svc.available()


def test_browsing_without_the_scope_asks_for_a_reconnect_rather_than_failing_oddly():
    from services.errors import AuthError
    svc = GoogleDriveService(FakeGoogleAuth([GOOGLE_SCOPE_SHEETS]))

    with pytest.raises(AuthError) as caught:
        svc.list_folder("root")

    err = caught.value
    assert err.needs_reconnect
    assert "Reconnect" in err.message
    assert "paste a spreadsheet link" in err.message, "must offer the way around it"
    assert "drive.metadata" in err.detail and "drive.metadata" not in err.message


def test_a_connection_with_both_scopes_can_browse():
    svc = GoogleDriveService(FakeGoogleAuth([GOOGLE_SCOPE_SHEETS, GOOGLE_SCOPE_DRIVE_METADATA]))
    assert svc.available()


def test_the_drive_scope_is_metadata_only():
    """The app must never ask for the ability to read file contents."""
    assert GOOGLE_SCOPE_DRIVE_METADATA.endswith("drive.metadata.readonly")
    from utils.config import GOOGLE_SCOPES
    for scope in GOOGLE_SCOPES:
        assert not scope.endswith("/drive"), "the full Drive scope must never be requested"
        assert not scope.endswith("drive.readonly"), "content-read access is not needed"


def test_missing_scopes_reports_only_what_is_absent():
    assert FakeGoogleAuth([GOOGLE_SCOPE_SHEETS]).missing_scopes() == [GOOGLE_SCOPE_DRIVE_METADATA]
    assert FakeGoogleAuth([GOOGLE_SCOPE_SHEETS, GOOGLE_SCOPE_DRIVE_METADATA]).missing_scopes() == []


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #

def _settings_with_config(complete: bool):
    from database.models import ColumnMapping, SettingsStore, SyncConfig
    from utils.config import TRACK_ITEM_ID
    s = SettingsStore()
    if complete:
        SyncConfig(board_id="100", board_name="Board", spreadsheet_id="sid",
                   spreadsheet_name="Sheet", worksheet="Leads",
                   mappings=[ColumnMapping("__item_id__", "id", TRACK_ITEM_ID),
                             ColumnMapping("__name__", "Name", "Property Name")],
                   auto_write=False).save(s)
    return s


class _DiagAuth:
    def __init__(self, monday_state=None, google_state=None, scopes=None) -> None:
        from services.auth_service import ConnState, ConnStatus
        from utils.config import Environment
        self.env = Environment.load()
        self._m = ConnStatus(monday_state or ConnState.CONNECTED, "me@example.com", "")
        self._g = ConnStatus(google_state or ConnState.CONNECTED, "me@example.com", "")
        self._scopes = scopes if scopes is not None else [GOOGLE_SCOPE_SHEETS,
                                                          GOOGLE_SCOPE_DRIVE_METADATA]
        outer = self

        class _M:
            def check(self_inner):
                return outer._m

        class _G:
            def check(self_inner):
                return outer._g

            def missing_scopes(self_inner):
                from utils.config import GOOGLE_SCOPES
                return [s for s in GOOGLE_SCOPES if s not in outer._scopes]

        self.monday, self.google = _M(), _G()


def test_diagnostics_names_the_missing_configuration():
    from services.diagnostics import FAIL, run_diagnostics
    report = run_diagnostics(_DiagAuth(), _settings_with_config(False))

    config = next(c for c in report.checks if c.name == "Configuration")
    assert config.state == FAIL
    assert "no Monday.com board is chosen" in config.detail
    assert "Settings" in config.fix


def test_diagnostics_flags_a_disconnected_service_with_the_fix():
    from services.auth_service import ConnState
    from services.diagnostics import FAIL, run_diagnostics
    report = run_diagnostics(_DiagAuth(monday_state=ConnState.NOT_CONNECTED),
                             _settings_with_config(True))

    monday = next(c for c in report.checks if c.name == "Monday.com connection")
    assert monday.state == FAIL
    assert "access tokens" in monday.fix
    assert report.problems, "a disconnected service is a problem, not a warning"


def test_diagnostics_flags_an_old_connection_as_a_warning_not_a_failure():
    """Missing the Drive scope must not read as though the app is broken."""
    from services.diagnostics import WARN, run_diagnostics
    report = run_diagnostics(_DiagAuth(scopes=[GOOGLE_SCOPE_SHEETS]),
                             _settings_with_config(True))

    drive = next(c for c in report.checks if c.name == "Google Drive folder browsing")
    assert drive.state == WARN
    assert "Reconnect" in drive.fix
    assert "Syncing works without it" in drive.fix
    google = next(c for c in report.checks if c.name == "Google Sheets connection")
    assert google.state != WARN, "the sheets connection itself is fine"


def test_diagnostics_reports_the_missing_client_secrets_as_the_blocker():
    from services.diagnostics import FAIL, run_diagnostics
    report = run_diagnostics(_DiagAuth(), _settings_with_config(True))

    check = next(c for c in report.checks if c.name == "Google client_secrets.json")
    # The test environment has no client_secrets.json, which is the single most
    # common reason a fresh install cannot connect.
    assert check.state == FAIL
    assert "Desktop app" in check.fix and "client_secrets.json" in check.fix


def test_the_report_is_plain_text_and_carries_no_secrets():
    from services.diagnostics import run_diagnostics
    from utils.logger import redact
    text = run_diagnostics(_DiagAuth(), _settings_with_config(True)).as_text()

    assert "DIAGNOSTIC REPORT" in text
    assert "Verdict" in text
    assert "safe to send on" in text

    # The strongest available check: run the report through the same filter that
    # guards the log file. If nothing in it is credential-shaped, the filter
    # changes nothing. This also covers secrets a future check might add.
    assert redact(text) == text, "something in the report looks like a credential"

    # And no token value patterns, spelled out for the sake of the next reader.
    for forbidden in ("eyJ", "1//", "ya29.", "gAAAAA", "Bearer "):
        assert forbidden not in text, f"{forbidden} must never appear in the report"
    # "client_secrets.json" is a filename the user has to be told about, so the
    # word appears; an actual assignment of one must not.
    for leak in ("client_secret=", 'client_secret"', "client_secret:"):
        assert leak not in text


def test_every_failing_check_offers_a_fix():
    """A problem with no instruction is useless to an office user."""
    from services.auth_service import ConnState
    from services.diagnostics import run_diagnostics
    report = run_diagnostics(
        _DiagAuth(monday_state=ConnState.EXPIRED, google_state=ConnState.NOT_CONNECTED),
        _settings_with_config(False))

    assert report.problems
    for check in report.problems:
        assert check.fix.strip(), f"{check.name} says something is wrong but not what to do"


def test_the_verdict_reflects_the_worst_finding():
    from services.auth_service import ConnState
    from services.diagnostics import run_diagnostics
    broken = run_diagnostics(_DiagAuth(monday_state=ConnState.NOT_CONNECTED),
                             _settings_with_config(False))
    assert "stopping the app" in broken.verdict


# --------------------------------------------------------------------------- #
# Naming the connected account
# --------------------------------------------------------------------------- #

def test_the_signed_in_account_is_read_from_about():
    svc = drive_with([])

    assert svc.signed_in_account() == "tester@example.com"
    assert any(q.startswith("about:") for q in svc._svc.queries)


def test_the_display_name_is_used_when_there_is_no_email():
    svc = GoogleDriveService(FakeGoogleAuth())
    svc._svc = FakeDriveApi([], about={"user": {"displayName": "Front Desk"}})

    assert svc.signed_in_account() == "Front Desk"


def test_an_empty_about_response_gives_no_account():
    svc = GoogleDriveService(FakeGoogleAuth())
    svc._svc = FakeDriveApi([], about={})

    assert svc.signed_in_account() == ""


def test_naming_the_account_needs_the_metadata_scope():
    from services.errors import AuthError
    svc = GoogleDriveService(FakeGoogleAuth([GOOGLE_SCOPE_SHEETS]))

    with pytest.raises(AuthError):
        svc.signed_in_account()
