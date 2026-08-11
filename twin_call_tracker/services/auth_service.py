"""Authentication for Monday.com and Google Sheets.

Monday supports two modes:
  * token  - a personal API token pasted once by the user. This is what Monday
             recommends for an internal tool and needs no OAuth app registration.
  * oauth  - the full authorization-code flow, used when a Monday OAuth app has
             been registered and its client id/secret supplied by configuration.

Google uses the installed-application authorization-code flow with a loopback
redirect and a refresh token, so the user connects once and the app renews access
silently afterwards (requirement 11).

Tokens only ever live in the encrypted store. Nothing in this module returns a
token to the user interface and nothing logs one.
"""
from __future__ import annotations

import json
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

import requests

from services.errors import AppError, AuthError, ConfigError, http_to_error, wrap_network
from utils.config import (Environment, GOOGLE_OAUTH_LOOPBACK_PORTS,
                          GOOGLE_SCOPE_DRIVE_METADATA, GOOGLE_SCOPES, HTTP_TIMEOUT,
                          MONDAY_API_URL, MONDAY_OAUTH_AUTHORIZE, MONDAY_OAUTH_SCOPES,
                          MONDAY_OAUTH_TOKEN)
from utils.logger import get_logger
from utils.security import secret_store

log = get_logger("auth")

MONDAY_KEY = "monday"
GOOGLE_KEY = "google"


class ConnState:
    NOT_CONNECTED = "Not Connected"
    CONNECTED = "Connected"
    EXPIRED = "Authentication Expired"
    ERROR = "Connection Error"


@dataclass
class ConnStatus:
    state: str = ConnState.NOT_CONNECTED
    account: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == ConnState.CONNECTED


# --------------------------------------------------------------------------- #
# Loopback receiver shared by both OAuth flows
# --------------------------------------------------------------------------- #

class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "TwinCallTracker/1.2"
    result: dict[str, str] = {}

    def do_GET(self) -> None:                                     # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        type(self).result = params
        ok = "code" in params
        body = _CALLBACK_PAGE_OK if ok else _CALLBACK_PAGE_FAIL
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: Any) -> None:          # silence stdout
        return


_CALLBACK_PAGE_OK = """<!doctype html><meta charset="utf-8"><title>Connected</title>
<style>body{font:16px system-ui;margin:0;display:grid;place-items:center;height:100vh;
background:#f4f6f9;color:#101a24}div{text-align:center;max-width:26em}
h1{font-size:20px;margin:0 0 8px}p{color:#4a5867}</style>
<div><h1>Connected</h1><p>You can close this tab and return to
Twin&nbsp;Call&nbsp;Tracker.</p></div>"""

_CALLBACK_PAGE_FAIL = """<!doctype html><meta charset="utf-8"><title>Not connected</title>
<style>body{font:16px system-ui;margin:0;display:grid;place-items:center;height:100vh;
background:#f4f6f9;color:#101a24}div{text-align:center;max-width:26em}
h1{font-size:20px;margin:0 0 8px}p{color:#4a5867}</style>
<div><h1>Authorisation was not completed</h1><p>You can close this tab and try again in
the application.</p></div>"""


def _serve_once(timeout: int = 180) -> tuple[HTTPServer, Callable[[], dict[str, str]]]:
    """Bind a loopback server on the first free configured port."""
    last: OSError | None = None
    for port in GOOGLE_OAUTH_LOOPBACK_PORTS:
        try:
            _CallbackHandler.result = {}
            httpd = HTTPServer(("127.0.0.1", port), _CallbackHandler)
            httpd.timeout = timeout

            def wait() -> dict[str, str]:
                httpd.handle_request()
                return dict(_CallbackHandler.result)

            return httpd, wait
        except OSError as exc:
            last = exc
            continue
    raise AppError("No local port was available to complete the sign-in. "
                   "Please close other applications and try again.", f"{last}")


# --------------------------------------------------------------------------- #
# Monday
# --------------------------------------------------------------------------- #

