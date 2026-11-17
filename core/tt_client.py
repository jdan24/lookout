"""TT REST client: token, pacing, call cap, status checks and paging.

Every request goes through ``core.readonly_http.ReadOnlyClient``, so this module
cannot write to TT. It only knows how to GET.

The request conventions below (``x-api-key`` and ``Authorization: Bearer`` headers,
a ``requestId`` query parameter, and the form fields of the token call) follow TT's
public REST API documentation. They were NOT taken from the previous tool's code and
must be confirmed on the first supervised pull.

The ``requestId`` must be ``<app>-<company>--<guid>``: TT answered HTTP 400 to
``Lookout--<guid>`` on the first production pull. ``core.config`` enforces the prefix.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from core.api_log import ApiLog
from core.credentials import TTCredentials
from core.normalize import is_on
from core.readonly_http import ReadOnlyClient, Response, TransportError

TOKEN_REFRESH_MARGIN_SECONDS = 60
MAX_RATE_LIMIT_RETRIES = 2
DEFAULT_RETRY_AFTER_SECONDS = 2.0


class TTApiError(Exception):
    """A TT call failed. The message never contains credentials or tokens."""

    def __init__(self, message: str, *, path: str = "", status_code: int | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.status_code = status_code


class CallCapReached(TTApiError):
    """The per-pull call cap was reached; no further calls are made."""


@dataclass(frozen=True)
class TTResponse:
    body: dict[str, Any]
    text: str  # exactly as TT sent it, for the snapshot


@dataclass(frozen=True)
class Page:
    number: int  # 1-based
    response: TTResponse
    reached_end: bool  # True on the last page of the list


class TTClient:
    def __init__(
        self,
        http: ReadOnlyClient,
        credentials: TTCredentials | Callable[[], TTCredentials],
        *,
        requests_per_second: float = 2.0,
        max_calls: int = 1500,
        request_id_prefix: str = "Lookout-CHANGEME",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        api_log: ApiLog | None = None,
    ) -> None:
        self._http = http
        self.api_log = api_log  # every call is written here when given
        self.environment = http.environment  # Live or UAT, fixed by the guarded client
        self._credentials = credentials
        self._interval = 1.0 / requests_per_second
        self._max_calls = max_calls
        self._prefix = request_id_prefix
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.calls: list[dict[str, Any]] = []  # (seq, method, path, status, ms); no secrets

    def __repr__(self) -> str:
        return f"TTClient(calls={len(self.calls)}, max_calls={self._max_calls})"

    @property
    def calls_made(self) -> int:
        return len(self.calls)

    @property
    def calls_left(self) -> int:
        return max(0, self._max_calls - len(self.calls))

    # --- plumbing ---------------------------------------------------------------

    def _creds(self) -> TTCredentials:
        return self._credentials() if callable(self._credentials) else self._credentials

    def _request_id(self) -> str:
        return f"{self._prefix}--{uuid.uuid4()}"

    def _send(
        self,
        method: str,
        path: str,
        send: Callable[[], Response],
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        log_bodies: bool = True,
    ) -> Response:
        """One paced, counted, logged HTTP call.

        ``log_bodies`` is False for the sign-in call: its form and reply hold secrets."""
        if len(self.calls) >= self._max_calls:
            raise CallCapReached(
                f"Call cap of {self._max_calls} reached; stopped before {method} {path}.",
                path=path,
            )
        now = self._clock()
        if self._last_start is not None:
            wait = self._last_start + self._interval - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_start = now

        record: dict[str, Any] = {
            "seq": len(self.calls) + 1,
            "method": method,
            "path": path,
            "status": None,
            "ms": None,
        }
        self.calls.append(record)
        try:
            response = send()
        except TransportError as exc:
            record["status"] = "network error"
            record["ms"] = round((self._clock() - now) * 1000)
            self._log(record, query, body if log_bodies else None, None)
            raise TTApiError(
                f"Could not reach TT for {method} {path} ({type(exc).__name__}).", path=path
            ) from None
        record["status"] = response.status_code
        record["ms"] = round((self._clock() - now) * 1000)
        if log_bodies:
            self._log(record, query, body, response.text)
        else:
            self._log(record, query, None, None)
        return response

    def _log(
        self,
        record: Mapping[str, Any],
        query: Mapping[str, Any] | None,
        body: Any,
        response_text: str | None,
    ) -> None:
        if self.api_log is None:
            return
        self.api_log.record(
            environment=self.environment,
            method=record["method"],
            path=record["path"],
            query=query,
            status=record["status"],
            ms=record["ms"],
            request_body=body,
            response_text=response_text,
        )

    # --- token ------------------------------------------------------------------

    def _fetch_token(self) -> None:
        creds = self._creds()
        path = f"/ttid/{self.environment}/token"
        query = {"requestId": self._request_id()}
        response = self._send(
            "POST",
            path,
            lambda: self._http.post_token(
                data={"grant_type": "user_app", "app_key": creds.app_secret},
                headers={"x-api-key": creds.app_key, "Accept": "application/json"},
                params=query,
            ),
            query=query,
            log_bodies=False,
        )
        # Nothing from the token response is ever put in a message: it may echo secrets.
        if response.status_code != 200:
            raise TTApiError(
                f"TT refused the sign-in (HTTP {response.status_code}). Check the stored "
                "credentials with scripts/set_credentials.py.",
                path=path,
                status_code=response.status_code,
            )
        try:
            body = json.loads(response.text)
            token = body["access_token"]
            lifetime = float(body.get("seconds_until_expiry", 0))
        except (ValueError, KeyError, TypeError):
            raise TTApiError("TT's sign-in reply could not be read.", path=path) from None
        if not isinstance(token, str) or not token:
            raise TTApiError("TT's sign-in reply had no access token.", path=path)
        self._token = token
        self._token_expires_at = self._clock() + max(0.0, lifetime - TOKEN_REFRESH_MARGIN_SECONDS)

    def _headers(self) -> dict[str, str]:
        if self._token is None or self._clock() >= self._token_expires_at:
            self._fetch_token()
        return {
            "x-api-key": self._creds().app_key,
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _call(
        self,
        method: str,
        path: str,
        send: Callable[[dict[str, str], dict[str, Any]], Response],
        params: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> TTResponse:
        """One logical call: a fresh token once on 401, a short wait on 429."""
        refreshed = False
        rate_limited = 0
        while True:
            headers = self._headers()
            query = {**(params or {}), "requestId": self._request_id()}
            response = self._send(
                method,
                path,
                lambda headers=headers, query=query: send(headers, query),
                query=query,
                body=body,
            )
            if response.status_code == 401 and not refreshed:
                refreshed = True
                self._token = None
                continue
            if response.status_code == 429 and rate_limited < MAX_RATE_LIMIT_RETRIES:
                rate_limited += 1
                self._sleep(_retry_after(response))
                continue
            break
        return _checked(response, path)

    def path_for(self, service: str, resource: str) -> str:
        return f"/{service}/{self.environment}/{resource.strip('/')}"

    # --- GET --------------------------------------------------------------------

    def get(
        self, service: str, resource: str, params: Mapping[str, Any] | None = None
    ) -> TTResponse:
        """GET ``/<service>/<environment>/<resource>`` and return the checked JSON body."""
        path = self.path_for(service, resource)
        return self._call(
            "GET",
            path,
            lambda headers, query: self._http.get(path, params=query, headers=headers),
            params,
        )

    def pages(
        self,
        service: str,
        resource: str,
        params: Mapping[str, Any] | None = None,
        *,
        max_pages: int | None = None,
    ) -> Iterator[Page]:
        """Follow ``nextPageKey`` until ``lastPage`` is "true", the key is empty, or the cap."""
        query = dict(params or {})
        seen_keys: set[str] = set()
        number = 0
        while True:
            number += 1
            response = self.get(service, resource, query)
            next_key = str(response.body.get("nextPageKey") or "")
            at_end = is_on(response.body.get("lastPage")) or not next_key
            yield Page(number=number, response=response, reached_end=at_end)
            if at_end:
                return
            if max_pages is not None and number >= max_pages:
                return
            if next_key in seen_keys:
                raise TTApiError(f"TT repeated a page key on {resource}; stopped.", path=resource)
            seen_keys.add(next_key)
            query["nextPageKey"] = next_key


def _retry_after(response: Response) -> float:
    try:
        return max(0.0, min(30.0, float(response.headers.get("Retry-After", ""))))
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


def _checked(response: Response, path: str) -> TTResponse:
    code = response.status_code
    text = response.text
    try:
        body = json.loads(text)
    except ValueError:
        body = None

    detail = ""
    if isinstance(body, dict):
        parts = [str(body[k]) for k in ("status", "status_message", "message") if body.get(k)]
        detail = " - " + "; ".join(parts)[:200] if parts else ""

    if code != 200:
        raise TTApiError(f"TT returned HTTP {code} for {path}{detail}", path=path, status_code=code)
    if not isinstance(body, dict):
        raise TTApiError(
            f"TT's reply for {path} was not a JSON object.", path=path, status_code=code
        )
    # A body whose status is not "Ok" is an error, even on HTTP 200.
    if "status" in body and body["status"] != "Ok":
        raise TTApiError(f"TT reported an error for {path}{detail}", path=path, status_code=code)
    return TTResponse(body=body, text=text)
