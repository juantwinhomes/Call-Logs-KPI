"""One error type with a user-facing sentence and a separate technical detail.

Requirement 15 asks for friendly messages and no stack traces on screen;
requirement 16 asks for the technical detail in the log. Every failure raised in
the services layer therefore carries both, and the UI only ever reads .message.
"""
from __future__ import annotations

import socket
from typing import Any


class AppError(Exception):
    """Base class: .message is safe to show, .detail goes to the log only."""

    code = "error"
    scope = "app"

    def __init__(self, message: str, detail: str = "", *, code: str | None = None,
                 scope: str | None = None, recoverable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or message
        if code:
            self.code = code
        if scope:
            self.scope = scope
        self.recoverable = recoverable

    def __str__(self) -> str:      # never leak detail through str()
        return self.message


class ConfigError(AppError):
    code, scope = "invalid_configuration", "config"


class AuthError(AppError):
    """Authentication is missing, expired, or was revoked."""
    code, scope = "authentication", "auth"

    def __init__(self, message: str, detail: str = "", *, service: str = "",
                 needs_reconnect: bool = True, **kw: Any) -> None:
        super().__init__(message, detail, **kw)
        self.service = service
        self.needs_reconnect = needs_reconnect


class NetworkError(AppError):
    code, scope = "network", "network"


class RateLimitError(AppError):
    code, scope = "rate_limit", "api"

    def __init__(self, message: str, detail: str = "", retry_after: float | None = None, **kw: Any):
        super().__init__(message, detail, **kw)
        self.retry_after = retry_after


class PermissionError_(AppError):
    code, scope = "permission", "api"


class NotFoundError(AppError):
    code, scope = "not_found", "api"


class MondayError(AppError):
    code, scope = "monday_api", "monday"


class SheetsError(AppError):
    code, scope = "google_api", "google"


# --------------------------------------------------------------------------- #
# Translation helpers
# --------------------------------------------------------------------------- #

NO_INTERNET = ("Unable to reach the internet. Please check your connection and try again.")


def wrap_network(exc: Exception, service: str) -> AppError:
    """Turn a transport-level exception into something a user can act on."""
    import requests

    name = type(exc).__name__
    if isinstance(exc, requests.exceptions.SSLError):
        return NetworkError(
            f"A secure connection to {service} could not be established. If your office uses "
            "a proxy or antivirus that inspects traffic, it may need to allow this application.",
            f"{name}: {exc}")
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
                        socket.timeout)):
        return NetworkError(f"{service} did not respond in time. Please try again.", f"{name}: {exc}")
    if isinstance(exc, requests.exceptions.ProxyError):
        return NetworkError(
            f"The connection to {service} was blocked by a proxy. Please check the network settings.",
            f"{name}: {exc}")
    if isinstance(exc, (requests.exceptions.ConnectionError, socket.gaierror, OSError)):
        return NetworkError(
            f"Unable to connect to {service}. Please check your internet connection and try again.",
            f"{name}: {exc}")
    return NetworkError(f"Unable to connect to {service}. Please try again.", f"{name}: {exc}")


def http_to_error(status: int, service: str, body: str = "") -> AppError:
    """Map an HTTP status onto the right error class and sentence."""
    snippet = (body or "")[:500]
    if status in (401, 403) and "rate" not in snippet.lower():
        if status == 401:
            return AuthError(
                f"Your {service} authentication is no longer valid. Please reconnect your account.",
                f"HTTP {status}: {snippet}", service=service)
        return PermissionError_(
            f"{service} refused the request because this account does not have permission. "
            "Please check that the account can see the board or spreadsheet you configured.",
            f"HTTP {status}: {snippet}")
    if status == 404:
        return NotFoundError(
            f"{service} could not find the item that was requested. It may have been deleted or "
            "renamed. Please check the settings.", f"HTTP {status}: {snippet}")
    if status == 429:
        return RateLimitError(
            f"{service} is temporarily limiting requests. Please wait a moment and try again.",
            f"HTTP {status}: {snippet}")
    if 500 <= status < 600:
        return AppError(f"{service} is currently unavailable. Please try again shortly.",
                        f"HTTP {status}: {snippet}", code="service_unavailable", scope=service.lower())
    return AppError(f"{service} returned an unexpected response (code {status}). Please try again.",
                    f"HTTP {status}: {snippet}", code="unexpected_response", scope=service.lower())
