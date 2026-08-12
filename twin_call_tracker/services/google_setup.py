"""Filing away the Google OAuth client file that the sign-in flow needs.

Google will not let a desktop program talk to it at all until the program can
identify itself with an OAuth client, which arrives as a client_secrets.json
download from the Google Cloud console. Before this module existed the only way
to install that file was to rename it by hand and copy it into a hidden
%LOCALAPPDATA% folder - three steps, each of which goes wrong quietly. Windows
hides known extensions by default, so a rename often produced
client_secrets.json.json and the app kept insisting nothing was set up.

So the UI now asks for the file wherever it landed and this module puts it in
place. It checks the file is the right kind of credential first, because the
console offers several downloads that look alike and only one of them works.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from services.errors import ConfigError
from utils.config import data_dir
from utils.logger import get_logger

log = get_logger("google-setup")

# Where the console hands out OAuth clients, for the "open the console" button.
CONSOLE_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"

FILENAME = "client_secrets.json"

# A real client_secrets.json is well under a kilobyte. Anything large is the
# wrong file, and reading it into memory to find that out is wasteful.
MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class InstalledClient:
    """What ended up on disk, for the confirmation the user sees."""
    path: Path
    client_id: str
    warning: str = ""


def installed_path() -> Path:
    """Where this module writes, and the first place the app looks."""
    return data_dir() / FILENAME


def read_client_id(raw: str) -> tuple[str, str]:
    """Validate the text of a client_secrets.json; return (client_id, warning).

    Raises ConfigError naming the specific mistake, because "that file cannot be
    used" on its own leaves someone with four plausible downloads and no clue
    which one to try next.
    """
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(
            "That file is not valid JSON, so it is not the credential file. In the Google Cloud "
            "console open APIs & Services, then Credentials, and use the download button on the "
            "OAuth client you created.", f"json decode failed: {exc}") from exc

    if not isinstance(doc, dict):
        raise ConfigError(
            "That file does not look like a Google credential file. Please download the OAuth "
            "client again from the Google Cloud console.", f"top level is {type(doc).__name__}")

    # The console's other downloads, named so the message can be specific.
    if doc.get("type") == "service_account":
        raise ConfigError(
            "That is a service account key, not an OAuth client. This app signs in as you, so it "
            "needs an OAuth client: in the console choose Create credentials, then OAuth client "
            "ID, then Desktop app.", "service_account key supplied")
    if doc.get("type") == "authorized_user":
        raise ConfigError(
            "That is a saved sign-in from another tool, not an OAuth client. Please download the "
            "OAuth client itself from the Google Cloud console.", "authorized_user file supplied")

    node = doc.get("installed") or doc.get("web")
    if not isinstance(node, dict) or not node.get("client_id"):
        raise ConfigError(
            "That JSON file is not a Google OAuth client. The right file has a section called "
            "\"installed\" and a client_id inside it. Create one in the console under Create "
            "credentials, OAuth client ID, Desktop app.",
            f"no installed/web client_id; keys={sorted(doc)[:6]}")

    warning = ""
    if "installed" not in doc:
        # client_config() accepts this, but the loopback redirect the sign-in
        # uses is only registered automatically for a Desktop app client.
        warning = ("This is a Web application client. Sign-in may be refused with a redirect_uri "
                   "error. If that happens, create a Desktop app client instead and choose it here.")
    return str(node["client_id"]), warning


def install_client_secrets(source: Path) -> InstalledClient:
    """Copy a downloaded credential into the application data folder."""
    source = Path(source).expanduser()
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ConfigError("That file could not be opened. Please choose it again.",
                          f"{type(exc).__name__} on stat {source}") from exc
    if size > MAX_BYTES:
        raise ConfigError(
            "That file is far too large to be a Google credential file, which is under a kilobyte. "
            "Please choose the JSON downloaded from the Google Cloud console.",
            f"{size} bytes exceeds {MAX_BYTES}")
    try:
        raw = source.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError("That file could not be read. Please choose it again.",
                          f"{type(exc).__name__} reading {source}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(
            "That file is not text, so it is not the credential file. Please choose the JSON "
            "downloaded from the Google Cloud console.", f"UnicodeDecodeError: {exc}") from exc

    client_id, warning = read_client_id(raw)

    target = installed_path()
    if target.exists() and source.resolve() == target.resolve():
        # Already the installed file. Worth catching: writing it out through the
        # temporary below and replacing it with itself would work, but someone
        # re-picking the installed file should not risk their only copy.
        log.info("client_secrets.json already installed, left in place")
        return InstalledClient(target, client_id, warning)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.part")
    try:
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, target)          # atomic, so a crash cannot half-write it
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ConfigError(
            "The credential file could not be saved into the application folder. Please check that "
            f"you can write to {target.parent}.", f"{type(exc).__name__} writing {target}") from exc

    log.info("client_secrets.json installed for client %s", _mask(client_id))

    override = os.environ.get("GOOGLE_CLIENT_SECRETS_FILE", "").strip()
    if override and Path(override).expanduser() != target:
        extra = ("GOOGLE_CLIENT_SECRETS_FILE is set on this computer and takes priority over the "
                 "file just saved. Clear that setting, or point it at the file that was saved.")
        warning = f"{warning}\n\n{extra}" if warning else extra
    return InstalledClient(target, client_id, warning)


def _mask(client_id: str) -> str:
    """Client ids are not secret, but there is no reason to fill the log with one."""
    return client_id[:12] + "..." if len(client_id) > 15 else client_id
