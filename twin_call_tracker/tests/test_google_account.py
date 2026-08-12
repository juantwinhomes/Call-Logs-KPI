"""Connecting a Google account, and connecting a *different* one afterwards.

Google reuses whatever account the default browser is already signed into unless
it is told otherwise, which makes attaching a second account look impossible.
These tests pin the request that stops that, and pin the label so a switch can
never leave the previous account's name on screen.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from services.auth_service import GOOGLE_KEY, ConnState, GoogleAuth
from utils.config import GOOGLE_SCOPES, Environment

CLIENT_CONFIG = {"installed": {"client_id": "8134-abc.apps.googleusercontent.com",
                               "client_secret": "GOCSPX-fake",
                               "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                               "token_uri": "https://oauth2.googleapis.com/token"}}


class FakeCreds:
    """What google_auth_oauthlib hands back after a successful sign-in."""

    def __init__(self, refresh_token: str = "1//fake-refresh") -> None:
        self.token = "ya29.fake-access"
        self.refresh_token = refresh_token
        self.token_uri = "https://oauth2.googleapis.com/token"
        self.client_id = CLIENT_CONFIG["installed"]["client_id"]
        self.client_secret = CLIENT_CONFIG["installed"]["client_secret"]
        self.scopes = list(GOOGLE_SCOPES)
        self.expiry = None
        self.valid = True


class FakeFlow:
    """Captures the kwargs run_local_server is called with."""
    last_kwargs: dict[str, Any] = {}

    def __init__(self, creds: Any = None) -> None:
        self._creds = creds or FakeCreds()

    @classmethod
    def from_client_config(cls, config, scopes, **_kw):
        cls.captured_scopes = list(scopes)
        return cls()

    def run_local_server(self, **kwargs):
        FakeFlow.last_kwargs = dict(kwargs)
        return self._creds


@pytest.fixture
def google(monkeypatch, tmp_path):
    """A GoogleAuth with a credential file in place and the browser stubbed out."""
    secrets = tmp_path / "client_secrets.json"
    secrets.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS_FILE", str(secrets))

    import google_auth_oauthlib.flow as flow_module
    monkeypatch.setattr(flow_module, "InstalledAppFlow", FakeFlow)

    auth = GoogleAuth(Environment.load())
    auth.disconnect()
    FakeFlow.last_kwargs = {}
    yield auth
    auth.disconnect()


def no_account_lookup(monkeypatch) -> None:
    """Skip the Drive call that names the account."""
    monkeypatch.setattr(GoogleAuth, "_remember_account", lambda self: None)


# --------------------------------------------------------------------------- #
# The sign-in request
# --------------------------------------------------------------------------- #

def test_google_is_asked_to_show_the_account_chooser(google, monkeypatch):
    no_account_lookup(monkeypatch)

    google.begin_oauth()

    prompt = FakeFlow.last_kwargs["prompt"]
    assert "select_account" in prompt, "without this Google reuses the browser's account"
    assert "consent" in prompt, "still needed for a fresh refresh token"


def test_offline_access_is_still_requested(google, monkeypatch):
    no_account_lookup(monkeypatch)

    google.begin_oauth()

    assert FakeFlow.last_kwargs["access_type"] == "offline"


def test_both_scopes_are_requested(google, monkeypatch):
    no_account_lookup(monkeypatch)

    google.begin_oauth()

    assert FakeFlow.captured_scopes == list(GOOGLE_SCOPES)


def test_a_sign_in_without_a_refresh_token_is_refused(google, monkeypatch):
    from services.errors import AuthError
    no_account_lookup(monkeypatch)
    monkeypatch.setattr(FakeFlow, "run_local_server",
                        lambda self, **kw: FakeCreds(refresh_token=""))

    with pytest.raises(AuthError):
        google.begin_oauth()


# --------------------------------------------------------------------------- #
# Which account is shown
# --------------------------------------------------------------------------- #

def test_the_connected_account_is_named_from_drive(google, monkeypatch):
    import services.google_drive_service as drive_module
    monkeypatch.setattr(drive_module.GoogleDriveService, "signed_in_account",
                        lambda self: "office@aclient.com")

    status = google.begin_oauth()

    assert status.state == ConnState.CONNECTED
    assert status.account == "office@aclient.com"


def test_connecting_a_second_account_replaces_the_first(google, monkeypatch):
    import services.google_drive_service as drive_module
    seen = iter(["juan@twinhomebuyer.com", "office@aclient.com"])
    monkeypatch.setattr(drive_module.GoogleDriveService, "signed_in_account",
                        lambda self: next(seen))

    assert google.begin_oauth().account == "juan@twinhomebuyer.com"
    assert google.begin_oauth().account == "office@aclient.com"


def test_a_failed_account_lookup_does_not_fail_the_sign_in(google, monkeypatch):
    import services.google_drive_service as drive_module

    def boom(self):
        raise RuntimeError("drive unreachable")
    monkeypatch.setattr(drive_module.GoogleDriveService, "signed_in_account", boom)

    status = google.begin_oauth()

    assert status.state == ConnState.CONNECTED
    assert status.account == "Google account connected"      # generic, never stale


def test_a_stale_account_label_is_not_carried_across_a_reconnect(google, monkeypatch):
    """The label must not survive when the lookup cannot say who it is now."""
    import services.google_drive_service as drive_module
    monkeypatch.setattr(drive_module.GoogleDriveService, "signed_in_account",
                        lambda self: "juan@twinhomebuyer.com")
    assert google.begin_oauth().account == "juan@twinhomebuyer.com"

    monkeypatch.setattr(drive_module.GoogleDriveService, "signed_in_account",
                        lambda self: (_ for _ in ()).throw(RuntimeError("offline")))

    assert google.begin_oauth().account != "juan@twinhomebuyer.com"


def test_a_token_refresh_keeps_the_account_label(google, monkeypatch):
    """Refreshing is not a reconnect, so the name must survive it."""
    import services.google_drive_service as drive_module
    monkeypatch.setattr(drive_module.GoogleDriveService, "signed_in_account",
                        lambda self: "office@aclient.com")
    google.begin_oauth()

    google._save(FakeCreds())                       # what credentials() does after a refresh

    assert (google.stored() or {}).get("account") == "office@aclient.com"


def test_disconnecting_forgets_the_account(google, monkeypatch):
    no_account_lookup(monkeypatch)
    google.begin_oauth()

    google.disconnect()

    assert google.stored() is None
    assert google.check().state == ConnState.NOT_CONNECTED


def test_the_stored_record_holds_the_refresh_token_and_scopes(google, monkeypatch):
    no_account_lookup(monkeypatch)

    google.begin_oauth()

    record = google.store.get(GOOGLE_KEY)
    assert record["refresh_token"] == "1//fake-refresh"
    assert record["scopes"] == list(GOOGLE_SCOPES)
