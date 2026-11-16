"""Pull -> snapshot -> rebuild, against the fake TT. No real network is involved."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from checks.setup.profile import parse_profile
from checks.setup.pull import pull_client
from checks.setup.verify import verify_snapshot
from core.credentials import parse_secret
from core.readonly_http import ReadOnlyClient
from core.setup_model import load_setup
from core.snapshots import Snapshot, latest_id, list_snapshots
from core.tt_client import TTClient
from tests.fixtures import synthetic
from tests.fixtures.fake_tt import SECRET, TOKEN, FakeTT

PROFILE = synthetic.profile()
WHEN = datetime(2026, 11, 13, 10, 0, 0)
IN_SCOPE = {str(i) for i in synthetic.in_scope_ids()}
OUT_OF_SCOPE = {"10", "11", "20", "50", "51", "900"}


def pull(tt: FakeTT, base: Path, *, max_calls: int = 1500, otd_max_pages: int = 1000) -> Snapshot:
    client = TTClient(
        ReadOnlyClient(transport=tt.transport),
        parse_secret(SECRET),
        max_calls=max_calls,
        clock=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    return pull_client(client, base, PROFILE, otd_max_pages=otd_max_pages, now=WHEN)


def detail_ids(tt: FakeTT) -> set[str]:
    ids = set()
    for path in tt.paths():
        parts = path.split("/")
        if parts[1] == "ttaccount" and parts[3] == "account":
            ids.add(parts[4])
    return ids


def rows_of(snapshot: Snapshot) -> list[dict[str, str]]:
    return [row.as_dict() for row in verify_snapshot(snapshot, PROFILE).rows]


def test_complete_pull(tmp_path: Path) -> None:
    tt = FakeTT()
    snapshot = pull(tt, tmp_path)

    assert snapshot.status == "complete"
    assert snapshot.client == "synthetic"
    assert snapshot.id == "20261113_100000"
    assert latest_id(tmp_path, "synthetic") == snapshot.id
    assert snapshot.manifest["calls"] == len(tt.requests)
    assert snapshot.manifest["errors"] == 0
    assert snapshot.manifest["scope"] == {"roots": ["100", "500"], "accounts": len(IN_SCOPE)}
    assert snapshot.model["roots"] == ["acct:100", "acct:500"]
    assert {n["id"] for n in snapshot.model["nodes"]} == IN_SCOPE
    assert snapshot.model["page_counts"]["accounts_list"] == 3

    log = snapshot.pull_log
    assert len(log["calls"]) == len(tt.requests)
    assert log["calls"][0]["path"] == "/ttid/ext_prod_live/token"
    assert log["errors"] == []
    assert all(c["method"] in {"GET", "POST"} for c in log["calls"])


def test_pull_only_ever_sends_gets_and_one_token_post(tmp_path: Path) -> None:
    tt = FakeTT()
    pull(tt, tmp_path)
    methods = [r.method for r in tt.requests]
    assert methods.count("POST") == 1
    assert set(methods) == {"GET", "POST"}
    assert tt.paths("POST") == ["/ttid/ext_prod_live/token"]
    assert {r.url.host for r in tt.requests} == {"ttrestapi.trade.tt"}


def test_pull_is_limited_to_the_clients_subtrees(tmp_path: Path) -> None:
    tt = FakeTT()
    snapshot = pull(tt, tmp_path)
    assert detail_ids(tt) == IN_SCOPE
    assert not detail_ids(tt) & OUT_OF_SCOPE

    kept = snapshot.raw("ordertagdefaults_list", "global_kept")
    kept_accounts = {str(e["accountId"]) for e in kept["orderTagDefaults"]}
    assert "51" not in kept_accounts  # another client's entry is dropped
    assert kept_accounts <= IN_SCOPE | {"0"}
    assert kept["_tool_summary"] == {
        "mode": "full_list",
        "pages": 3,
        "entries_read": len(synthetic.OTD_ENTRIES),
        "kept": len(synthetic.OTD_ENTRIES) - 1,
        "reached_end": True,
    }


def test_rebuilding_from_the_pulled_snapshot_gives_the_expected_results(tmp_path: Path) -> None:
    pulled = pull(FakeTT(), tmp_path / "pulled")
    built = synthetic.build_snapshot(tmp_path / "built")
    assert rows_of(pulled) == rows_of(built)
    # and rebuilding twice from disk is stable
    assert rows_of(pulled) == rows_of(pulled)


def test_raw_bodies_are_saved_as_tt_sent_them(tmp_path: Path) -> None:
    snapshot = pull(FakeTT(), tmp_path)
    saved = (snapshot.path / "raw" / "account_detail__200.json").read_text(encoding="utf-8")
    assert saved == json.dumps(synthetic.account_detail(200))
    assert synthetic.BIG_FAMILY_ID in saved
    setup = load_setup(snapshot)
    assert setup.limit_rows("200")[0]["productFamilyId"] == synthetic.BIG_FAMILY_ID


def test_no_secret_or_token_is_written_anywhere(tmp_path: Path) -> None:
    pull(FakeTT(), tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "fakesecretvalue" not in text, path
            assert TOKEN not in text, path
            assert "fakekey" not in text, path


def test_filter_honoured_reads_per_account(tmp_path: Path) -> None:
    tt = FakeTT(honour_otd_filter=True)
    snapshot = pull(tt, tmp_path)
    assert snapshot.status == "complete"
    summary = snapshot.raw("ordertagdefaults_list", "global_kept")["_tool_summary"]
    assert summary["mode"] == "per_account"
    assert summary["reached_end"] is True
    assert summary["kept"] == summary["entries_read"] == len(synthetic.OTD_ENTRIES) - 1

    otd_requests = [r for r in tt.data_requests() if r.url.path.endswith("/ordertagdefaults")]
    asked = [r.url.params.get("accountId") for r in otd_requests]
    assert None not in asked  # the unfiltered company-wide list was never requested
    assert set(asked) == IN_SCOPE | {"0"}
    assert "honoured" in " ".join(snapshot.pull_log["notes"])
    assert rows_of(snapshot) == rows_of(synthetic.build_snapshot(tmp_path / "built"))


def test_one_failing_account_makes_the_snapshot_partial(tmp_path: Path) -> None:
    tt = FakeTT()
    tt.fail["/ttaccount/ext_prod_live/account/310"] = (500, {"status": "Internal Error"})
    snapshot = pull(tt, tmp_path)

    assert snapshot.status == "partial"
    assert latest_id(tmp_path, "synthetic") == snapshot.id  # partial is still usable
    (error,) = snapshot.pull_log["errors"]
    assert "account_detail acct:310" in error and "HTTP 500" in error
    assert snapshot.entries(node_key="acct:310", endpoint_id="account_detail") == [
        {"node_key": "acct:310", "endpoint_id": "account_detail", "file": None, "status": "error"}
    ]
    assert detail_ids(tt) == IN_SCOPE  # the pull carried on with the other accounts

    rows = verify_snapshot(snapshot, PROFILE).rows
    unknown = [r for r in rows if r.node.startswith("ABC20002BE") and r.rule_id == "LIMITS"]
    assert [(r.colour.value, r.reason.value) for r in unknown] == [("amber", "UNKNOWN")]


def test_call_cap_stops_the_pull_and_marks_the_rest_unread(tmp_path: Path) -> None:
    tt = FakeTT()
    snapshot = pull(tt, tmp_path, max_calls=12)

    assert snapshot.status == "partial"
    assert len(tt.requests) == 12  # not one call more
    assert snapshot.manifest["calls"] == 12
    assert any("Call cap of 12" in e for e in snapshot.pull_log["errors"])
    statuses = {e["status"] for e in snapshot.raw_index if e["file"] is None}
    assert statuses == {"not read"}
    assert snapshot.entries(endpoint_id="ordertagdefaults_list")[0]["file"] is None

    setup = load_setup(snapshot)
    assert setup.otd_reached_end is False
    rows = verify_snapshot(snapshot, PROFILE).rows
    assert not [r for r in rows if r.rule_id == "OTD" and r.reason == "MISSING"]


def test_order_tag_default_page_cap_gives_unknown_not_missing(tmp_path: Path) -> None:
    tt = FakeTT(otd_page_size=5)
    snapshot = pull(tt, tmp_path, otd_max_pages=2)

    assert snapshot.status == "partial"
    summary = snapshot.raw("ordertagdefaults_list", "global_kept")["_tool_summary"]
    assert summary["pages"] == 2 and summary["reached_end"] is False
    assert summary["entries_read"] == 10
    assert snapshot.pull_log["errors"] == []
    assert "not read to the end" in " ".join(snapshot.pull_log["notes"])

    rows = verify_snapshot(snapshot, PROFILE).rows
    b4 = [
        r
        for r in rows
        if r.rule_id == "OTD" and "Clearing Account" in r.setting and r.actual == "Not set"
    ]
    assert b4 and all(r.reason == "UNKNOWN" for r in b4)


def test_failed_accounts_list_gives_a_failed_snapshot(tmp_path: Path) -> None:
    good = pull(FakeTT(), tmp_path)

    tt = FakeTT()
    tt.fail["/ttaccount/ext_prod_live/accounts"] = (200, {"status": "Error", "status_message": "x"})
    client = TTClient(
        ReadOnlyClient(transport=tt.transport),
        parse_secret(SECRET),
        clock=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    failed = pull_client(client, tmp_path, PROFILE, now=datetime(2026, 11, 13, 11, 0, 0))
    assert failed.status == "failed"
    assert "Accounts list" in failed.pull_log["errors"][0]
    assert len(tt.data_requests()) == 1  # nothing else was attempted
    assert list_snapshots(tmp_path, "synthetic") == [failed.id, good.id]
    assert latest_id(tmp_path, "synthetic") == good.id  # LATEST still points at the good one


def test_wrong_hierarchy_fails_before_reading_any_account(tmp_path: Path) -> None:
    """A profile whose path is not in TT: stop before reading any account."""
    tt = FakeTT()
    client = TTClient(
        ReadOnlyClient(transport=tt.transport),
        parse_secret(SECRET),
        clock=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    other = parse_profile(
        {
            "client": "elsewhere",
            "trees": [
                {
                    "name": "t",
                    "label": "T",
                    "path": ["NOBANK", "NOCLIENT"],
                    "levels": [{"depth": 0, "label": "Client group"}],
                }
            ],
        }
    )
    snapshot = pull_client(client, tmp_path, other, now=WHEN)
    assert snapshot.status == "failed"
    assert snapshot.client == "elsewhere"
    assert "Could not find the client's start accounts" in snapshot.pull_log["errors"][0]
    assert detail_ids(tt) == set()


@pytest.mark.parametrize("honour", [False, True])
def test_pull_stays_within_a_small_call_budget(tmp_path: Path, honour: bool) -> None:
    tt = FakeTT(honour_otd_filter=honour)
    snapshot = pull(tt, tmp_path)
    accounts = len(IN_SCOPE)
    # token + 3 list pages + 2 per account + fields + markets + OTD probe + OTD pages
    assert snapshot.manifest["calls"] <= 1 + 3 + 2 * accounts + 2 + 1 + accounts + 1


def test_progress_is_reported(tmp_path: Path) -> None:
    lines: list[str] = []
    client = TTClient(
        ReadOnlyClient(transport=FakeTT().transport),
        parse_secret(SECRET),
        clock=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    pull_client(client, tmp_path, PROFILE, now=WHEN, progress=lines.append)
    assert lines[0] == "Accounts list: page 1"
    assert f"Account 1 of {len(IN_SCOPE)}: FUND" in lines
    assert any(line.startswith("Order Tag Defaults: page") for line in lines)


def test_a_uat_pull_is_marked_as_uat(tmp_path: Path) -> None:
    tt = FakeTT(environment="ext_uat_cert")
    client = TTClient(
        ReadOnlyClient(environment="ext_uat_cert", transport=tt.transport),
        parse_secret(SECRET),
        clock=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    snapshot = pull_client(client, tmp_path, PROFILE, now=WHEN)
    assert snapshot.status == "complete"
    assert snapshot.manifest["environment"] == "ext_uat_cert"
    assert all("/ext_uat_cert/" in path for path in tt.paths("GET") + tt.paths("POST"))
    assert pull(FakeTT(), tmp_path / "live").manifest["environment"] == "ext_prod_live"
