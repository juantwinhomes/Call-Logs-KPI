"""Installing a downloaded client_secrets.json.

The point of the feature is that people pick the wrong file - the console offers
several JSON downloads that look alike - so most of these tests are about the
rejections saying which mistake was made, not about the happy path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.errors import ConfigError
from services.google_setup import (installed_path, install_client_secrets, read_client_id)

DESKTOP_CLIENT = {
    "installed": {
        "client_id": "813400000000-abcdefghijklmnop.apps.googleusercontent.com",
        "project_id": "twin-call-tracker",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": "GOCSPX-notarealsecret",
        "redirect_uris": ["http://localhost"],
    }
}


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    """GOOGLE_CLIENT_SECRETS_FILE would change what the module warns about.

    monkeypatch rather than os.environ directly: it restores the variable when
    the test ends, so the two tests below that deliberately set it cannot leak it
    into another module's tests.
    """
    monkeypatch.delenv("GOOGLE_CLIENT_SECRETS_FILE", raising=False)


@pytest.fixture(autouse=True)
def _clean_target():
    yield
    installed_path().unlink(missing_ok=True)


def write(tmp_path: Path, doc, name: str = "download.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(doc) if not isinstance(doc, str) else doc, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# The file that works
# --------------------------------------------------------------------------- #

def test_a_desktop_client_is_installed_under_the_expected_name(tmp_path):
    source = write(tmp_path, DESKTOP_CLIENT,
                   "client_secret_813400000000-abc.apps.googleusercontent.com.json")

    result = install_client_secrets(source)

    assert result.path == installed_path()
    assert result.path.name == "client_secrets.json"       # renamed for the user
    assert result.client_id == DESKTOP_CLIENT["installed"]["client_id"]
    assert result.warning == ""
    assert json.loads(result.path.read_text(encoding="utf-8")) == DESKTOP_CLIENT
    assert source.is_file(), "the download is copied, not moved"


def test_the_app_finds_the_installed_file(tmp_path):
    """The whole point: oauth_available flips without restarting anything."""
    from utils.config import Environment

    env = Environment.load()
    assert env.google_secrets_path() is None

    install_client_secrets(write(tmp_path, DESKTOP_CLIENT))

    assert env.google_secrets_path() == installed_path()


def test_installing_twice_leaves_a_usable_file(tmp_path):
    install_client_secrets(write(tmp_path, DESKTOP_CLIENT))
    second = install_client_secrets(write(tmp_path, DESKTOP_CLIENT, "again.json"))

    assert json.loads(second.path.read_text(encoding="utf-8")) == DESKTOP_CLIENT


def test_picking_the_already_installed_file_does_not_destroy_it(tmp_path):
    install_client_secrets(write(tmp_path, DESKTOP_CLIENT))

    result = install_client_secrets(installed_path())

    assert result.path.is_file()
    assert json.loads(result.path.read_text(encoding="utf-8")) == DESKTOP_CLIENT


def test_a_byte_order_mark_is_tolerated(tmp_path):
    """Notepad adds one if the file has been opened and saved."""
    source = tmp_path / "bom.json"
    source.write_text(json.dumps(DESKTOP_CLIENT), encoding="utf-8-sig")

    assert install_client_secrets(source).client_id.startswith("813400000000")


def test_no_partial_file_is_left_behind(tmp_path):
    install_client_secrets(write(tmp_path, DESKTOP_CLIENT))

    leftovers = list(installed_path().parent.glob("client_secrets*.part"))
    assert leftovers == []


# --------------------------------------------------------------------------- #
# The files people pick by mistake
# --------------------------------------------------------------------------- #

def test_a_web_client_is_accepted_but_warned_about(tmp_path):
    doc = {"web": {"client_id": "813400000000-web.apps.googleusercontent.com"}}

    result = install_client_secrets(write(tmp_path, doc))

    assert result.path.is_file()
    assert "Web application" in result.warning
    assert "Desktop app" in result.warning


def test_a_service_account_key_is_named_as_such(tmp_path):
    doc = {"type": "service_account", "project_id": "x", "private_key": "-----BEGIN-----"}

    with pytest.raises(ConfigError) as caught:
        install_client_secrets(write(tmp_path, doc))

    assert "service account key" in caught.value.message
    assert "OAuth client ID" in caught.value.message
    assert not installed_path().exists()


def test_a_saved_sign_in_from_another_tool_is_named_as_such(tmp_path):
    doc = {"type": "authorized_user", "refresh_token": "1//x", "client_id": "y"}

    with pytest.raises(ConfigError) as caught:
        install_client_secrets(write(tmp_path, doc))

    assert "saved sign-in" in caught.value.message


def test_json_that_is_not_a_credential_says_what_to_look_for(tmp_path):
    with pytest.raises(ConfigError) as caught:
        install_client_secrets(write(tmp_path, {"hello": "world"}))

    assert "installed" in caught.value.message
    assert "client_id" in caught.value.message


def test_a_non_json_file_is_rejected(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("my google client id is 8134", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        install_client_secrets(source)

    assert "not valid JSON" in caught.value.message


def test_a_binary_file_is_rejected(tmp_path):
    source = tmp_path / "logo.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02\x80\xff")

    with pytest.raises(ConfigError):
        install_client_secrets(source)


def test_a_huge_file_is_rejected_without_reading_it(tmp_path):
    source = tmp_path / "backup.json"
    source.write_bytes(b"{}" + b" " * (70 * 1024))

    with pytest.raises(ConfigError) as caught:
        install_client_secrets(source)

    assert "too large" in caught.value.message


def test_a_missing_file_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as caught:
        install_client_secrets(tmp_path / "not-there.json")

    assert "could not be opened" in caught.value.message


def test_a_json_list_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        install_client_secrets(write(tmp_path, [DESKTOP_CLIENT]))


def test_an_installed_node_without_a_client_id_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        install_client_secrets(write(tmp_path, {"installed": {"project_id": "x"}}))


# --------------------------------------------------------------------------- #
# Warnings that are about the computer, not the file
# --------------------------------------------------------------------------- #

def test_an_environment_override_is_flagged_because_it_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS_FILE", str(tmp_path / "elsewhere.json"))

    result = install_client_secrets(write(tmp_path, DESKTOP_CLIENT))

    assert "GOOGLE_CLIENT_SECRETS_FILE" in result.warning
    assert result.path.is_file()


def test_an_override_pointing_at_the_installed_file_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS_FILE", str(installed_path()))

    result = install_client_secrets(write(tmp_path, DESKTOP_CLIENT))

    assert result.warning == ""


# --------------------------------------------------------------------------- #
# The error detail never reaches the screen, so it carries the technical part
# --------------------------------------------------------------------------- #

def test_the_detail_is_technical_and_the_message_is_not(tmp_path):
    with pytest.raises(ConfigError) as caught:
        install_client_secrets(write(tmp_path, "{ oops"))

    assert "json decode failed" in caught.value.detail
    assert "json decode failed" not in caught.value.message


def test_read_client_id_returns_the_id_without_touching_disk():
    client_id, warning = read_client_id(json.dumps(DESKTOP_CLIENT))

    assert client_id == DESKTOP_CLIENT["installed"]["client_id"]
    assert warning == ""
    assert not installed_path().exists()
