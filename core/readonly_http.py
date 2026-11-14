"""Read-only HTTP guard for Trading Technologies (TT).

Lookout never writes to TT. This module is the single place where an HTTP library
is imported, and every request that leaves the process passes through
``check_request`` at the transport layer.

Allowed, and nothing else:

* ``GET  https://<allowed host>/<read service>/ext_prod_live/<resource...>``
* ``POST https://<allowed host>/ttid/ext_prod_live/token`` (the one token call)

DO NOT loosen this guard or widen the allow-lists below. They are constants on
purpose: no config file or caller can change them.
"""

from __future__ import annotations

import re
import ssl
import urllib.request
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx
import truststore

ALLOWED_HOSTS: frozenset[str] = frozenset({"apigateway.trade.tt", "ttrestapi.trade.tt"})
ALLOWED_ENVIRONMENT = "ext_prod_live"
READ_SERVICES: frozenset[str] = frozenset({"ttaccount", "ttuser", "ttgroup", "ttsetup", "ttpds"})
TOKEN_PATH = f"/ttid/{ALLOWED_ENVIRONMENT}/token"

# One or more plain segments, optional single trailing slash. No percent-encoding,
# no backslashes, no empty segments: nothing that a server could read differently.
_PLAIN_PATH = re.compile(r"(?:/[A-Za-z0-9_.~-]+)+/?")
_DOT_SEGMENT = re.compile(r"(?:^|/)\.{1,2}(?:/|$)")

# Re-exported so other modules can name these types without importing httpx.
Response = httpx.Response
TransportError = httpx.TransportError


class ReadOnlyViolation(Exception):
    """A request was refused because it is not on the read-only allow-list."""


def check_request(method: str, url: str | httpx.URL) -> None:
    """Raise ``ReadOnlyViolation`` unless the request is on the allow-list."""
    # httpx tidies "." and ".." away while parsing, so look at the text first.
    if isinstance(url, str) and _DOT_SEGMENT.search(url.split("?", 1)[0].split("#", 1)[0]):
        raise ReadOnlyViolation("Refused: dot segments are not allowed in the URL path.")
    try:
        parsed = httpx.URL(url) if isinstance(url, str) else url
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise ReadOnlyViolation(f"Refused: unparseable URL ({exc}).") from exc

    verb = method.upper() if isinstance(method, str) else ""
    shown = f"{verb or method!r} {parsed.scheme}://{parsed.host}{parsed.path}"

    if parsed.scheme != "https":
        raise ReadOnlyViolation(f"Refused: only https is allowed ({shown}).")
    if parsed.userinfo:
        raise ReadOnlyViolation(f"Refused: credentials in the URL are not allowed ({shown}).")
    if parsed.host not in ALLOWED_HOSTS:
        raise ReadOnlyViolation(f"Refused: host is not allow-listed ({shown}).")
    if parsed.port is not None:
        raise ReadOnlyViolation(f"Refused: only the default https port is allowed ({shown}).")

    try:
        path = parsed.raw_path.split(b"?", 1)[0].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ReadOnlyViolation(f"Refused: non-ASCII path ({shown}).") from exc
    if not _PLAIN_PATH.fullmatch(path):
        raise ReadOnlyViolation(f"Refused: path is not in plain form ({shown}).")
    segments = [s for s in path.split("/") if s]
    if any(s in {".", ".."} for s in segments):
        raise ReadOnlyViolation(f"Refused: dot segments are not allowed ({shown}).")

    if verb == "POST":
        if path != TOKEN_PATH:
            raise ReadOnlyViolation(f"Refused: the only POST allowed is the token call ({shown}).")
        return

    if verb != "GET":
        raise ReadOnlyViolation(f"Refused: method is not allowed, Lookout is read-only ({shown}).")

    if len(segments) < 3:
        raise ReadOnlyViolation(f"Refused: expected /<service>/<environment>/<resource> ({shown}).")
    service, environment = segments[0], segments[1]
    if service not in READ_SERVICES:
        raise ReadOnlyViolation(f"Refused: service is not allow-listed for reading ({shown}).")
    if environment != ALLOWED_ENVIRONMENT:
        raise ReadOnlyViolation(f"Refused: environment is not {ALLOWED_ENVIRONMENT} ({shown}).")


class GuardedTransport(httpx.BaseTransport):
    """Wraps a transport and checks every outgoing request, redirects included."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        check_request(request.method, request.url)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def system_proxy_for(host: str) -> str | None:
    """The system https proxy for ``host`` (Windows settings or env vars), if any."""
    if urllib.request.proxy_bypass(host):
        return None
    return urllib.request.getproxies().get("https") or None


def _real_transport(host: str) -> httpx.BaseTransport:
    # truststore uses the OS certificate store, so corporate SSL inspection works.
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.HTTPTransport(verify=context, proxy=system_proxy_for(host))


class ReadOnlyClient:
    """The only HTTP client in Lookout. It can ``get`` and ``post_token``; nothing else."""

    def __init__(
        self,
        host: str = "ttrestapi.trade.tt",
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if host not in ALLOWED_HOSTS:
            raise ReadOnlyViolation(f"Refused: host {host!r} is not allow-listed.")
        self.host = host
        self._client = httpx.Client(
            base_url=f"https://{host}",
            transport=GuardedTransport(transport or _real_transport(host)),
            follow_redirects=False,
            trust_env=False,
            timeout=timeout,
        )

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        # A path only: never a full URL, "//host/..." or anything httpx would rewrite.
        if (
            not isinstance(path, str)
            or not _PLAIN_PATH.fullmatch(path)
            or _DOT_SEGMENT.search(path)
        ):
            raise ReadOnlyViolation(f"Refused: {path!r} is not a plain absolute path.")
        return self._client.request("GET", path, params=params, headers=headers)

    def post_token(
        self,
        *,
        data: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """The single write-shaped call Lookout makes: asking TT for an access token."""
        return self._client.request("POST", TOKEN_PATH, data=data, headers=headers, params=params)

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
