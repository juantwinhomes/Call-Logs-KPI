"""Application paths, constants and the settings store.

Settings live in SQLite (see database.models.SettingsStore); this module owns the
non-secret constants and the on-disk layout, and nothing here ever holds a token.

When frozen by PyInstaller the executable directory is read-only in a normal
install, so all writable state goes under %LOCALAPPDATA%\\MondayGoogleSync.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Monday + Google Sheets Sync"
APP_SLUG = "MondayGoogleSync"
APP_VERSION = "1.1.0"
ORG_NAME = "Twin Home Buyer"

# Single-instance IPC endpoint (QLocalServer name).
IPC_SOCKET_NAME = "MondayGoogleSync.singleinstance"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Directory holding read-only resources that ship with the app."""
    if is_frozen():
        # onefile unpacks to _MEIPASS; onedir keeps them beside the exe.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Writable per-user application directory."""
    override = os.environ.get("MGS_DATA_DIR")
    if override:
        p = Path(override).expanduser()
    elif sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        p = Path(base or Path.home()) / APP_SLUG
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_SLUG
    else:
        base = os.environ.get("XDG_DATA_HOME")
        p = Path(base) / APP_SLUG if base else Path.home() / ".local" / "share" / APP_SLUG
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "sync_state.sqlite3"


def token_store_path() -> Path:
    return data_dir() / "credentials.enc"


def key_file_path() -> Path:
    return data_dir() / "keyfile.bin"


# --------------------------------------------------------------------------- #
# OAuth / API constants
# --------------------------------------------------------------------------- #

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_OAUTH_AUTHORIZE = "https://auth.monday.com/oauth2/authorize"
MONDAY_OAUTH_TOKEN = "https://auth.monday.com/oauth2/token"
# Least privilege: read boards, read users/account for the connection test.
MONDAY_OAUTH_SCOPES = ["boards:read", "me:read"]

# Least privilege, in two parts.
#
# spreadsheets              read and write the spreadsheet the user names. The
#                           narrower drive.file scope cannot open a pre-existing
#                           sheet the app did not create, and the broader drive
#                           scope would grant access to every file in the account.
# drive.metadata.readonly   list folder and file *names*, so the user can browse
#                           Drive to find their spreadsheet instead of pasting a
#                           URL. It carries no ability to read file contents: the
#                           app can see that a document exists and what it is
#                           called, and nothing about what is inside it.
GOOGLE_SCOPE_SHEETS = "https://www.googleapis.com/auth/spreadsheets"
GOOGLE_SCOPE_DRIVE_METADATA = "https://www.googleapis.com/auth/drive.metadata.readonly"
GOOGLE_SCOPES = [GOOGLE_SCOPE_SHEETS, GOOGLE_SCOPE_DRIVE_METADATA]

# Drive item types the folder browser cares about.
MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_SPREADSHEET = "application/vnd.google-apps.spreadsheet"
MIME_SHORTCUT = "application/vnd.google-apps.shortcut"
GOOGLE_OAUTH_LOOPBACK_PORTS = (8731, 8732, 8733, 8734, 0)

HTTP_TIMEOUT = 30            # seconds, per request
MONDAY_PAGE_SIZE = 100       # items_page limit
SHEETS_MAX_RETRIES = 4

# --------------------------------------------------------------------------- #
# Tracking columns the app maintains in the worksheet
# --------------------------------------------------------------------------- #

TRACK_BOARD_ID = "Monday Board ID"
TRACK_ITEM_ID = "Monday Item ID"
TRACK_MODIFIED = "Last Monday Modified Date"
TRACK_SYNCED = "Last Synced Date"
TRACK_STATUS = "Sync Status"
TRACKING_COLUMNS = (TRACK_BOARD_ID, TRACK_ITEM_ID, TRACK_MODIFIED, TRACK_SYNCED, TRACK_STATUS)

# Setting keys (kept here so UI and services cannot drift apart)
S_MONDAY_AUTH_MODE = "monday.auth_mode"          # "token" | "oauth"
S_MONDAY_WORKSPACE_ID = "monday.workspace_id"
S_MONDAY_WORKSPACE_NAME = "monday.workspace_name"
S_MONDAY_BOARD_ID = "monday.board_id"
S_MONDAY_BOARD_NAME = "monday.board_name"
S_MONDAY_COLUMNS = "monday.monitored_columns"    # JSON list of column ids
S_SHEET_ID = "google.spreadsheet_id"
S_SHEET_NAME = "google.spreadsheet_name"
S_WORKSHEET = "google.worksheet"
S_MAPPINGS = "mapping.columns"                   # JSON list of {monday_id,monday_title,sheet_header}
S_AUTO_WRITE = "sync.auto_write"                 # "1" | "0"
S_LAST_SYNC = "sync.last_successful_checkpoint"  # ISO8601 UTC
S_LAST_CHECKED = "sync.last_checked"             # ISO8601 UTC


@dataclass
class OAuthClient:
    """OAuth client credentials, supplied by configuration, never hardcoded."""
    client_id: str = ""
    client_secret: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass
class Environment:
    """Non-secret runtime configuration, from environment or a local .env."""
    monday: OAuthClient = field(default_factory=OAuthClient)
    google: OAuthClient = field(default_factory=OAuthClient)
    google_client_secrets_file: str = ""

    @classmethod
    def load(cls) -> "Environment":
        _load_dotenv()
        return cls(
            monday=OAuthClient(
                os.environ.get("MONDAY_CLIENT_ID", "").strip(),
                os.environ.get("MONDAY_CLIENT_SECRET", "").strip(),
            ),
            google=OAuthClient(
                os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
                os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
            ),
            google_client_secrets_file=os.environ.get("GOOGLE_CLIENT_SECRETS_FILE", "").strip(),
        )

    def google_secrets_path(self) -> Path | None:
        """client_secrets.json, looked for beside the exe then in the data dir."""
        if self.google_client_secrets_file:
            p = Path(self.google_client_secrets_file).expanduser()
            return p if p.is_file() else None
        for cand in (data_dir() / "client_secrets.json",
                     bundle_dir() / "client_secrets.json",
                     Path.cwd() / "client_secrets.json"):
            if cand.is_file():
                return cand
        return None


def _load_dotenv() -> None:
    """Minimal .env reader — avoids a dependency and never overwrites real env."""
    for cand in (Path.cwd() / ".env", data_dir() / ".env", bundle_dir() / ".env"):
        if not cand.is_file():
            continue
        try:
            for raw in cand.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            pass
        break
