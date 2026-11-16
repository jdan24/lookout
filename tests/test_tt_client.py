from __future__ import annotations

import re

import httpx
import pytest

from core.credentials import parse_secret
from core.readonly_http import ReadOnlyClient
from core.tt_client import CallCapReached, TTApiError, TTClient
from tests.fixtures import synthetic
from tests.fixtures.fake_tt import SECRET, TOKEN, FakeTT


class FakeTime:
    """A clock that only moves when the client sleeps or a test advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


@pytest.fixture
def tt() -> FakeTT:
    return FakeTT()


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


def make(tt: FakeTT, fake_time: FakeTime, **kwargs) -> TTClient:
    return TTClient(
        ReadOnlyClient(transport=tt.transport),
        parse_secret(SECRET),
        clock=fake_time.clock,
        sleep=fake_time.sleep,
        **kwargs,
    )


# --- token -----------------------------------------------------------------------


def test_first_get_signs_in_once_then_reuses_the_token(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time)
    client.get("ttpds", "markets")
    client.get("ttpds", "markets")
    assert tt.token_requests == 1
    assert [(r.method, r.url.path) for r in tt.requests] == [
        ("POST", "/ttid/ext_prod_live/token"),
        ("GET", "/ttpds/ext_prod_live/markets"),
        ("GET", "/ttpds/ext_prod_live/markets"),
    ]
    token_request = tt.requests[0]
    assert token_request.headers["x-api-key"] == "fakekey"  # the text before the colon
    assert token_request.content.decode() == "grant_type=user_app&app_key=fakekey%3Afakesecretvalue"
    get = tt.requests[1]
    assert get.headers["Authorization"] == f"Bearer {TOKEN}"
    assert get.headers["x-api-key"] == "fakekey"


def test_every_request_carries_a_fresh_request_id(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time, request_id_prefix="Lookout-Acme")
    client.get("ttpds", "markets")
    client.get("ttpds", "markets")
    ids = [r.url.params["requestId"] for r in tt.requests]
    guid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    assert all(re.fullmatch(rf"Lookout-Acme--{guid}", i) for i in ids)
    assert len(set(ids)) == 3


def test_a_request_id_without_a_company_name_is_refused_by_tt(
    tt: FakeTT, fake_time: FakeTime
) -> None:
    """What happened on the first production pull: TT answers HTTP 400."""
    client = make(tt, fake_time, request_id_prefix="Lookout")
    with pytest.raises(TTApiError, match="requestId query parameter") as caught:
        client.get("ttpds", "markets")
    assert caught.value.status_code == 400


def test_token_is_renewed_before_it_expires(fake_time: FakeTime) -> None:
    tt = FakeTT(token_lifetime=600)
    client = make(tt, fake_time)
    client.get("ttpds", "markets")
    fake_time.now += 500  # still inside the lifetime, minus the safety margin
    client.get("ttpds", "markets")
    assert tt.token_requests == 1
    fake_time.now += 100  # past lifetime - 60s margin
    client.get("ttpds", "markets")
    assert tt.token_requests == 2


def test_a_401_triggers_one_new_sign_in_and_a_retry(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time)
    client.get("ttpds", "markets")
    rejected = []

    def reject_once(request: httpx.Request) -> httpx.Response | None:
        if request.method == "GET" and not rejected:
            rejected.append(request)
            return httpx.Response(401, json={"status": "Unauthorized"})
        return None

    tt.hook = reject_once
    assert client.get("ttpds", "markets").body["markets"]
    assert tt.token_requests == 2

    tt.hook = lambda request: (
        httpx.Response(401, json={"status": "Unauthorized"}) if request.method == "GET" else None
    )
    with pytest.raises(TTApiError) as info:
        client.get("ttpds", "markets")
    assert info.value.status_code == 401
    assert tt.token_requests == 3  # one retry only, no loop


def test_failed_sign_in_never_leaks_the_secret(tt: FakeTT, fake_time: FakeTime) -> None:
    client = TTClient(
        ReadOnlyClient(transport=tt.transport),
        parse_secret("wrongkey:wrongsecretvalue"),
        clock=fake_time.clock,
        sleep=fake_time.sleep,
    )
    with pytest.raises(TTApiError) as info:
        client.get("ttpds", "markets")
    # the fake server echoes the form back, as a careless server might
    assert "wrongsecretvalue" not in str(info.value) and "wrongkey" not in str(info.value)
    assert "set_credentials.py" in str(info.value)
    assert info.value.status_code == 400


def test_secret_and_token_never_appear_in_the_call_log_or_repr(
    tt: FakeTT, fake_time: FakeTime
) -> None:
    client = make(tt, fake_time)
    client.get("ttaccount", "account/200")
    text = repr(client) + repr(client.calls) + str(vars(client)["calls"])
    assert "fakesecretvalue" not in text and TOKEN not in text and "fakekey" not in text
    assert client.calls[0] == {
        "seq": 1,
        "method": "POST",
        "path": "/ttid/ext_prod_live/token",
        "status": 200,
        "ms": 0,
    }
    assert client.calls[1]["path"] == "/ttaccount/ext_prod_live/account/200"
    assert set(client.calls[1]) == {"seq", "method", "path", "status", "ms"}


def test_credentials_can_be_a_provider_called_only_when_needed(
    tt: FakeTT, fake_time: FakeTime
) -> None:
    asked = []

    def provider():
        asked.append(1)
        return parse_secret(SECRET)

    client = TTClient(
        ReadOnlyClient(transport=tt.transport),
        provider,
        clock=fake_time.clock,
        sleep=fake_time.sleep,
    )
    assert asked == []
    client.get("ttpds", "markets")
    assert asked


# --- status checks ---------------------------------------------------------------


def test_body_and_text_are_returned(tt: FakeTT, fake_time: FakeTime) -> None:
    response = make(tt, fake_time).get("ttaccount", "account/200")
    assert response.body["account"][0]["name"] == "FUND-ALPHA"
    assert synthetic.BIG_FAMILY_ID in response.text
    limit = response.body["account"][0]["riskLimits"]["productLimits"][0]
    assert limit["productFamilyId"] == synthetic.BIG_FAMILY_ID


@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [
        (200, {"status": "Error", "status_message": "bad thing"}, "bad thing"),
        (200, {"status": "Unauthorized"}, "Unauthorized"),
        (500, {"status": "Internal Error"}, "HTTP 500"),
        (404, {"status": "Not Found"}, "HTTP 404"),
        (302, {}, "HTTP 302"),
    ],
)
def test_errors_including_not_ok_on_http_200(
    tt: FakeTT, fake_time: FakeTime, code: int, body: dict, expected: str
) -> None:
    tt.fail["/ttaccount/ext_prod_live/account/200"] = (code, body)
    with pytest.raises(TTApiError, match=expected) as info:
        make(tt, fake_time).get("ttaccount", "account/200")
    assert info.value.path == "/ttaccount/ext_prod_live/account/200"
    assert info.value.status_code == code


def test_a_body_without_a_status_field_is_accepted(tt: FakeTT, fake_time: FakeTime) -> None:
    tt.fail["/ttaccount/ext_prod_live/account/200"] = (200, {"account": [{"id": 200}]})
    assert make(tt, fake_time).get("ttaccount", "account/200").body == {"account": [{"id": 200}]}


def test_non_json_and_non_object_replies_are_errors(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time)
    tt.hook = lambda r: (
        httpx.Response(200, text="<html>proxy page</html>") if r.method == "GET" else None
    )
    with pytest.raises(TTApiError, match="not a JSON object"):
        client.get("ttpds", "markets")
    tt.hook = lambda r: httpx.Response(200, json=[1, 2]) if r.method == "GET" else None
    with pytest.raises(TTApiError, match="not a JSON object"):
        client.get("ttpds", "markets")


def test_network_failure_is_a_clean_error(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time)
    client.get("ttpds", "markets")

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    tt.hook = explode
    with pytest.raises(TTApiError, match="Could not reach TT"):
        client.get("ttpds", "markets")
    assert client.calls[-1]["status"] == "network error"


def test_rate_limit_reply_is_retried_after_waiting(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time)
    client.get("ttpds", "markets")
    limited = []

    def limit_once(request: httpx.Request) -> httpx.Response | None:
        if request.method == "GET" and not limited:
            limited.append(1)
            return httpx.Response(429, headers={"Retry-After": "3"}, json={"status": "Too Many"})
        return None

    tt.hook = limit_once
    fake_time.sleeps.clear()
    assert client.get("ttpds", "markets").body["status"] == "Ok"
    assert 3.0 in fake_time.sleeps

    tt.hook = lambda r: (
        httpx.Response(429, json={"status": "Too Many"}) if r.method == "GET" else None
    )
    with pytest.raises(TTApiError, match="HTTP 429"):
        client.get("ttpds", "markets")


def test_the_client_cannot_be_used_to_reach_a_blocked_service(
    tt: FakeTT, fake_time: FakeTime
) -> None:
    from core.readonly_http import ReadOnlyViolation

    client = make(tt, fake_time)
    with pytest.raises(ReadOnlyViolation):
        client.get("ttledger", "fills")
    with pytest.raises(ReadOnlyViolation):
        client.get("ttaccount", "../../ttledger/ext_prod_live/fills")
    assert "/ttledger/ext_prod_live/fills" not in tt.paths()
    assert (
        not hasattr(client, "post") and not hasattr(client, "put") and not hasattr(client, "delete")
    )


# --- pacing and the call cap -----------------------------------------------------


def test_requests_are_spaced_at_the_configured_rate(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time, requests_per_second=2)
    for _ in range(3):
        client.get("ttpds", "markets")
    # 4 calls (token + 3 GETs) -> 3 waits of half a second
    assert fake_time.sleeps == [0.5, 0.5, 0.5]

    fake_time.sleeps.clear()
    fake_time.now += 10  # time passed by itself: no need to wait
    client.get("ttpds", "markets")
    assert fake_time.sleeps == []


def test_slower_rate(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time, requests_per_second=0.5)
    client.get("ttpds", "markets")
    assert fake_time.sleeps == [2.0]


def test_call_cap_counts_every_call_including_the_token(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time, max_calls=3)
    client.get("ttpds", "markets")
    client.get("ttpds", "markets")
    assert (client.calls_made, client.calls_left) == (3, 0)
    with pytest.raises(CallCapReached, match="Call cap of 3"):
        client.get("ttpds", "markets")
    assert len(tt.requests) == 3  # the refused call never left
    assert isinstance(CallCapReached("x"), TTApiError)


# --- paging ----------------------------------------------------------------------


def test_paging_follows_next_page_key_until_last_page_is_true(
    tt: FakeTT, fake_time: FakeTime
) -> None:
    client = make(tt, fake_time)
    pages = list(client.pages("ttaccount", "accounts"))
    assert [p.number for p in pages] == [1, 2, 3]
    assert [p.reached_end for p in pages] == [False, False, True]
    assert pages[0].response.body["lastPage"] == "false"  # a string, as TT sends it
    names = [a["name"] for p in pages for a in p.response.body["accounts"]]
    assert len(names) == len(synthetic.ACCOUNTS)
    keys = [r.url.params.get("nextPageKey") for r in tt.data_requests()]
    assert keys == [None, "key2", "key3"]


def test_paging_stops_at_the_page_cap_without_reaching_the_end(
    tt: FakeTT, fake_time: FakeTime
) -> None:
    client = make(tt, fake_time)
    pages = list(client.pages("ttaccount", "accounts", max_pages=2))
    assert [p.reached_end for p in pages] == [False, False]
    assert len(tt.data_requests()) == 2


def test_paging_keeps_other_query_parameters(fake_time: FakeTime) -> None:
    tt = FakeTT(honour_otd_filter=True, otd_page_size=3)
    client = make(tt, fake_time)
    pages = list(client.pages("ttsetup", "ordertagdefaults", {"accountId": "300"}))
    assert len(pages) == 2 and pages[-1].reached_end
    assert all(r.url.params["accountId"] == "300" for r in tt.data_requests())


def test_empty_key_ends_the_list_even_if_last_page_says_false(
    tt: FakeTT, fake_time: FakeTime
) -> None:
    client = make(tt, fake_time)
    tt.hook = lambda r: (
        httpx.Response(
            200, json={"accounts": [], "lastPage": "false", "nextPageKey": "", "status": "Ok"}
        )
        if r.method == "GET"
        else None
    )
    pages = list(client.pages("ttaccount", "accounts"))
    assert len(pages) == 1 and pages[0].reached_end


def test_a_repeated_page_key_stops_instead_of_looping(tt: FakeTT, fake_time: FakeTime) -> None:
    client = make(tt, fake_time)
    tt.hook = lambda r: (
        httpx.Response(
            200, json={"accounts": [], "lastPage": "false", "nextPageKey": "same", "status": "Ok"}
        )
        if r.method == "GET"
        else None
    )
    with pytest.raises(TTApiError, match="repeated a page key"):
        list(client.pages("ttaccount", "accounts"))
    assert len(tt.data_requests()) == 2
