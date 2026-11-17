"""The Clone page's write guard: a few UAT POSTs and nothing else, never Live."""

from __future__ import annotations

import httpx
import pytest

from core.readonly_http import ReadOnlyViolation, check_request
from core.tt_writer import TTWriter
from core.write_http import (
    WRITE_ENVIRONMENT,
    WRITE_PATHS,
    WriteClient,
    WriteRefused,
    check_write,
)

BASE = "https://ttrestapi.trade.tt"
UAT = "ext_uat_cert"
LIVE = "ext_prod_live"


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"status": "Ok"})


def test_write_list_is_exactly_the_agreed_one() -> None:
    assert WRITE_ENVIRONMENT == UAT
    assert [p.pattern for p in WRITE_PATHS] == [
        "/ttaccount/ext_uat_cert/account/clone",
        "/ttsetup/ext_uat_cert/ordertagdefaults",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/ttaccount/ext_uat_cert/account/clone",
        "/ttsetup/ext_uat_cert/ordertagdefaults",
        "/ttid/ext_uat_cert/token",
    ],
)
def test_allowed_writes_pass(path: str) -> None:
    check_write("POST", BASE + path)
    check_write("post", BASE + path + "?requestId=x")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/ttaccount/ext_prod_live/account/clone"),  # Live: never
        ("POST", "/ttsetup/ext_prod_live/ordertagdefaults"),
        ("POST", "/ttsetup/ext_prod_live/ordertagdefaults/123"),
        ("POST", "/ttid/ext_prod_live/token"),
        ("POST", "/ttaccount/ext_uat_cert/account"),  # create, not clone
        ("POST", "/ttaccount/ext_uat_cert/account/clone/1"),
        ("POST", "/ttaccount/ext_uat_cert/account/riskSettings"),
        ("POST", "/ttsetup/ext_uat_cert/ordertagdefaults/123"),  # updates: not needed
        ("POST", "/ttsetup/ext_uat_cert/ordertagdefaults/abc"),
        ("POST", "/ttsetup/ext_uat_cert/ordertagdefaults/profiles"),
        ("POST", "/ttuser/ext_uat_cert/user"),
        ("GET", "/ttaccount/ext_uat_cert/account/clone"),
        ("GET", "/ttaccount/ext_uat_cert/accounts"),
        ("PUT", "/ttsetup/ext_uat_cert/ordertagdefaults/123"),
        ("DELETE", "/ttsetup/ext_uat_cert/ordertagdefaults/123"),
        ("PATCH", "/ttsetup/ext_uat_cert/ordertagdefaults/123"),
    ],
)
def test_everything_else_is_refused(method: str, path: str) -> None:
    with pytest.raises(WriteRefused):
        check_write(method, BASE + path)


@pytest.mark.parametrize(
    "url",
    [
        "http://ttrestapi.trade.tt/ttaccount/ext_uat_cert/account/clone",
        "https://evil.example/ttaccount/ext_uat_cert/account/clone",
        "https://ttrestapi.trade.tt:8443/ttaccount/ext_uat_cert/account/clone",
        "https://user:pw@ttrestapi.trade.tt/ttaccount/ext_uat_cert/account/clone",
        "https://ttrestapi.trade.tt/ttaccount/ext_uat_cert/x/../account/clone",
        "https://ttrestapi.trade.tt/ttaccount/ext_uat_cert/account%2Fclone",
    ],
)
def test_unsafe_urls_are_refused(url: str) -> None:
    with pytest.raises(WriteRefused):
        check_write("POST", url)


def test_the_read_guard_still_refuses_these_writes() -> None:
    for path in ("/ttaccount/ext_uat_cert/account/clone", "/ttsetup/ext_uat_cert/ordertagdefaults"):
        with pytest.raises(ReadOnlyViolation):
            check_request("POST", BASE + path, UAT)


@pytest.mark.parametrize("environment", [LIVE, "", None, "ext_uat_cert2"])
def test_no_write_client_outside_uat(environment: object) -> None:
    with pytest.raises(WriteRefused):
        WriteClient(environment=environment, transport=Recorder().transport)  # type: ignore[arg-type]


def test_write_client_posts_only_the_allowed_paths() -> None:
    recorder = Recorder()
    with WriteClient(environment=UAT, transport=recorder.transport) as client:
        client.post("/ttaccount/ext_uat_cert/account/clone", json={"a": 1})
        client.post_token(data={"grant_type": "x"})
        for bad in ("/ttaccount/ext_prod_live/account/clone", "https://evil.example/x", "//x/y"):
            with pytest.raises(WriteRefused):
                client.post(bad, json={})
        assert not hasattr(client, "get")
    assert [(r.method, r.url.path) for r in recorder.requests] == [
        ("POST", "/ttaccount/ext_uat_cert/account/clone"),
        ("POST", "/ttid/ext_uat_cert/token"),
    ]


def test_write_client_refuses_unknown_hosts() -> None:
    with pytest.raises(WriteRefused):
        WriteClient("evil.example", environment=UAT, transport=Recorder().transport)


def test_redirects_are_not_followed() -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    with WriteClient(environment=UAT, transport=httpx.MockTransport(redirect)) as client:
        reply = client.post("/ttsetup/ext_uat_cert/ordertagdefaults", json=[])
    assert reply.status_code == 302


def test_writer_needs_a_write_client_and_cannot_read() -> None:
    from core.readonly_http import ReadOnlyClient
    from core.tt_client import TTApiError

    with pytest.raises(TypeError):
        TTWriter(ReadOnlyClient(environment=UAT), lambda: None)  # type: ignore[arg-type]
    writer = TTWriter(WriteClient(environment=UAT, transport=Recorder().transport), lambda: None)
    with pytest.raises(TTApiError, match="cannot read"):
        writer.get("ttaccount", "accounts")
