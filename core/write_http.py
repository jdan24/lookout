"""Write guard for the Clone page: the only way Lookout can change anything in TT.

It works in TT UAT (``ext_uat_cert``) only. A ``WriteClient`` cannot be created for any
other environment, and its transport allows these requests and nothing else:

* ``POST /ttaccount/ext_uat_cert/account/clone``                  clone one account
* ``POST /ttsetup/ext_uat_cert/ordertagdefaults``                 create Order Tag Defaults
* ``POST /ttid/ext_uat_cert/token``                               the token call

No GET, PUT, DELETE or other path, and never Live. Reads go through the read-only
guard in ``core/readonly_http.py``, which is unchanged by this module.

DO NOT widen this list or add Live without the user's explicit say-so. The rules are
constants on purpose: no config file or caller can change them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx

from core.readonly_http import (
    ALLOWED_HOSTS,
    UAT_ENVIRONMENT,
    ReadOnlyViolation,
    _real_transport,
    checked_url,
    token_path,
)

WRITE_ENVIRONMENT = UAT_ENVIRONMENT
WRITE_PATHS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"/ttaccount/{WRITE_ENVIRONMENT}/account/clone"),
    re.compile(rf"/ttsetup/{WRITE_ENVIRONMENT}/ordertagdefaults"),
)


class WriteRefused(ReadOnlyViolation):
    """A write was refused because it is not on the Clone page's short list."""


def is_write_path(path: str) -> bool:
    return any(pattern.fullmatch(path) for pattern in WRITE_PATHS)


def check_write(method: str, url: str | httpx.URL) -> None:
    """Raise ``WriteRefused`` unless the request is one of the allowed UAT POSTs."""
    try:
        verb, path, shown = checked_url(method, url)
    except WriteRefused:
        raise
    except ReadOnlyViolation as exc:
        raise WriteRefused(str(exc)) from None
    if verb != "POST":
        raise WriteRefused(f"Refused: the write client only sends POST ({shown}).")
    if path == token_path(WRITE_ENVIRONMENT) or is_write_path(path):
        return
    raise WriteRefused(f"Refused: not one of the Clone page's UAT calls ({shown}).")


def _check_environment(environment: object) -> None:
    if environment != WRITE_ENVIRONMENT:
        raise WriteRefused(
            f"Refused: writing to TT is allowed in {WRITE_ENVIRONMENT} (UAT) only, "
            f"not {environment!r}."
        )


class WriteGuardedTransport(httpx.BaseTransport):
    """Wraps a transport and checks every outgoing request against the write list."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        check_write(request.method, request.url)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class WriteClient:
    """The Clone page's HTTP client. UAT only; it can ``post`` and ``post_token``."""

    def __init__(
        self,
        host: str = "ttrestapi.trade.tt",
        *,
        environment: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        _check_environment(environment)
        if host not in ALLOWED_HOSTS:
            raise WriteRefused(f"Refused: host {host!r} is not allow-listed.")
        self.host = host
        self.environment = environment
        self._token_path = token_path(environment)
        self._client = httpx.Client(
            base_url=f"https://{host}",
            transport=WriteGuardedTransport(transport or _real_transport(host)),
            follow_redirects=False,
            trust_env=False,
            timeout=timeout,
        )

    def post(
        self,
        path: str,
        *,
        json: Any,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if not isinstance(path, str) or not is_write_path(path):
            raise WriteRefused(f"Refused: {path!r} is not one of the Clone page's UAT calls.")
        return self._client.request("POST", path, json=json, params=params, headers=headers)

    def post_token(
        self,
        *,
        data: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        return self._client.request(
            "POST", self._token_path, data=data, headers=headers, params=params
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
