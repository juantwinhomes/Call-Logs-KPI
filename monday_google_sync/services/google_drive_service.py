"""Google Drive browsing, so the spreadsheet can be found by folder.

Read-only, and only metadata: the drive.metadata.readonly scope lets this list
names, ids and types. It cannot read what is inside any file. Opening the chosen
spreadsheet is still the Sheets client's job under the spreadsheets scope.

The browser understands the four places a spreadsheet actually lives:

    My Drive          the account's own tree
    Shared with me    files other people shared, which have no parent folder
    Shared drives     team drives, each its own root
    Starred           the user's own shortlist

Shortcuts are resolved to their target, because a folder full of shortcuts is a
normal way to organise a shared tracker and a user should not have to know the
difference.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from services.auth_service import GoogleAuth
from services.errors import (AppError, AuthError, NotFoundError, PermissionError_, RateLimitError,
                             SheetsError, wrap_network)
from utils.config import (GOOGLE_SCOPE_DRIVE_METADATA, MIME_FOLDER, MIME_SHORTCUT,
                          MIME_SPREADSHEET, SHEETS_MAX_RETRIES)
from utils.logger import get_logger

log = get_logger("drive")

PAGE_SIZE = 200
_FIELDS = ("nextPageToken, files(id, name, mimeType, modifiedTime, owners(displayName), "
           "parents, shortcutDetails(targetId, targetMimeType), driveId, trashed)")

# Pseudo-locations the browser offers alongside real folders.
ROOT_MY_DRIVE = "root"
ROOT_SHARED_WITH_ME = "__shared_with_me__"
ROOT_STARRED = "__starred__"
ROOT_SHARED_DRIVES = "__shared_drives__"

PSEUDO_ROOTS = {
    ROOT_MY_DRIVE: "My Drive",
    ROOT_SHARED_WITH_ME: "Shared with me",
    ROOT_STARRED: "Starred",
    ROOT_SHARED_DRIVES: "Shared drives",
}


@dataclass
class DriveItem:
    """A folder, a spreadsheet, or a shared drive presented as a folder."""
    id: str
    name: str
    mime_type: str
    modified: str = ""
    owner: str = ""
    parents: list[str] = field(default_factory=list)
    drive_id: str = ""
    is_shared_drive: bool = False

    @property
    def is_folder(self) -> bool:
        return self.mime_type == MIME_FOLDER or self.is_shared_drive

    @property
    def is_spreadsheet(self) -> bool:
        return self.mime_type == MIME_SPREADSHEET

    @property
    def kind(self) -> str:
        if self.is_shared_drive:
            return "Shared drive"
        if self.is_folder:
            return "Folder"
        if self.is_spreadsheet:
            return "Google Sheet"
        return "File"


@dataclass
class Listing:
    """What one folder contains, folders first."""
    folders: list[DriveItem] = field(default_factory=list)
    sheets: list[DriveItem] = field(default_factory=list)
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.folders and not self.sheets

    def all_items(self) -> list[DriveItem]:
        return self.folders + self.sheets


class GoogleDriveService:
    def __init__(self, auth: GoogleAuth) -> None:
        self.auth = auth
        self._svc: Any = None
        self._name_cache: dict[str, DriveItem] = {}

    # ------------------------------------------------------------------ core #
    def available(self) -> bool:
        """True when the stored credential carries the Drive metadata scope."""
        return self.auth.has_scope(GOOGLE_SCOPE_DRIVE_METADATA)

    def _require_scope(self) -> None:
        if not self.available():
            raise AuthError(
                "Browsing Google Drive needs one extra permission that this connection does not "
                "have yet. Press Reconnect Google Sheets and approve the request to see your "
                "folders. Everything else keeps working in the meantime - you can still paste a "
                "spreadsheet link.",
                "stored google credential lacks drive.metadata.readonly",
                service="Google", needs_reconnect=True)

    def _service(self) -> Any:
        if self._svc is None:
            from googleapiclient.discovery import build
            self._require_scope()
            creds = self.auth.credentials()
            self._svc = build("drive", "v3", credentials=creds, cache_discovery=False,
                              static_discovery=True)
        return self._svc

    def reset(self) -> None:
        self._svc = None
        self._name_cache.clear()

    def _call(self, request: Any, what: str) -> Any:
        from googleapiclient.errors import HttpError

        last: Exception | None = None
        for attempt in range(SHEETS_MAX_RETRIES):
            try:
                return request.execute(num_retries=0)
            except HttpError as exc:
                status = int(getattr(exc.resp, "status", 0) or 0)
                last = exc
                if status in (429, 500, 502, 503, 504) and attempt < SHEETS_MAX_RETRIES - 1:
                    wait = min(20.0, 1.5 * (2 ** attempt))
                    log.warning("Drive %s returned HTTP %d; retrying in %.1fs", what, status, wait)
                    time.sleep(wait)
                    continue
                raise self._http_error(exc, status, what) from exc
            except AuthError:
                raise
            except Exception as exc:
                last = exc
                name = type(exc).__name__.lower()
                if any(k in name for k in ("timeout", "connection", "ssl")) \
                        and attempt < SHEETS_MAX_RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise wrap_network(exc, "Google Drive") from exc
        raise wrap_network(last or RuntimeError("unknown"), "Google Drive")

    @staticmethod
    def _http_error(exc: Any, status: int, what: str) -> AppError:
        detail = f"drive {what}: HTTP {status}: {str(exc)[:300]}"
        if status == 401:
            return AuthError("Your Google authentication has expired. Please reconnect your account.",
                             detail, service="Google")
        if status == 403:
            low = str(exc).lower()
            if "quota" in low or "rate" in low or "userratelimit" in low:
                return RateLimitError(
                    "Google Drive is limiting requests right now. Please wait a moment and try "
                    "again.", detail)
            if "api has not been used" in low or "disabled" in low or "accessnotconfigured" in low:
                return PermissionError_(
                    "The Google Drive API is not enabled for this project, so folders cannot be "
                    "listed. Please ask IT to enable it in the Google Cloud console, or paste the "
                    "spreadsheet link instead.", detail)
            if "insufficient" in low or "scope" in low:
                return AuthError(
                    "Browsing Drive needs one extra permission. Press Reconnect Google Sheets and "
                    "approve the request.", detail, service="Google", needs_reconnect=True)
            return PermissionError_(
                "This Google account is not allowed to list that folder.", detail)
        if status == 404:
            return NotFoundError(
                "That Drive folder could not be found. It may have been deleted or moved.", detail)
        if status == 429:
            return RateLimitError("Google Drive is limiting requests. Please try again shortly.",
                                  detail)
        if 500 <= status < 600:
            return SheetsError("Google Drive is currently unavailable. Please try again shortly.",
                               detail)
        return SheetsError(f"Google Drive returned an unexpected response (code {status}).", detail)

    # --------------------------------------------------------------- browsing #
    def list_folder(self, folder_id: str = ROOT_MY_DRIVE, *,
                    max_items: int = 1000) -> Listing:
        """Folders and spreadsheets inside one folder or pseudo-location."""
        self._require_scope()
        if folder_id == ROOT_SHARED_DRIVES:
            return Listing(folders=self.shared_drives())

        if folder_id == ROOT_SHARED_WITH_ME:
            query = "sharedWithMe and trashed = false"
        elif folder_id == ROOT_STARRED:
            query = "starred = true and trashed = false"
        else:
            query = f"'{_escape(folder_id)}' in parents and trashed = false"
        # Ask only for what the browser shows. Everything else in the folder -
        # documents, PDFs, images - is irrelevant and would only be noise.
        query += (f" and (mimeType = '{MIME_FOLDER}' or mimeType = '{MIME_SPREADSHEET}'"
                  f" or mimeType = '{MIME_SHORTCUT}')")

        items: list[DriveItem] = []
        token: str | None = None
        truncated = False
        while True:
            resp = self._call(self._service().files().list(
                q=query, pageSize=PAGE_SIZE, pageToken=token, fields=_FIELDS,
                orderBy="folder,name_natural",
                supportsAllDrives=True, includeItemsFromAllDrives=True,
                corpora="allDrives" if folder_id in (ROOT_SHARED_WITH_ME, ROOT_STARRED)
                        else "allDrives"), f"list {folder_id}")
            for raw in resp.get("files") or []:
                item = _to_item(raw)
                if item is not None:
                    items.append(item)
                    self._name_cache[item.id] = item
            token = resp.get("nextPageToken")
            if not token or len(items) >= max_items:
                truncated = bool(token)
                break

        listing = Listing(
            folders=sorted((i for i in items if i.is_folder), key=lambda i: i.name.lower()),
            sheets=sorted((i for i in items if i.is_spreadsheet), key=lambda i: i.name.lower()),
            truncated=truncated)
        log.info("Drive folder %s: %d folder(s), %d spreadsheet(s)%s",
                 folder_id, len(listing.folders), len(listing.sheets),
                 " (truncated)" if truncated else "")
        return listing

    def shared_drives(self) -> list[DriveItem]:
        """Team drives, each presented as a top-level folder."""
        self._require_scope()
        out: list[DriveItem] = []
        token: str | None = None
        while True:
            resp = self._call(self._service().drives().list(
                pageSize=100, pageToken=token, fields="nextPageToken, drives(id, name)"),
                "list shared drives")
            for d in resp.get("drives") or []:
                if not d.get("id"):
                    continue
                item = DriveItem(id=str(d["id"]), name=d.get("name") or "Shared drive",
                                 mime_type=MIME_FOLDER, drive_id=str(d["id"]),
                                 is_shared_drive=True)
                out.append(item)
                self._name_cache[item.id] = item
            token = resp.get("nextPageToken")
            if not token:
                break
        out.sort(key=lambda i: i.name.lower())
        log.info("Account can see %d shared drive(s)", len(out))
        return out

    def get(self, file_id: str) -> DriveItem:
        """One item's metadata, used to name a folder in the breadcrumb."""
        self._require_scope()
        if file_id in PSEUDO_ROOTS:
            return DriveItem(id=file_id, name=PSEUDO_ROOTS[file_id], mime_type=MIME_FOLDER)
        if file_id in self._name_cache:
            return self._name_cache[file_id]
        resp = self._call(self._service().files().get(
            fileId=file_id, supportsAllDrives=True,
            fields="id, name, mimeType, modifiedTime, parents, driveId, "
                   "shortcutDetails(targetId, targetMimeType), owners(displayName)"),
            f"get {file_id}")
        item = _to_item(resp)
        if item is None:
            raise NotFoundError("That Drive item could not be opened.", f"get({file_id}) unusable")
        self._name_cache[item.id] = item
        return item

    def breadcrumb(self, folder_id: str, limit: int = 12) -> list[DriveItem]:
        """The path from a root down to this folder, for the trail across the top."""
        if folder_id in PSEUDO_ROOTS:
            return [DriveItem(folder_id, PSEUDO_ROOTS[folder_id], MIME_FOLDER)]
        trail: list[DriveItem] = []
        current = folder_id
        seen: set[str] = set()
        for _ in range(limit):
            if not current or current in seen:
                break
            seen.add(current)
            try:
                item = self.get(current)
            except AppError:
                break
            trail.append(item)
            if item.is_shared_drive or not item.parents:
                break
            current = item.parents[0]
        trail.reverse()
        # A file whose top parent is the account's own root sits in My Drive.
        if trail and not trail[0].is_shared_drive and trail[0].parents:
            trail.insert(0, DriveItem(ROOT_MY_DRIVE, PSEUDO_ROOTS[ROOT_MY_DRIVE], MIME_FOLDER))
        return trail

    def search_spreadsheets(self, text: str, *, limit: int = 100) -> list[DriveItem]:
        """Spreadsheets anywhere the account can see, by name."""
        self._require_scope()
        text = (text or "").strip()
        if not text:
            return []
        query = (f"mimeType = '{MIME_SPREADSHEET}' and trashed = false "
                 f"and name contains '{_escape(text)}'")
        resp = self._call(self._service().files().list(
            q=query, pageSize=min(limit, PAGE_SIZE), fields=_FIELDS,
            orderBy="modifiedTime desc", supportsAllDrives=True,
            includeItemsFromAllDrives=True, corpora="allDrives"), f"search '{text}'")
        out = [i for i in (_to_item(r) for r in (resp.get("files") or [])) if i is not None]
        for i in out:
            self._name_cache[i.id] = i
        log.info("Drive search for %r matched %d spreadsheet(s)", text, len(out))
        return out

    def roots(self) -> list[DriveItem]:
        """The starting places offered when the picker opens."""
        out = [DriveItem(ROOT_MY_DRIVE, PSEUDO_ROOTS[ROOT_MY_DRIVE], MIME_FOLDER),
               DriveItem(ROOT_SHARED_WITH_ME, PSEUDO_ROOTS[ROOT_SHARED_WITH_ME], MIME_FOLDER),
               DriveItem(ROOT_STARRED, PSEUDO_ROOTS[ROOT_STARRED], MIME_FOLDER)]
        try:
            if self.shared_drives():
                out.append(DriveItem(ROOT_SHARED_DRIVES, PSEUDO_ROOTS[ROOT_SHARED_DRIVES],
                                     MIME_FOLDER))
        except AppError as exc:
            # Shared drives are a Workspace feature; a personal account 403s here
            # and that is not a problem worth showing anyone.
            log.info("Shared drives unavailable: %s", exc.code)
        return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _escape(text: str) -> str:
    """Escape a value for a Drive query string."""
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


def _to_item(raw: dict[str, Any]) -> DriveItem | None:
    """Build a DriveItem, resolving a shortcut to what it points at."""
    if not raw or not raw.get("id"):
        return None
    mime = raw.get("mimeType") or ""
    file_id = str(raw["id"])
    name = raw.get("name") or "(untitled)"

    if mime == MIME_SHORTCUT:
        target = raw.get("shortcutDetails") or {}
        target_mime = target.get("targetMimeType") or ""
        target_id = target.get("targetId")
        if not target_id or target_mime not in (MIME_FOLDER, MIME_SPREADSHEET):
            return None
        # Keep the shortcut's own name - that is what the user sees in Drive -
        # but point at the real file so opening it works.
        file_id, mime = str(target_id), target_mime

    if mime not in (MIME_FOLDER, MIME_SPREADSHEET):
        return None
    owners = raw.get("owners") or []
    return DriveItem(
        id=file_id, name=name, mime_type=mime,
        modified=(raw.get("modifiedTime") or "")[:10],
        owner=(owners[0].get("displayName") if owners else "") or "",
        parents=[str(p) for p in (raw.get("parents") or [])],
        drive_id=str(raw.get("driveId") or ""))
