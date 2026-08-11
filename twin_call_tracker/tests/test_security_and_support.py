"""Tests for log redaction, the encrypted store, error mapping and helpers.

Requirement 17 forbids credentials in logs and on screen, so those two are
asserted rather than assumed.
"""
from __future__ import annotations


import pytest                                                          # noqa: E402

from services.errors import (AppError, AuthError, NetworkError, NotFoundError,    # noqa: E402
                             PermissionError_, RateLimitError, http_to_error)
from services.google_sheets_service import (a1_column, extract_spreadsheet_id,    # noqa: E402
                                            quote_title, _first_row_of_range)
from utils.logger import redact                                        # noqa: E402
from utils.security import SecretStore, mask                           # noqa: E402


# --------------------------------------------------------------------------- #
# Log redaction
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("secret", [
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1aWQiOjEyMzQ1fQ.s3cr3tS1gnatureHere",
    "1//0gAbCdEfGhIjKlMnOpQrStUvWxYz1234567890",
    "ya29.a0ARrdaM-FakeAccessTokenValueForTesting123456",
    "gAAAAABm1234567890abcdefGHIJKLMNOPqrstuvwxyz==",
])
def test_token_shaped_strings_never_survive_redaction(secret: str):
    line = f"Sending request with token {secret} to the API"
    out = redact(line)
    assert secret not in out
    assert "<redacted>" in out


@pytest.mark.parametrize("line", [
    '{"access_token": "abcdef123456", "expires_in": 3600}',
    '{"refresh_token":"1//xyzXYZ0123456789"}',
    "client_secret=GOCSPX-abcdefghijklmnop",
    "api_key: sk-abcdef0123456789",
    "password = hunter2hunter2",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
    "authorization: Basic dXNlcjpwYXNzd29yZA==",
])
def test_credential_bearing_lines_are_scrubbed(line: str):
    out = redact(line)
    for leak in ("abcdef123456", "1//xyzXYZ0123456789", "GOCSPX-abcdefghijklmnop",
                 "sk-abcdef0123456789", "hunter2hunter2",
                 "abcdefghijklmnopqrstuvwxyz", "dXNlcjpwYXNzd29yZA=="):
        assert leak not in out, f"{leak} survived in: {out}"
    assert "<redacted>" in out


def test_redaction_leaves_ordinary_log_lines_alone():
    line = "Board 18421423765: 452 items seen, 3 changed since 2026-08-11T21:31:15+00:00"
    assert redact(line) == line


def test_the_logger_filter_scrubs_a_real_record(tmp_path, monkeypatch):
    """End to end: something logged with a token in it reaches the file scrubbed."""
    import logging
    from utils.logger import RedactionFilter

    path = tmp_path / "probe.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("mgs-test-redaction")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("token=%s", "ya29.SuperSecretAccessTokenValue")
    logger.info('{"refresh_token": "1//SuperSecretRefreshValue"}')
    handler.close()

    text = path.read_text(encoding="utf-8")
    assert "SuperSecretAccessTokenValue" not in text
    assert "SuperSecretRefreshValue" not in text
    assert "<redacted>" in text


def test_mask_shows_no_more_than_the_last_four_characters():
    assert mask("abcdefghijklmnop") == "********mnop"
    assert mask("") == "not set"
    assert mask(None) == "not set"
    assert "abcdefghijkl" not in mask("abcdefghijklmnop")


# --------------------------------------------------------------------------- #
# Encrypted store
# --------------------------------------------------------------------------- #

def test_secrets_round_trip_and_the_file_is_not_readable_plaintext():
    from utils.config import token_store_path
    store = SecretStore()
    store.clear()
    store.put("monday", {"mode": "token", "access_token": "SuperSecretTokenValue12345"})

    assert store.get("monday")["access_token"] == "SuperSecretTokenValue12345"
    blob = token_store_path().read_bytes()
    assert b"SuperSecretTokenValue12345" not in blob, "the token must not be on disk in the clear"
    assert blob.startswith(b"gAAAAA"), "expected Fernet ciphertext"


def test_a_fresh_store_instance_reads_what_the_previous_one_wrote():
    store = SecretStore()
    store.clear()
    store.put("google", {"refresh_token": "1//persisted"})
    assert SecretStore().get("google") == {"refresh_token": "1//persisted"}


