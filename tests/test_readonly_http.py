"""The read-only guard: allowed calls pass, everything else raises before any I/O."""

from __future__ import annotations

import httpx
import pytest

from core import readonly_http
from core.readonly_http import (
    ALLOWED_ENVIRONMENTS,
    ALLOWED_HOSTS,
    LIVE_ENVIRONMENT,
    READ_SERVICES,
    UAT_ENVIRONMENT,
    GuardedTransport,
    ReadOnlyClient,
    ReadOnlyViolation,
    check_request,
    token_path,
)

BASE = "https://ttrestapi.trade.tt"
ACCOUNTS = f"{BASE}/ttaccount/ext_prod_live/accounts"


class Recorder:
    """A fake TT that records every request that reaches the wire."""

    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response or httpx.Response(200, json={"status": "Ok"})


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


# --- the allow-list itself -------------------------------------------------------


def test_allow_list_is_exactly_the_agreed_one() -> None:
    assert ALLOWED_HOSTS == {"apigateway.trade.tt", "ttrestapi.trade.tt"}
    assert READ_SERVICES == {"ttaccount", "ttuser", "ttgroup", "ttsetup", "ttpds"}
    assert ALLOWED_ENVIRONMENTS == {"ext_prod_live", "ext_uat_cert"}
    assert (LIVE_ENVIRONMENT, UAT_ENVIRONMENT) == ("ext_prod_live", "ext_uat_cert")
    assert token_path(LIVE_ENVIRONMENT) == "/ttid/ext_prod_live/token"
    assert token_path(UAT_ENVIRONMENT) == "/ttid/ext_uat_cert/token"


# --- check_request: allowed ------------------------------------------------------

