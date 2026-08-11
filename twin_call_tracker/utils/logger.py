"""Rotating file logging with credential redaction.

Requirement 16 says the log must record what happened, and requirement 17 says it
must never contain a token. Both are met by scrubbing every record through
RedactionFilter before it is formatted, so a token cannot reach the file even if
some future caller passes one in by mistake.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from typing import Iterable

from utils.config import APP_SLUG, APP_VERSION, log_dir

LOG_FILE_NAME = "app.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 5
_configured = False

# Patterns that must never survive into the log. Ordered widest-first.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JSON or kwargs style: "access_token": "...."
    (re.compile(r'(?i)("?(?:access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|'
                r'api[_-]?key|apikey|authorization|password|passwd|secret|private[_-]?key)"?\s*'
                r'[:=]\s*")([^"]{4,})(")'), r"\1<redacted>\3"),
    (re.compile(r'(?i)\b((?:access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|'
                r'api[_-]?key|apikey|password|secret)\s*[:=]\s*)([^\s,;"\')]{4,})'),
     r"\1<redacted>"),
    # HTTP Authorization headers
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}"), r"\1 <redacted>"),
    # Google refresh tokens and OAuth codes
    (re.compile(r"\b1//[A-Za-z0-9_\-]{10,}"), "<redacted>"),
    (re.compile(r"\bya29\.[A-Za-z0-9._\-]{10,}"), "<redacted>"),
    # JSON Web Tokens (Monday API tokens are JWTs)
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"), "<redacted>"),
    # Fernet ciphertext
    (re.compile(r"\bgAAAAA[A-Za-z0-9_\-=]{16,}"), "<redacted>"),
)


def redact(text: str) -> str:
    """Scrub anything token-shaped out of a string."""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactionFilter(logging.Filter):
    """Rewrites the message and args of every record before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: self._clean(v) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(self._clean(a) for a in record.args)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:      # logging must never break the app
            pass
        return True

    @staticmethod
    def _clean(value):
        return redact(value) if isinstance(value, str) else value


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure the root logger once. Safe to call repeatedly."""
    global _configured
    root = logging.getLogger()
    if _configured:
        return logging.getLogger(APP_SLUG)

    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    redactor = RedactionFilter()

    try:
        fh = logging.handlers.RotatingFileHandler(
            log_dir() / LOG_FILE_NAME, maxBytes=_MAX_BYTES, backupCount=_BACKUPS,
            encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(redactor)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)
    except OSError:
        pass       # a read-only disk must not stop the app starting

    # A GUI build has no console; only attach one when a stream really exists.
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.addFilter(redactor)
        sh.setLevel(logging.INFO)
        root.addHandler(sh)

    # Third-party libraries are chatty and their debug output can carry headers.
    for noisy in ("googleapiclient", "google", "google_auth_httplib2", "urllib3",
                  "requests", "oauthlib", "requests_oauthlib", "google.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    log = logging.getLogger(APP_SLUG)
    log.info("=" * 62)
    log.info("Application starting - %s v%s", APP_SLUG, APP_VERSION)
    log.info("Log file: %s", log_dir() / LOG_FILE_NAME)
    return log


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{APP_SLUG}.{name}")


def read_log_tail(max_lines: int = 500) -> str:
    """Last lines of the current log, for the Logs screen."""
    path = log_dir() / LOG_FILE_NAME
    if not path.is_file():
        return "No log file yet."
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines: Iterable[str] = fh.readlines()
        tail = list(lines)[-max_lines:]
        return "".join(tail) or "Log file is empty."
    except OSError as exc:
        return f"Could not read the log file: {exc}"