def test_delete_and_has():
    store = SecretStore()
    store.clear()
    store.put("monday", {"access_token": "x" * 40})
    assert store.has("monday")
    store.delete("monday")
    assert not store.has("monday")
    assert store.get("monday") is None


# --------------------------------------------------------------------------- #
# Error mapping: the sentence the user sees, and no leakage
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status,cls", [
    (401, AuthError),
    (403, PermissionError_),
    (404, NotFoundError),
    (429, RateLimitError),
    (500, AppError),
    (503, AppError),
])
def test_http_statuses_map_to_the_right_error(status: int, cls: type):
    err = http_to_error(status, "Monday.com", "some body")
    assert isinstance(err, cls)
    assert err.message and not err.message.startswith("HTTP")


def test_the_technical_detail_is_kept_but_str_shows_only_the_friendly_message():
    err = http_to_error(500, "Google Sheets", "Traceback: internal explosion at 0xdeadbeef")
    assert "0xdeadbeef" in err.detail
    assert "0xdeadbeef" not in str(err)
    assert "0xdeadbeef" not in err.message
    assert "Traceback" not in err.message


def test_user_messages_are_plain_english_not_codes():
    for status in (401, 403, 404, 429, 500):
        message = http_to_error(status, "Monday.com").message
        assert message[0].isupper()
        assert message.endswith((".", "!"))
        assert "None" not in message
        assert "Traceback" not in message


def test_network_wrapping_gives_the_connection_advice():
    import requests
    err = AppError("", "")
    from services.errors import wrap_network
    err = wrap_network(requests.exceptions.ConnectionError("dns failure"), "Monday.com")
    assert isinstance(err, NetworkError)
    assert "internet connection" in err.message.lower()
    assert "dns failure" in err.detail


# --------------------------------------------------------------------------- #
# Sheets helpers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBd/edit#gid=0",
     "1BxiMVs0XRA5nFMdKvBd"),
    ("https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBd", "1BxiMVs0XRA5nFMdKvBd"),
    ("1BxiMVs0XRA5nFMdKvBdabcdefghij", "1BxiMVs0XRA5nFMdKvBdabcdefghij"),
    ("  1BxiMVs0XRA5nFMdKvBdabcdefghij  ", "1BxiMVs0XRA5nFMdKvBdabcdefghij"),
    ("not a sheet", ""),
    ("", ""),
])
def test_spreadsheet_id_is_extracted_from_whatever_the_user_pastes(text: str, expected: str):
    assert extract_spreadsheet_id(text) == expected


@pytest.mark.parametrize("index,letter", [
    (0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA"), (701, "ZZ"),
    (702, "AAA"),
])
def test_a1_column_letters(index: int, letter: str):
    assert a1_column(index) == letter


def test_worksheet_titles_with_apostrophes_are_quoted_safely():
    assert quote_title("Leads") == "'Leads'"
    assert quote_title("Ana's leads") == "'Ana''s leads'"
    assert quote_title("Q3 2026 - Live") == "'Q3 2026 - Live'"


@pytest.mark.parametrize("rng,row", [
    ("'Leads'!A57:K59", 57),
    ("Sheet1!A2:C2", 2),
    ("'Ana''s leads'!B10:D10", 10),
    ("", 0),
])
def test_first_row_of_an_appended_range(rng: str, row: int):
    assert _first_row_of_range(rng) == row


# --------------------------------------------------------------------------- #
# Config / model helpers
# --------------------------------------------------------------------------- #

def test_display_timestamp_reads_like_the_specification():
    from database.models import to_local_display
    import re
    text = to_local_display("2026-08-11T15:08:00+00:00")
    assert re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4} - \d{1,2}:\d{2} (AM|PM)$", text), text
    assert to_local_display(None) == "Never"
    assert to_local_display("nonsense") == "Never"