class MondayAuth:
    """Holds the Monday credential and can prove it works."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.store = secret_store()

    # -- state -------------------------------------------------------------- #
    def stored(self) -> dict[str, Any] | None:
        return self.store.get(MONDAY_KEY)

    @property
    def configured(self) -> bool:
        return bool(self.stored())

    def disconnect(self) -> None:
        self.store.delete(MONDAY_KEY)

    def mode(self) -> str:
        data = self.stored() or {}
        return str(data.get("mode") or "token")

    # -- token mode --------------------------------------------------------- #
    def save_personal_token(self, token: str) -> ConnStatus:
        token = (token or "").strip()
        if not token:
            raise ConfigError("Please paste your Monday.com API token.")
        if len(token) < 20:
            raise ConfigError("That does not look like a Monday.com API token. "
                              "Copy it from Monday.com > your avatar > Developers > My access tokens.")
        status = self._probe(token)
        if not status.ok:
            raise AuthError(status.detail or "Monday.com rejected that API token.",
                            status.detail, service="Monday.com")
        self.store.put(MONDAY_KEY, {"mode": "token", "access_token": token,
                                    "account": status.account})
        return status

    # -- oauth mode --------------------------------------------------------- #
    @property
    def oauth_available(self) -> bool:
        return self.env.monday.configured

    def begin_oauth(self) -> ConnStatus:
        if not self.oauth_available:
            raise ConfigError(
                "Monday.com OAuth is not configured on this installation. Use an API token "
                "instead, or ask IT to add MONDAY_CLIENT_ID and MONDAY_CLIENT_SECRET.")
        httpd, wait = _serve_once()
        redirect_uri = f"http://127.0.0.1:{httpd.server_port}/monday/callback"
        state = secrets.token_urlsafe(24)
        url = MONDAY_OAUTH_AUTHORIZE + "?" + urllib.parse.urlencode({
            "client_id": self.env.monday.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(MONDAY_OAUTH_SCOPES),
            "state": state,
        })
        log.info("Opening the browser for Monday.com authorisation")
        holder: dict[str, dict[str, str]] = {}

        def run() -> None:
            holder["params"] = wait()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        webbrowser.open(url)
        thread.join(timeout=185)
        httpd.server_close()
        params = holder.get("params") or {}
        if params.get("state") != state:
            raise AuthError("The Monday.com sign-in could not be verified. Please try again.",
                            "OAuth state mismatch", service="Monday.com")
        if "code" not in params:
            raise AuthError("Monday.com sign-in was not completed. Please try again.",
                            f"callback params: {sorted(params)}", service="Monday.com")
        return self._exchange_code(params["code"], redirect_uri)

    def _exchange_code(self, code: str, redirect_uri: str) -> ConnStatus:
        try:
            resp = requests.post(MONDAY_OAUTH_TOKEN, timeout=HTTP_TIMEOUT, data={
                "client_id": self.env.monday.client_id,
                "client_secret": self.env.monday.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            })
        except requests.RequestException as exc:
            raise wrap_network(exc, "Monday.com") from exc
        if resp.status_code != 200:
            raise http_to_error(resp.status_code, "Monday.com", resp.text)
        payload = resp.json()
        access = payload.get("access_token")
        if not access:
            raise AuthError("Monday.com did not return an access token. Please try again.",
                            "token response missing access_token", service="Monday.com")
        status = self._probe(access)
        record = {"mode": "oauth", "access_token": access, "account": status.account}
        if payload.get("refresh_token"):
            record["refresh_token"] = payload["refresh_token"]
        if payload.get("expires_in"):
            record["expires_at"] = (datetime.now(timezone.utc)
                                    + timedelta(seconds=int(payload["expires_in"]))).isoformat()
        self.store.put(MONDAY_KEY, record)
        return status

    # -- use ---------------------------------------------------------------- #
    def access_token(self) -> str:
        data = self.stored()
        if not data or not data.get("access_token"):
            raise AuthError("Monday.com is not connected yet. Please connect it first.",
                            "no stored monday credential", service="Monday.com")
        expires_at = data.get("expires_at")
        if expires_at and data.get("mode") == "oauth":
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp <= datetime.now(timezone.utc) + timedelta(seconds=60):
                    return self._refresh(data)
            except ValueError:
                pass
        return str(data["access_token"])

    def _refresh(self, data: dict[str, Any]) -> str:
        refresh = data.get("refresh_token")
        if not refresh or not self.env.monday.configured:
            raise AuthError(
                "Your Monday authentication has expired. Please reconnect your account.",
                "monday token expired and no refresh token is available", service="Monday.com")
        log.info("Refreshing the Monday.com access token")
        try:
            resp = requests.post(MONDAY_OAUTH_TOKEN, timeout=HTTP_TIMEOUT, data={
                "client_id": self.env.monday.client_id,
                "client_secret": self.env.monday.client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            })
        except requests.RequestException as exc:
            raise wrap_network(exc, "Monday.com") from exc
        if resp.status_code != 200:
            raise AuthError(
                "Your Monday authentication has expired. Please reconnect your account.",
                f"refresh failed HTTP {resp.status_code}", service="Monday.com")
        payload = resp.json()
        data["access_token"] = payload.get("access_token", data["access_token"])
        if payload.get("refresh_token"):
            data["refresh_token"] = payload["refresh_token"]
        if payload.get("expires_in"):
            data["expires_at"] = (datetime.now(timezone.utc)
                                  + timedelta(seconds=int(payload["expires_in"]))).isoformat()
        self.store.put(MONDAY_KEY, data)
        return str(data["access_token"])

    # -- probe -------------------------------------------------------------- #
    def _probe(self, token: str) -> ConnStatus:
        """A cheap `me` query proves the credential and names the account."""
        try:
            resp = requests.post(
                MONDAY_API_URL, timeout=HTTP_TIMEOUT,
                headers={"Authorization": token, "Content-Type": "application/json",
                         "API-Version": "2024-10"},
                json={"query": "{ me { name email } account { name } }"})
        except requests.RequestException as exc:
            err = wrap_network(exc, "Monday.com")
            return ConnStatus(ConnState.ERROR, "", err.message)
        if resp.status_code in (401, 403):
            return ConnStatus(ConnState.EXPIRED, "",
                              "Monday.com rejected the credential. Please reconnect.")
        if resp.status_code != 200:
            return ConnStatus(ConnState.ERROR, "",
                              http_to_error(resp.status_code, "Monday.com", resp.text).message)
        try:
            payload = resp.json()
        except ValueError:
            return ConnStatus(ConnState.ERROR, "", "Monday.com returned an unreadable response.")
        if payload.get("errors"):
            first = payload["errors"][0].get("message", "") if payload["errors"] else ""
            low = first.lower()
            if "unauthorized" in low or "authentication" in low or "not authenticated" in low:
                return ConnStatus(ConnState.EXPIRED, "",
                                  "Monday.com rejected the credential. Please reconnect.")
            return ConnStatus(ConnState.ERROR, "", f"Monday.com reported: {first[:180]}")
        data = payload.get("data") or {}
        me = data.get("me") or {}
        account = (data.get("account") or {}).get("name") or ""
        who = me.get("name") or me.get("email") or ""
        label = f"{who} ({account})" if who and account else who or account or "connected"
        return ConnStatus(ConnState.CONNECTED, label, "")

    def check(self) -> ConnStatus:
        data = self.stored()
        if not data:
            return ConnStatus(ConnState.NOT_CONNECTED, "", "")
        try:
            token = self.access_token()
        except AuthError as exc:
            return ConnStatus(ConnState.EXPIRED, str(data.get("account") or ""), exc.message)
        status = self._probe(token)
        if status.ok and not status.account:
            status.account = str(data.get("account") or "")
        return status


# --------------------------------------------------------------------------- #
# Google
# --------------------------------------------------------------------------- #

class GoogleAuth:
    """Google OAuth with a refresh token, stored encrypted."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.store = secret_store()

    # -- state -------------------------------------------------------------- #
    def stored(self) -> dict[str, Any] | None:
        return self.store.get(GOOGLE_KEY)

    @property
    def configured(self) -> bool:
        return bool(self.stored())

    def disconnect(self) -> None:
        self.store.delete(GOOGLE_KEY)

    def scopes(self) -> list[str]:
        """Scopes the stored credential actually carries."""
        data = self.stored() or {}
        return [str(x) for x in (data.get("scopes") or [])]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes()

    def missing_scopes(self) -> list[str]:
        """Scopes the app wants that this credential does not have.

        A connection made before Drive browsing existed carries only the
        spreadsheets scope. That connection still syncs perfectly well, so it is
        not treated as broken - only the folder browser asks for a reconnect.
        """
        held = set(self.scopes())
        return [s for s in GOOGLE_SCOPES if s not in held]

    def client_config(self) -> dict[str, Any]:
        """Client id/secret from a client_secrets.json or from the environment."""
        path = self.env.google_secrets_path()
        if path is not None:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ConfigError(
                    "The Google client_secrets.json file could not be read. Please ask IT to "
                    "replace it.", f"{type(exc).__name__} reading {path.name}") from exc
            node = raw.get("installed") or raw.get("web")
            if not node or not node.get("client_id"):
                raise ConfigError(
                    "The Google client_secrets.json file is not an OAuth client for a desktop "
                    "application.", "missing installed/web client_id")
            return {"installed": {
                "client_id": node["client_id"],
                "client_secret": node.get("client_secret", ""),
                "auth_uri": node.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": node.get("token_uri", "https://oauth2.googleapis.com/token"),
            }}
        if self.env.google.configured:
            return {"installed": {
                "client_id": self.env.google.client_id,
                "client_secret": self.env.google.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }}
        raise ConfigError(
            "Google Sheets is not set up on this installation yet. A client_secrets.json file "
            "from the Google Cloud console must be placed in the application data folder. "
            "See the README section 'Google setup'.",
            "no client_secrets.json and no GOOGLE_CLIENT_ID/SECRET")

    @property
    def oauth_available(self) -> bool:
        try:
            self.client_config()
            return True
        except ConfigError:
            return False

    # -- connect ------------------------------------------------------------ #
    def begin_oauth(self) -> ConnStatus:
        from google_auth_oauthlib.flow import InstalledAppFlow

        cfg = self.client_config()
        flow = InstalledAppFlow.from_client_config(cfg, scopes=GOOGLE_SCOPES)
        log.info("Opening the browser for Google authorisation")
        last: Exception | None = None
        for port in GOOGLE_OAUTH_LOOPBACK_PORTS:
            try:
                creds = flow.run_local_server(
                    port=port, open_browser=True, timeout_seconds=300,
                    authorization_prompt_message="",
                    success_message="Connected. You can close this tab and return to the app.",
                    # Ask for offline access so a refresh token comes back, and force the
                    # consent screen so a re-connect always yields a fresh refresh token.
                    access_type="offline", prompt="consent")
                break
            except OSError as exc:
                last = exc
                continue
            except Exception as exc:                       # user cancelled, timeout, etc.
                raise AuthError("Google sign-in was not completed. Please try again.",
                                f"{type(exc).__name__}: {exc}", service="Google") from exc
        else:
            raise AppError("No local port was available to complete the Google sign-in.",
                           f"{last}")
        if not creds or not creds.refresh_token:
            raise AuthError(
                "Google did not return a long-lived permission, so the app would ask you to sign "
                "in again constantly. Please remove this app under your Google Account's "
                "third-party access and connect once more.",
                "no refresh_token in credentials", service="Google")
        self._save(creds)
        return self.check()

    def _save(self, creds: Any) -> None:
        record = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or GOOGLE_SCOPES),
        }
        if getattr(creds, "expiry", None):
            record["expiry"] = creds.expiry.replace(tzinfo=timezone.utc).isoformat()
        existing = self.stored() or {}
        if existing.get("account"):
            record["account"] = existing["account"]
        self.store.put(GOOGLE_KEY, record)

    # -- use ---------------------------------------------------------------- #
    def credentials(self) -> Any:
        """Valid google credentials, refreshing silently when needed."""
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        data = self.stored()
        if not data or not data.get("refresh_token"):
            raise AuthError("Google Sheets is not connected yet. Please connect it first.",
                            "no stored google credential", service="Google")
        expiry = None
        if data.get("expiry"):
            try:
                expiry = datetime.fromisoformat(data["expiry"]).replace(tzinfo=None)
            except ValueError:
                expiry = None
        creds = Credentials(
            token=data.get("token"), refresh_token=data["refresh_token"],
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"), client_secret=data.get("client_secret"),
            scopes=data.get("scopes") or GOOGLE_SCOPES)
        creds.expiry = expiry
        if not creds.valid:
            log.info("Refreshing the Google access token")
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                raise AuthError(
                    "Your Google authentication has expired. Please reconnect your account.",
                    f"RefreshError: {exc}", service="Google") from exc
            except Exception as exc:
                raise wrap_network(exc, "Google") from exc
            self._save(creds)
        return creds

    def check(self) -> ConnStatus:
        data = self.stored()
        if not data:
            return ConnStatus(ConnState.NOT_CONNECTED, "", "")
        try:
            creds = self.credentials()
        except AuthError as exc:
            return ConnStatus(ConnState.EXPIRED, str(data.get("account") or ""), exc.message)
        except AppError as exc:
            return ConnStatus(ConnState.ERROR, str(data.get("account") or ""), exc.message)
        # The spreadsheets scope carries no profile information, so the account
        # label comes from the token's own metadata rather than a userinfo call
        # the app has no scope for.
        label = str(data.get("account") or "")
        if not label:
            label = "Google account connected"
            data["account"] = label
            self.store.put(GOOGLE_KEY, data)
        if not creds:
            return ConnStatus(ConnState.ERROR, label, "Google credentials could not be prepared.")
        detail = ""
        if GOOGLE_SCOPE_DRIVE_METADATA in self.missing_scopes():
            # Connected and fully able to sync; only folder browsing is unavailable.
            detail = ("Reconnect to browse Drive by folder - this connection was made before that "
                      "was available. Syncing works as it is.")
        return ConnStatus(ConnState.CONNECTED, label, detail)


# --------------------------------------------------------------------------- #
# Facade
# --------------------------------------------------------------------------- #

class AuthService:
    def __init__(self, env: Environment | None = None) -> None:
        self.env = env or Environment.load()
        self.monday = MondayAuth(self.env)
        self.google = GoogleAuth(self.env)

    def statuses(self) -> tuple[ConnStatus, ConnStatus]:
        return self.monday.check(), self.google.check()
