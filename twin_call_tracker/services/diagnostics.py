"""A self-check the user can run and send to whoever supports the app.

Every check answers one question, in plain language, and the report contains no
credentials — only whether one is present and whether it works. The point is that
somebody who cannot see the screen can read this and know what is wrong.
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from utils.config import (APP_VERSION, GOOGLE_SCOPE_DRIVE_METADATA, Environment, data_dir,
                          db_path, is_frozen, log_dir, token_store_path)
from utils.logger import get_logger

log = get_logger("diagnostics")

OK = "OK"
WARN = "NEEDS ATTENTION"
FAIL = "PROBLEM"
SKIP = "not checked"


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    generated_at: str = ""

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.checks if c.state == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.state == WARN]

    @property
    def verdict(self) -> str:
        if self.problems:
            return "Something is stopping the app from working."
        if self.warnings:
            return "The app works, but something still needs setting up."
        return "Everything checks out."

    def as_text(self) -> str:
        lines = [
            "TWIN CALL TRACKER - DIAGNOSTIC REPORT",
            "=" * 62,
            f"Generated : {self.generated_at}",
            f"Verdict   : {self.verdict}",
            "",
        ]
        for c in self.checks:
            lines.append(f"[{c.state}] {c.name}")
            if c.detail:
                lines.append(f"    {c.detail}")
            if c.fix and c.state in (FAIL, WARN):
                lines.append(f"    -> {c.fix}")
        lines += ["", "This report contains no passwords or tokens and is safe to send on."]
        return "\n".join(lines)


def run_diagnostics(auth: Any, settings: Any) -> Report:
    """Walk every prerequisite in the order it is needed."""
    from database.models import Checkpoint, SyncConfig, SyncRunRepo
    from services.auth_service import ConnState

    report = Report(generated_at=datetime.now(timezone.utc).astimezone().strftime(
        "%d %B %Y at %H:%M"))
    add = report.checks.append
    env = getattr(auth, "env", None) or Environment.load()

    # ---- 1. the installation itself ------------------------------------- #
    add(Check("Application", OK,
              f"Version {APP_VERSION}, "
              f"{'packaged executable' if is_frozen() else 'running from source'}, "
              f"Python {platform.python_version()} on {platform.system()} {platform.release()}"))

    writable = True
    try:
        probe = data_dir() / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        writable = False
        add(Check("Application data folder", FAIL,
                  f"{data_dir()} cannot be written to ({type(exc).__name__})",
                  "The app needs to save its settings here. Check the folder's permissions, or "
                  "set MGS_DATA_DIR to somewhere writable."))
    if writable:
        add(Check("Application data folder", OK, str(data_dir())))
        add(Check("Settings database", OK if db_path().is_file() else WARN,
                  f"{db_path().name} "
                  + (f"({db_path().stat().st_size // 1024} KB)" if db_path().is_file()
                     else "has not been created yet"),
                  "It is created the first time the app opens." if not db_path().is_file() else ""))

    # ---- 2. Google prerequisites ---------------------------------------- #
    secrets_path = env.google_secrets_path()
    if secrets_path is None:
        add(Check("Google client_secrets.json", FAIL,
                  "not found in the application data folder",
                  f"Download the OAuth client for a Desktop app from the Google Cloud console, "
                  f"rename it to client_secrets.json, and put it in {data_dir()}. "
                  "Until then the Connect Google Sheets button cannot do anything."))
    else:
        add(Check("Google client_secrets.json", OK, str(secrets_path)))

    add(Check("Saved credentials file", OK if token_store_path().is_file() else WARN,
              f"{token_store_path().name} "
              + ("present and encrypted" if token_store_path().is_file()
                 else "not created yet - nothing has been connected"),
              "Connect Monday.com and Google Sheets on the dashboard."
              if not token_store_path().is_file() else ""))

    # ---- 3. connections -------------------------------------------------- #
    try:
        monday_status = auth.monday.check()
        state, detail = monday_status.state, monday_status.detail
        if state == ConnState.CONNECTED:
            add(Check("Monday.com connection", OK, monday_status.account or "connected"))
        elif state == ConnState.NOT_CONNECTED:
            add(Check("Monday.com connection", FAIL, "not connected",
                      "Press Connect Monday.com and paste your API token "
                      "(Monday.com > your avatar > Developers > My access tokens)."))
        elif state == ConnState.EXPIRED:
            add(Check("Monday.com connection", FAIL, detail or "the credential was rejected",
                      "Press Reconnect Monday.com."))
        else:
            add(Check("Monday.com connection", FAIL, detail or "could not be reached",
                      "Check the internet connection, then press Test Connection."))
    except Exception as exc:
        add(Check("Monday.com connection", FAIL, f"the check itself failed: {type(exc).__name__}",
                  "Send this report and the log file on."))

    try:
        google_status = auth.google.check()
        state, detail = google_status.state, google_status.detail
        if state == ConnState.CONNECTED:
            add(Check("Google Sheets connection", OK, google_status.account or "connected"))
            missing = auth.google.missing_scopes()
            if GOOGLE_SCOPE_DRIVE_METADATA in missing:
                add(Check("Google Drive folder browsing", WARN,
                          "this connection predates folder browsing, so Drive cannot be listed",
                          "Press Reconnect Google Sheets and approve the extra permission. "
                          "Syncing works without it - only the Browse button needs it."))
            else:
                add(Check("Google Drive folder browsing", OK, "permission granted"))
        elif state == ConnState.NOT_CONNECTED:
            add(Check("Google Sheets connection", FAIL, "not connected",
                      "Press Connect Google Sheets. If it says Google is not set up, the "
                      "client_secrets.json step above is the reason."))
        elif state == ConnState.EXPIRED:
            add(Check("Google Sheets connection", FAIL, detail or "the credential was rejected",
                      "Press Reconnect Google Sheets."))
        else:
            add(Check("Google Sheets connection", FAIL, detail or "could not be reached",
                      "Check the internet connection, then press Test Connection."))
    except Exception as exc:
        add(Check("Google Sheets connection", FAIL, f"the check itself failed: {type(exc).__name__}",
                  "Send this report and the log file on."))

    # ---- 4. configuration ------------------------------------------------ #
    cfg = SyncConfig.load(settings)
    if cfg.is_complete:
        add(Check("Configuration", OK,
                  f'board "{cfg.board_name or cfg.board_id}" -> '
                  f'"{cfg.spreadsheet_name or cfg.spreadsheet_id}" tab "{cfg.worksheet}", '
                  f"{len(cfg.mappings)} column(s) mapped"))
    else:
        add(Check("Configuration", FAIL, "; ".join(cfg.missing_reasons()),
                  "Open Settings and finish these, then press Save Settings."))

    add(Check("Write mode", OK,
              "Refresh writes changes straight away" if cfg.auto_write
              else "Preview mode - Refresh shows what it found and waits for Apply Updates"))

    # ---- 5. what has actually happened ----------------------------------- #
    checkpoint = Checkpoint(settings)
    runs = SyncRunRepo().recent(10)
    if not runs:
        add(Check("Sync history", WARN, "no refresh has ever run on this computer",
                  "Press REFRESH / CHECK FOR UPDATES once everything above is OK."))
    else:
        last = runs[0]
        state = OK if last.outcome in ("success", "preview", "warning") else FAIL
        add(Check("Last refresh", state,
                  f"{last.started_at} - {last.outcome}: {last.message or 'no message'}",
                  "The Sync History screen lists the technical error." if state == FAIL else ""))
        failures = [r for r in runs if r.outcome == "error"]
        if failures and last.outcome != "error":
            add(Check("Earlier failures", WARN,
                      f"{len(failures)} of the last {len(runs)} runs failed",
                      "See Sync History."))
    add(Check("Last successful sync point", OK if checkpoint.get() else WARN,
              checkpoint.get() or "never - the next refresh will examine every item",
              ""))

    # ---- 6. environment -------------------------------------------------- #
    proxy = any(k in os.environ for k in
                ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"))
    add(Check("Network", OK,
              "a proxy is configured in the environment" if proxy
              else "no proxy configured; direct connection"))
    add(Check("Log file", OK if (log_dir() / "app.log").is_file() else WARN,
              str(log_dir() / "app.log") if (log_dir() / "app.log").is_file()
              else "not written yet"))

    log.info("Diagnostics run: %d checks, %d problem(s), %d warning(s)",
             len(report.checks), len(report.problems), len(report.warnings))
    return report