def test_missing_config_reasons_are_specific():
    from database.models import ColumnMapping, SyncConfig
    from utils.config import TRACK_ITEM_ID

    empty = SyncConfig()
    reasons = empty.missing_reasons()
    assert "no Monday.com board is chosen" in reasons
    assert "no Google spreadsheet is chosen" in reasons
    assert not empty.is_complete

    no_id = SyncConfig(board_id="1", spreadsheet_id="s", worksheet="t",
                       mappings=[ColumnMapping("x", "X", "Some Column")])
    assert any(TRACK_ITEM_ID in r for r in no_id.missing_reasons())

    good = SyncConfig(board_id="1", spreadsheet_id="s", worksheet="t",
                      mappings=[ColumnMapping("__item_id__", "id", TRACK_ITEM_ID),
                                ColumnMapping("x", "X", "Some Column")])
    assert good.is_complete
    assert good.missing_reasons() == []


def test_app_version_comparison():
    from ui.about import _newer
    assert _newer("1.1.0", "1.0.0")
    assert _newer("1.0.1", "1.0.0")
    assert _newer("2.0.0", "1.9.9")
    assert not _newer("1.0.0", "1.0.0")
    assert not _newer("0.9.0", "1.0.0")
    assert _newer("1.2", "1.1.9")


def test_a_keyring_that_does_not_persist_falls_back_to_a_key_file():
    """A null or locked-down credential store must not silently break the store.

    Without the read-back check in _key_from_keyring, every call would mint a new
    key and nothing written could ever be decrypted again.
    """
    from utils.config import key_file_path
    from utils.security import _key_from_keyring

    # conftest points keyring at the null backend, which accepts writes and keeps
    # nothing, so this is exactly the broken-backend case.
    assert _key_from_keyring() is None

    store = SecretStore()
    store.clear()
    store.put("monday", {"access_token": "PersistedThroughAKeyFile"})
    assert key_file_path().is_file(), "the fallback key file should exist"
    assert SecretStore().get("monday")["access_token"] == "PersistedThroughAKeyFile"


def test_state_from_the_previous_application_name_is_carried_over(tmp_path, monkeypatch):
    """Renaming the app must not appear to lose the user's whole setup.

    A 1.1.0 installation kept its files under MondayGoogleSync. On first run as
    Twin Call Tracker the folder is copied across, so settings, sync state and the
    encrypted credentials are still there.
    """
    import importlib
    from utils import config

    home = tmp_path / "home"
    (home).mkdir()
    monkeypatch.delenv("TCT_DATA_DIR", raising=False)
    monkeypatch.delenv("MGS_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    monkeypatch.setattr(config.sys, "platform", "linux", raising=False)

    legacy = home / config.LEGACY_APP_SLUG
    (legacy / "logs").mkdir(parents=True)
    (legacy / "sync_state.sqlite3").write_bytes(b"old database")
    (legacy / "credentials.enc").write_bytes(b"gAAAAAencrypted")
    (legacy / "logs" / "app.log").write_text("previous log", encoding="utf-8")

    config._migrated = False
    new_dir = config.data_dir()

    assert new_dir.name == config.APP_SLUG
    assert (new_dir / "sync_state.sqlite3").read_bytes() == b"old database"
    assert (new_dir / "credentials.enc").read_bytes() == b"gAAAAAencrypted"
    assert (new_dir / "logs" / "app.log").read_text(encoding="utf-8") == "previous log"
    # Copied, not moved, so an older build still finds its own data.
    assert (legacy / "sync_state.sqlite3").is_file()
    importlib.reload(config)


def test_the_carry_over_never_overwrites_existing_state(tmp_path, monkeypatch):
    import importlib
    from utils import config

    home = tmp_path / "home2"
    home.mkdir()
    monkeypatch.delenv("TCT_DATA_DIR", raising=False)
    monkeypatch.delenv("MGS_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    monkeypatch.setattr(config.sys, "platform", "linux", raising=False)

    legacy = home / config.LEGACY_APP_SLUG
    legacy.mkdir(parents=True)
    (legacy / "sync_state.sqlite3").write_bytes(b"old")
    current = home / config.APP_SLUG
    current.mkdir(parents=True)
    (current / "sync_state.sqlite3").write_bytes(b"in use")

    config._migrated = False
    config.data_dir()

    assert (current / "sync_state.sqlite3").read_bytes() == b"in use"
    importlib.reload(config)
