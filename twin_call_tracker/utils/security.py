"""Encrypted local credential storage.

Tokens are held in one Fernet-encrypted JSON blob (credentials.enc). The Fernet
key lives in the operating system credential store via `keyring` — Windows
Credential Manager, macOS Keychain, Secret Service on Linux. Where no keyring
backend exists the key falls back to a 0600 file in the app data directory and
that downgrade is logged, because a key beside its ciphertext protects against a
copied file but not against a user who can read the directory.

Nothing here writes a secret to a log, and no secret is ever returned in a
message intended for the user interface.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from utils.config import APP_SLUG, LEGACY_APP_SLUG, key_file_path, token_store_path
from utils.logger import get_logger

log = get_logger("security")

_KEYRING_SERVICE = f"{APP_SLUG}.encryption"
# The service name the key was filed under before the app was renamed. Without
# this the copied credentials.enc would be undecryptable and every user would
# silently have to reconnect both accounts.
_LEGACY_KEYRING_SERVICE = f"{LEGACY_APP_SLUG}.encryption"
_KEYRING_USER = "fernet-key"
_lock = threading.RLock()


class CredentialError(RuntimeError):
    """Raised when the credential store cannot be read or written."""


# --------------------------------------------------------------------------- #
# Key management
# --------------------------------------------------------------------------- #

def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        backend = keyring.get_keyring()
        if isinstance(backend, FailKeyring):
            return None
        # A "chainer" with no viable backend behaves like fail; probe it.
        return keyring
    except Exception as exc:                       # pragma: no cover - env specific
        log.debug("keyring unavailable: %s", type(exc).__name__)
        return None


def _key_from_keyring() -> bytes | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        existing = kr.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if existing:
            return existing.encode("ascii")
        # An installation from before the rename filed its key under the old name.
        inherited = kr.get_password(_LEGACY_KEYRING_SERVICE, _KEYRING_USER)
        if inherited:
            try:
                kr.set_password(_KEYRING_SERVICE, _KEYRING_USER, inherited)
                log.info("Carried the encryption key over from the previous "
                         "application name, so saved connections still work")
            except Exception:
                pass          # reading it is enough; re-filing is a convenience
            return inherited.encode("ascii")
        key = Fernet.generate_key()
        kr.set_password(_KEYRING_SERVICE, _KEYRING_USER, key.decode("ascii"))
        # Some backends accept a write and store nothing - a null backend, or a
        # locked-down machine. Without this read-back every call would mint a
        # fresh key and the store would decrypt with none of them.
        if kr.get_password(_KEYRING_SERVICE, _KEYRING_USER) != key.decode("ascii"):
            log.warning("The OS credential store did not keep the encryption key; "
                        "falling back to a key file")
            return None
        log.info("Created a new encryption key in the OS credential store")
        return key
    except Exception as exc:                       # pragma: no cover - env specific
        log.warning("Could not use the OS credential store (%s); falling back to a key file",
                    type(exc).__name__)
        return None


def _key_from_file() -> bytes:
    path = key_file_path()
    if path.is_file():
        try:
            data = path.read_bytes().strip()
            if data:
                return data
        except OSError as exc:
            raise CredentialError("The local encryption key file could not be read.") from exc
    key = Fernet.generate_key()
    try:
        path.write_bytes(key)
        if not os.name == "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)   # 0600
        else:
            _windows_restrict(path)
        log.warning("No OS credential store available - encryption key stored at %s "
                    "with owner-only permissions", path.name)
    except OSError as exc:
        raise CredentialError("The local encryption key file could not be created.") from exc
    return key


def _windows_restrict(path) -> None:                # pragma: no cover - Windows only
    """Best-effort ACL tightening so only the current user can read the key."""
    try:
        import subprocess
        user = os.environ.get("USERNAME")
        if not user:
            return
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
                       check=False, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def _fernet() -> Fernet:
    key = _key_from_keyring() or _key_from_file()
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise CredentialError("The local encryption key is not valid.") from exc


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

class SecretStore:
    """A tiny encrypted key/value store for tokens.

    Keys are plain identifiers such as "monday" or "google"; values are JSON
    serialisable dicts. Reads are cached in memory for the process lifetime.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None

    # -- internals ---------------------------------------------------------- #
    def _load(self) -> dict[str, Any]:
        with _lock:
            if self._cache is not None:
                return self._cache
            path = token_store_path()
            if not path.is_file():
                self._cache = {}
                return self._cache
            try:
                blob = path.read_bytes()
            except OSError as exc:
                raise CredentialError("Saved credentials could not be read.") from exc
            if not blob.strip():
                self._cache = {}
                return self._cache
            try:
                self._cache = json.loads(_fernet().decrypt(blob).decode("utf-8"))
            except InvalidToken:
                # Wrong key: the store is unreadable. Do not delete the user's
                # file silently - rename it so a reconnect can proceed.
                backup = path.with_suffix(".unreadable")
                try:
                    path.replace(backup)
                    log.error("Credential store could not be decrypted; moved to %s. "
                              "The accounts must be reconnected.", backup.name)
                except OSError:
                    log.error("Credential store could not be decrypted and could not be moved.")
                self._cache = {}
            except (ValueError, UnicodeDecodeError) as exc:
                log.error("Credential store is corrupt: %s", type(exc).__name__)
                self._cache = {}
            return self._cache

    def _flush(self) -> None:
        with _lock:
            data = json.dumps(self._cache or {}, separators=(",", ":")).encode("utf-8")
            path = token_store_path()
            tmp = path.with_suffix(".tmp")
            try:
                tmp.write_bytes(_fernet().encrypt(data))
                if os.name != "nt":
                    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
                tmp.replace(path)                     # atomic on the same volume
                if os.name == "nt":
                    _windows_restrict(path)
            except OSError as exc:
                raise CredentialError("Credentials could not be saved to disk.") from exc

    # -- public ------------------------------------------------------------- #
    def get(self, name: str) -> dict[str, Any] | None:
        value = self._load().get(name)
        return dict(value) if isinstance(value, dict) else None

    def put(self, name: str, value: dict[str, Any]) -> None:
        self._load()[name] = value
        self._flush()
        log.info("Stored credentials for '%s' (encrypted, %d fields)", name, len(value))

    def delete(self, name: str) -> None:
        if self._load().pop(name, None) is not None:
            self._flush()
            log.info("Removed stored credentials for '%s'", name)

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    def clear(self) -> None:
        self._cache = {}
        self._flush()


_store: SecretStore | None = None


def secret_store() -> SecretStore:
    global _store
    if _store is None:
        _store = SecretStore()
    return _store


def mask(value: str | None, keep: int = 4) -> str:
    """Render a secret for display: never more than the last few characters."""
    if not value:
        return "not set"
    tail = value[-keep:] if len(value) > keep else ""
    return f"{'*' * 8}{tail}" if tail else "*" * 8