ALLOWED = [
    ("GET", ACCOUNTS),
    ("get", ACCOUNTS),
    ("GET", f"{ACCOUNTS}?nextPageKey=abc123&requestId=lookout--1"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/account/7654321"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/account/7654321/users"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/connections/"),
    ("GET", f"{BASE}/ttuser/ext_prod_live/user/12345"),
    ("GET", f"{BASE}/ttgroup/ext_prod_live/groups"),
    ("GET", f"{BASE}/ttsetup/ext_prod_live/ordertagdefaults"),
    ("GET", f"{BASE}/ttsetup/ext_prod_live/ordertagdefaults/fields"),
    ("GET", f"{BASE}/ttpds/ext_prod_live/productfamilies?marketId=7"),
    ("GET", "https://apigateway.trade.tt/ttpds/ext_prod_live/markets"),
    ("GET", "https://TTRESTAPI.trade.tt/ttpds/ext_prod_live/markets"),
    ("GET", "https://ttrestapi.trade.tt:443/ttpds/ext_prod_live/markets"),
    ("POST", f"{BASE}/ttid/ext_prod_live/token"),
    ("POST", f"{BASE}/ttid/ext_prod_live/token?requestId=lookout--1"),
    ("POST", "https://apigateway.trade.tt/ttid/ext_prod_live/token"),
]


@pytest.mark.parametrize(("method", "url"), ALLOWED)
def test_allowed_requests_pass(method: str, url: str) -> None:
    check_request(method, url)


# --- check_request: blocked ------------------------------------------------------

BLOCKED = [
    # write methods on a data service
    ("PUT", ACCOUNTS),
    ("PATCH", ACCOUNTS),
    ("DELETE", ACCOUNTS),
    ("POST", ACCOUNTS),
    ("POST", f"{BASE}/ttaccount/ext_prod_live/account"),
    ("POST", f"{BASE}/ttsetup/ext_prod_live/ordertagdefaults"),
    ("DELETE", f"{BASE}/ttaccount/ext_prod_live/account/7654321"),
    # other methods are not needed, so they are refused too
    ("HEAD", ACCOUNTS),
    ("OPTIONS", ACCOUNTS),
    ("TRACE", ACCOUNTS),
    ("CONNECT", ACCOUNTS),
    ("", ACCOUNTS),
    ("GETX", ACCOUNTS),
    # the token path accepts POST only, and POST accepts the token path only
    ("GET", f"{BASE}/ttid/ext_prod_live/token"),
    ("PUT", f"{BASE}/ttid/ext_prod_live/token"),
    ("DELETE", f"{BASE}/ttid/ext_prod_live/token"),
    ("POST", f"{BASE}/ttid/ext_prod_live/token/"),
    ("POST", f"{BASE}/ttid/ext_prod_live/token/extra"),
    ("POST", f"{BASE}/ttid/ext_prod_live/tokens"),
    ("POST", f"{BASE}/ttid/ext_uat_cert/token"),
    ("POST", f"{BASE}/TTID/ext_prod_live/token"),
    ("POST", f"{BASE}/ttid/ext_prod_live/users"),
    ("GET", f"{BASE}/ttid/ext_prod_live/users"),
    # wrong environment
    ("GET", f"{BASE}/ttaccount/ext_uat_cert/accounts"),
    ("GET", f"{BASE}/ttaccount/ext_prod_sim/accounts"),
    ("GET", f"{BASE}/ttaccount/EXT_PROD_LIVE/accounts"),
    ("GET", f"{BASE}/ttaccount/accounts"),
    # services outside the allow-list
    ("GET", f"{BASE}/ttledger/ext_prod_live/fills"),
    ("GET", f"{BASE}/ttmonitor/ext_prod_live/position"),
    ("GET", f"{BASE}/ttbacktest/ext_prod_live/x"),
    ("GET", f"{BASE}/TTACCOUNT/ext_prod_live/accounts"),
    ("GET", f"{BASE}/ttaccountx/ext_prod_live/accounts"),
    # incomplete paths
    ("GET", BASE),
    ("GET", f"{BASE}/"),
    ("GET", f"{BASE}/ttaccount"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/"),
    # hosts
    ("GET", "https://evil.example/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://ttrestapi.trade.tt.evil.example/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://evil-ttrestapi.trade.tt/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://sub.ttrestapi.trade.tt/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://trade.tt/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://ttrestapi.trade.tt./ttaccount/ext_prod_live/accounts"),
    ("GET", "https://ttrestapi.trade.tt@evil.example/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://user:pw@ttrestapi.trade.tt/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://ttrestapi.trade.tt:8443/ttaccount/ext_prod_live/accounts"),
    ("GET", "https://127.0.0.1/ttaccount/ext_prod_live/accounts"),
    ("POST", "https://evil.example/ttid/ext_prod_live/token"),
    # schemes
    ("GET", "http://ttrestapi.trade.tt/ttaccount/ext_prod_live/accounts"),
    ("GET", "ftp://ttrestapi.trade.tt/ttaccount/ext_prod_live/accounts"),
    ("GET", "/ttaccount/ext_prod_live/accounts"),
    ("GET", "ttrestapi.trade.tt/ttaccount/ext_prod_live/accounts"),
    # path tricks
    ("GET", f"{BASE}/ttaccount/ext_prod_live/%2e%2e/%2e%2e/ttledger/ext_prod_live/fills"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/a%2Fb"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/a%5Cb"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/a\\b"),
    ("GET", f"{BASE}//ttaccount/ext_prod_live/accounts"),
    ("GET", f"{BASE}/ttaccount//ext_prod_live/accounts"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/accounts//"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/acc ounts"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/accounts;x=1"),
    ("POST", f"{BASE}/ttid/ext_prod_live/%74oken"),
]


# httpx resolves "." and ".." while building a request, before the transport sees it.
DOT_SEGMENTS = [
    ("GET", f"{BASE}/ttaccount/ext_prod_live/./accounts"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/../../ttledger/ext_prod_live/fills"),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/.."),
    ("GET", f"{BASE}/ttaccount/ext_prod_live/accounts/../../../ttid/ext_prod_live/token"),
    ("POST", f"{BASE}/ttaccount/ext_prod_live/../../ttid/ext_prod_live/token"),
]


@pytest.mark.parametrize(("method", "url"), DOT_SEGMENTS)
def test_dot_segments_are_refused_as_text(method: str, url: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        check_request(method, url)


@pytest.mark.parametrize(("method", "url"), DOT_SEGMENTS)
def test_dot_segments_cannot_smuggle_a_blocked_request(
    method: str, url: str, recorder: Recorder
) -> None:
    """Whatever httpx resolves them to is checked; only an allowed request can leave."""
    with httpx.Client(transport=GuardedTransport(recorder.transport)) as client:
        try:
            client.request(method, url)
        except ReadOnlyViolation:
            pass
    for sent in recorder.requests:
        assert "/." not in sent.url.raw_path.decode()
        check_request(sent.method, sent.url)


@pytest.mark.parametrize(("method", "url"), BLOCKED)
def test_blocked_requests_raise(method: str, url: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        check_request(method, url)


@pytest.mark.parametrize(("method", "url"), BLOCKED)
def test_blocked_requests_never_reach_the_wire(method: str, url: str, recorder: Recorder) -> None:
    """Sent through a plain httpx client, the guard stops them at the transport."""
    with httpx.Client(transport=GuardedTransport(recorder.transport)) as client:
        with pytest.raises((ReadOnlyViolation, httpx.UnsupportedProtocol, httpx.InvalidURL)):
            client.request(method or "GET ", url)
    assert recorder.requests == []


def test_non_string_method_is_refused() -> None:
    with pytest.raises(ReadOnlyViolation):
        check_request(None, ACCOUNTS)  # type: ignore[arg-type]


# --- GuardedTransport ------------------------------------------------------------


def test_allowed_get_reaches_the_wire(recorder: Recorder) -> None:
    with httpx.Client(transport=GuardedTransport(recorder.transport)) as client:
        response = client.get(ACCOUNTS)
    assert response.status_code == 200
    assert [(r.method, str(r.url)) for r in recorder.requests] == [("GET", ACCOUNTS)]


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "POST"])
def test_send_and_stream_are_guarded_too(method: str, recorder: Recorder) -> None:
    with httpx.Client(transport=GuardedTransport(recorder.transport)) as client:
        with pytest.raises(ReadOnlyViolation):
            client.send(client.build_request(method, ACCOUNTS, json={"name": "x"}))
        with pytest.raises(ReadOnlyViolation):
            with client.stream(method, ACCOUNTS):
                pass
    assert recorder.requests == []


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/ttaccount/ext_prod_live/accounts",
        "http://ttrestapi.trade.tt/ttaccount/ext_prod_live/accounts",
        "https://ttrestapi.trade.tt/ttledger/ext_prod_live/fills",
    ],
)
def test_redirects_are_checked_even_if_a_client_follows_them(location: str) -> None:
    recorder = Recorder(httpx.Response(302, headers={"Location": location}))
    client = httpx.Client(transport=GuardedTransport(recorder.transport), follow_redirects=True)
    with client, pytest.raises(ReadOnlyViolation):
        client.get(ACCOUNTS)
    assert len(recorder.requests) == 1  # the original GET only; the redirect never left


def test_guard_close_closes_the_inner_transport() -> None:
    closed: list[bool] = []

    class Inner(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        def close(self) -> None:
            closed.append(True)

    GuardedTransport(Inner()).close()
    assert closed == [True]


# --- ReadOnlyClient --------------------------------------------------------------


def test_client_get(recorder: Recorder) -> None:
    with ReadOnlyClient(transport=recorder.transport) as client:
        response = client.get(
            "/ttaccount/ext_prod_live/accounts",
            params={"nextPageKey": "k1"},
            headers={"x-api-key": "key"},
        )
    assert response.json() == {"status": "Ok"}
    (request,) = recorder.requests
    assert request.method == "GET"
    assert str(request.url) == f"{ACCOUNTS}?nextPageKey=k1"
    assert request.headers["x-api-key"] == "key"


def test_client_post_token_goes_to_the_token_path_only(recorder: Recorder) -> None:
    with ReadOnlyClient(transport=recorder.transport) as client:
        client.post_token(data={"grant_type": "user_app"}, headers={"x-api-key": "key"})
    (request,) = recorder.requests
    assert request.method == "POST"
    assert request.url.path == token_path(LIVE_ENVIRONMENT)
    assert request.url.host == "ttrestapi.trade.tt"


def test_client_exposes_no_write_methods() -> None:
    public = {name for name in dir(ReadOnlyClient) if not name.startswith("_")}
    assert public == {"get", "post_token", "close"}


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/ttaccount/ext_prod_live/accounts",
        "//evil.example/ttaccount/ext_prod_live/accounts",
        "/ttledger/ext_prod_live/fills",
        "/ttaccount/ext_uat_cert/accounts",
        "/ttid/ext_prod_live/token",
        "/ttaccount/ext_prod_live/../../ttledger/ext_prod_live/fills",
        "/ttaccount/ext_prod_live/./accounts",
        "ttaccount/ext_prod_live/accounts",
        "/ttaccount/ext_prod_live/accounts?x=1",
        "",
    ],
)
def test_client_get_refuses_anything_off_the_allow_list(path: str, recorder: Recorder) -> None:
    with ReadOnlyClient(transport=recorder.transport) as client:
        with pytest.raises(ReadOnlyViolation):
            client.get(path)
    assert recorder.requests == []


@pytest.mark.parametrize("host", ["evil.example", "ttrestapi.trade.tt.evil.example", "", "tt"])
def test_client_refuses_unknown_hosts(host: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        ReadOnlyClient(host=host)


def test_client_accepts_both_allowed_hosts(recorder: Recorder) -> None:
    for host in sorted(ALLOWED_HOSTS):
        with ReadOnlyClient(host=host, transport=recorder.transport) as client:
            client.get("/ttpds/ext_prod_live/markets")
    assert [r.url.host for r in recorder.requests] == sorted(ALLOWED_HOSTS)


def test_client_does_not_follow_redirects() -> None:
    recorder = Recorder(httpx.Response(302, headers={"Location": ACCOUNTS + "?again=1"}))
    with ReadOnlyClient(transport=recorder.transport) as client:
        response = client.get("/ttaccount/ext_prod_live/accounts")
    assert response.status_code == 302
    assert len(recorder.requests) == 1


def test_client_ignores_proxy_environment_variables(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    """Env proxies must not mount a second, unguarded route around the guard."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:8080")
    with ReadOnlyClient(transport=recorder.transport) as client:
        client.get("/ttpds/ext_prod_live/markets")
        with pytest.raises(ReadOnlyViolation):
            client.get("/ttledger/ext_prod_live/fills")
    assert len(recorder.requests) == 1


def test_real_transport_is_wrapped_by_the_guard() -> None:
    """With no transport injected, a blocked call still raises before any connection."""
    with ReadOnlyClient() as client:
        with pytest.raises(ReadOnlyViolation):
            client.get("/ttledger/ext_prod_live/fills")


# --- proxy lookup ----------------------------------------------------------------


def test_system_proxy_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        readonly_http.urllib.request, "getproxies", lambda: {"https": "http://proxy.corp:8080"}
    )
    monkeypatch.setattr(readonly_http.urllib.request, "proxy_bypass", lambda host: False)
    assert readonly_http.system_proxy_for("ttrestapi.trade.tt") == "http://proxy.corp:8080"

    monkeypatch.setattr(readonly_http.urllib.request, "proxy_bypass", lambda host: True)
    assert readonly_http.system_proxy_for("ttrestapi.trade.tt") is None

    monkeypatch.setattr(readonly_http.urllib.request, "proxy_bypass", lambda host: False)
    monkeypatch.setattr(readonly_http.urllib.request, "getproxies", lambda: {})
    assert readonly_http.system_proxy_for("ttrestapi.trade.tt") is None


# --- Live and UAT are kept apart -----------------------------------------------------

UAT = "ext_uat_cert"
UAT_ACCOUNTS = f"{BASE}/ttaccount/{UAT}/accounts"


def test_uat_requests_pass_only_for_a_uat_guard() -> None:
    check_request("GET", UAT_ACCOUNTS, UAT)
    check_request("POST", f"{BASE}/ttid/{UAT}/token", UAT)
    for method, url in [("GET", UAT_ACCOUNTS), ("POST", f"{BASE}/ttid/{UAT}/token")]:
        with pytest.raises(ReadOnlyViolation):
            check_request(method, url)  # the default guard is Live
        with pytest.raises(ReadOnlyViolation):
            check_request(method, url, LIVE_ENVIRONMENT)


def test_a_uat_guard_refuses_live_and_everything_else() -> None:
    for method, url in [
        ("GET", ACCOUNTS),
        ("POST", f"{BASE}/ttid/ext_prod_live/token"),
        ("POST", UAT_ACCOUNTS),
        ("PUT", UAT_ACCOUNTS),
        ("DELETE", UAT_ACCOUNTS),
        ("GET", f"{BASE}/ttid/{UAT}/token"),
        ("POST", f"{BASE}/ttid/{UAT}/token/extra"),
        ("GET", f"{BASE}/ttledger/{UAT}/fills"),
        ("GET", f"https://evil.example/ttaccount/{UAT}/accounts"),
        ("GET", f"{BASE}/ttaccount/ext_prod_sim/accounts"),
    ]:
        with pytest.raises(ReadOnlyViolation):
            check_request(method, url, UAT)


@pytest.mark.parametrize("environment", ["ext_prod_sim", "EXT_UAT_CERT", "", "ext_uat_cert/x", None])
def test_no_other_environment_can_be_named(environment: object, recorder: Recorder) -> None:
    with pytest.raises(ReadOnlyViolation, match="environment"):
        check_request("GET", ACCOUNTS, environment)  # type: ignore[arg-type]
    with pytest.raises(ReadOnlyViolation, match="environment"):
        GuardedTransport(recorder.transport, environment)  # type: ignore[arg-type]
    with pytest.raises(ReadOnlyViolation, match="environment"):
        ReadOnlyClient(environment=environment, transport=recorder.transport)  # type: ignore[arg-type]
    with pytest.raises(ReadOnlyViolation, match="environment"):
        token_path(environment)  # type: ignore[arg-type]


def test_a_client_stays_in_the_environment_it_was_made_for(recorder: Recorder) -> None:
    with ReadOnlyClient(environment=UAT, transport=recorder.transport) as uat:
        assert uat.environment == UAT
        uat.get(f"/ttaccount/{UAT}/accounts")
        uat.post_token(data={"grant_type": "user_app"})
        with pytest.raises(ReadOnlyViolation):
            uat.get("/ttaccount/ext_prod_live/accounts")
    with ReadOnlyClient(transport=recorder.transport) as live:
        assert live.environment == LIVE_ENVIRONMENT
        with pytest.raises(ReadOnlyViolation):
            live.get(f"/ttaccount/{UAT}/accounts")
    assert [(r.method, r.url.path) for r in recorder.requests] == [
        ("GET", f"/ttaccount/{UAT}/accounts"),
        ("POST", f"/ttid/{UAT}/token"),
    ]
