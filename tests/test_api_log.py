"""The local API log: every call, with bodies, but never a secret or the token."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from core.api_log import MAX_BODY_CHARS, ApiLog
from core.credentials import parse_secret
from core.readonly_http import ReadOnlyClient
from core.tt_client import TTApiError, TTClient
from core.tt_writer import TTWriter
from core.write_http import WriteClient
from tests.fixtures.fake_tt import SECRET, TOKEN, FakeTT

NO_WAIT = {"clock": lambda: 0.0, "sleep": lambda seconds: None}
WHEN = datetime(2026, 11, 18, 9, 30, 0)


def lines(log: ApiLog) -> list[dict]:
    return [json.loads(line) for line in log.path_for(WHEN).read_text("utf-8").splitlines()]


def test_reads_are_logged_with_query_and_reply(tmp_path: Path) -> None:
    tt = FakeTT()
    log = ApiLog(tmp_path / "api_log", now=lambda: WHEN)
    client = TTClient(ReadOnlyClient(transport=tt.transport), parse_secret(SECRET),
                      api_log=log, **NO_WAIT)  # fmt: skip
    client.get("ttpds", "markets")
    token, markets = lines(log)
    assert log.path_for(WHEN).name == "api_20261118.jsonl"
    assert (token["method"], token["path"], token["status"]) == (
        "POST", "/ttid/ext_prod_live/token", 200,
    )  # fmt: skip
    assert "response" not in token and "request_body" not in token
    assert token["query"]["requestId"].startswith("Lookout-CHANGEME--")
    assert (markets["method"], markets["path"], markets["environment"]) == (
        "GET", "/ttpds/ext_prod_live/markets", "ext_prod_live",
    )  # fmt: skip
    assert json.loads(markets["response"])["markets"][0]["name"] == "CME"
    assert markets["query"]["requestId"] != token["query"]["requestId"]
    text = log.path_for(WHEN).read_text("utf-8")
    assert SECRET.split(":")[1] not in text and TOKEN not in text
    assert SECRET.split(":")[0] not in text  # the API key is a header: never logged


def test_writes_are_logged_with_the_request_body(tmp_path: Path) -> None:
    tt = FakeTT(environment="ext_uat_cert")
    log = ApiLog(tmp_path, now=lambda: WHEN)
    http = WriteClient(environment="ext_uat_cert", transport=tt.transport)
    writer = TTWriter(http, parse_secret(SECRET), api_log=log, **NO_WAIT)
    writer.post("ttaccount", "account/clone", {"sourceAccountId": 300, "accountName": "N1"},
                {"limits": "false"})  # fmt: skip
    clone = lines(log)[-1]
    assert clone["request_body"] == {"sourceAccountId": 300, "accountName": "N1"}
    assert clone["query"]["limits"] == "false"
    assert json.loads(clone["response"])["account"][0]["name"] == "N1"


def test_failed_calls_are_logged_too(tmp_path: Path) -> None:
    tt = FakeTT()
    tt.fail["/ttpds/ext_prod_live/markets"] = (400, {"status": "Failed", "status_message": "bad"})
    log = ApiLog(tmp_path, now=lambda: WHEN)
    client = TTClient(ReadOnlyClient(transport=tt.transport), parse_secret(SECRET),
                      api_log=log, **NO_WAIT)  # fmt: skip
    with pytest.raises(TTApiError):
        client.get("ttpds", "markets")
    failed = lines(log)[-1]
    assert failed["status"] == 400 and "bad" in failed["response"]


def test_a_refused_sign_in_logs_no_bodies(tmp_path: Path) -> None:
    tt = FakeTT()
    log = ApiLog(tmp_path, now=lambda: WHEN)
    wrong = parse_secret("fakekey:wrongsecretvalue")
    client = TTClient(ReadOnlyClient(transport=tt.transport), wrong, api_log=log, **NO_WAIT)
    with pytest.raises(TTApiError):
        client.get("ttpds", "markets")
    [token] = lines(log)
    assert token["status"] == 400 and set(token) == {
        "time", "environment", "method", "path", "query", "status", "ms",
    }  # fmt: skip
    assert "wrongsecretvalue" not in log.path_for(WHEN).read_text("utf-8")


def test_long_replies_are_cut_and_a_broken_folder_does_not_stop_the_call(tmp_path: Path) -> None:
    log = ApiLog(tmp_path, now=lambda: WHEN)
    log.record(environment="e", method="GET", path="/p", query=None, status=200, ms=1,
               response_text="x" * (MAX_BODY_CHARS + 10))  # fmt: skip
    assert lines(log)[0]["response"].endswith("characters in all]")

    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    broken = ApiLog(blocked / "api_log", now=lambda: WHEN)
    broken.record(environment="e", method="GET", path="/p", query=None, status=200, ms=1)
    assert "Could not write the API log" in broken.failed
