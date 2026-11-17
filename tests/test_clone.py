"""Clone plans and runs, against the fake TT in UAT. No real TT is ever called."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from core.clone import ClearingMode, NewAccount, build_plan, clone_params, parse_new_accounts
from core.clone_run import LOOKUP_TRIES, id_in_reply, read_source, run_plan, write_log
from core.credentials import parse_secret
from core.hierarchy import AccountTree
from core.readonly_http import ReadOnlyClient
from core.tt_client import TTClient
from core.tt_writer import TTWriter
from core.write_http import WriteClient
from tests.fixtures.fake_tt import SECRET, FakeTT

UAT = "ext_uat_cert"
NO_WAIT: dict[str, Any] = {"sleep": lambda seconds: None, "clock": lambda: 0.0}


def item(id_: int, name: str, parent: int) -> dict[str, Any]:
    return {"id": id_, "name": name, "parentAccountId": parent, "accountType": 3}


# POD1 has a group below it; every name below contains "POD1".
GROUP = AccountTree(
    [
        item(1, "TOP", 0),
        item(10, "POD1", 1),
        item(11, "POD1-A", 10),
        item(12, "POD1-B", 10),
        item(13, "X-POD1-A1", 11),
        item(20, "POD2", 1),
        item(21, "POD9-B", 1),  # makes cloning POD1 as POD9 clash on a child
        item(30, "LONE", 0),
    ]
)


@pytest.fixture
def tt() -> FakeTT:
    return FakeTT(environment=UAT)


def reader(tt: FakeTT) -> TTClient:
    http = ReadOnlyClient(environment=UAT, transport=tt.transport)
    return TTClient(http, parse_secret(SECRET), **NO_WAIT)


def writer(tt: FakeTT) -> TTWriter:
    http = WriteClient(environment=UAT, transport=tt.transport)
    return TTWriter(http, parse_secret(SECRET), **NO_WAIT)


def rows(names: list[str]) -> list[NewAccount]:
    return [NewAccount(name) for name in names]


# --- the pasted table or CSV -----------------------------------------------------


def test_one_name_per_line_strips_spaces_and_blank_lines() -> None:
    text = "  POD7 \r\nPOD8   \n\n   \nPOD9\n"
    assert parse_new_accounts(text).accounts == tuple(rows(["POD7", "POD8", "POD9"]))
    assert parse_new_accounts("") == parse_new_accounts("  \n\n")


def test_a_paste_from_excel_splits_on_tabs() -> None:
    text = "NEW1\t Desk one, London \tCLR1\nNEW2\t\t\nNEW3\tDesk three\n"
    parsed = parse_new_accounts(text)
    assert parsed.errors == ()
    assert parsed.accounts == (
        NewAccount("NEW1", "Desk one, London", "CLR1"),
        NewAccount("NEW2"),
        NewAccount("NEW3", "Desk three"),
    )


def test_a_csv_file_uses_commas_quotes_and_skips_its_header() -> None:
    text = '\ufeffAccount,Description,Clearing\r\nNEW1,"Desk 1, NY",CLR1\r\nNEW2,,\r\n,,\r\n'
    parsed = parse_new_accounts(text)
    assert parsed.header_skipped and parsed.errors == ()
    assert parsed.accounts == (NewAccount("NEW1", "Desk 1, NY", "CLR1"), NewAccount("NEW2"))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("NEW1\n\tDesk only\n", "Row 2 has no new account"),
        ("NEW1,a,b,c\n", "Row 1 has more than 3 columns"),
    ],
)
def test_bad_rows_are_reported(text: str, message: str) -> None:
    parsed = parse_new_accounts(text)
    assert any(message in error for error in parsed.errors), parsed.errors


def test_an_account_named_like_a_word_is_not_a_header() -> None:
    assert not parse_new_accounts("ACCOUNT1\nACCOUNT2\n").header_skipped


def test_clone_params_send_every_switch_off() -> None:
    params = clone_params()
    assert set(params.values()) == {"false"} and len(params) == 9
    assert {"orderTagDefaults", "limits", "assignNewAccountsToSameUsers"} <= set(params)


# --- plans -----------------------------------------------------------------------


def test_single_account_goes_under_the_same_parent() -> None:
    plan = build_plan(GROUP, " POD2 ", rows(["POD7", "POD8"]))
    assert plan.ok
    assert [(s.new_name, s.parent_id, s.parent_step) for s in plan.steps] == [
        ("POD7", "1", None),
        ("POD8", "1", None),
    ]
    assert plan.rows()[0] == {
        "Step": "1", "New account": "POD7", "Under": "TOP (1)", "Description": "",
        "Copied from": "POD2 (20)",
    }  # fmt: skip


def test_group_is_cloned_top_down_with_names_swapped() -> None:
    plan = build_plan(GROUP, "POD1", rows(["POD7"]))
    assert plan.ok, plan.errors
    assert [(s.number, s.new_name, s.parent_step) for s in plan.steps] == [
        (1, "POD7", None),
        (2, "POD7-A", 1),
        (3, "X-POD7-A1", 2),
        (4, "POD7-B", 1),
    ]


def test_source_can_be_given_by_id_and_top_level_has_no_parent() -> None:
    plan = build_plan(GROUP, "30", rows(["SOLO"]))
    assert plan.ok and plan.steps[0].parent_id == "" and plan.steps[0].parent_name == "(top level)"


@pytest.mark.parametrize(
    ("source", "names", "message"),
    [
        ("NOPE", ["X"], "No account named NOPE"),
        ("", ["X"], "Enter the account"),
        ("POD2", [], "Paste or upload at least one"),
        ("POD2", ["POD1"], "POD1 already exists in TT."),
        ("POD2", ["NEW", "new"], "listed more than once"),
        ("POD2", ["POD2"], "is the account being cloned"),
        ("POD1", ["POD9"], "POD9-B already exists"),  # derived names are checked too
    ],
)
def test_plan_errors(source: str, names: list[str], message: str) -> None:
    plan = build_plan(GROUP, source, rows(names))
    assert not plan.ok
    assert any(message in error for error in plan.errors), plan.errors


def test_a_child_without_the_group_name_cannot_be_named() -> None:
    tree = AccountTree([item(1, "TEAM", 0), item(2, "TEAM-1", 1), item(3, "ODD", 1)])
    plan = build_plan(tree, "TEAM", rows(["CREW"]))
    assert plan.errors == (
        "ODD is below TEAM, but its name does not contain TEAM, so Lookout cannot name its copy.",
    )


def test_two_names_by_the_same_name_are_ambiguous() -> None:
    tree = AccountTree([item(1, "SAME", 0), item(2, "SAME", 0)])
    plan = build_plan(tree, "SAME", rows(["NEW"]))
    assert "enter the id instead" in plan.errors[0]


def test_clearing_values_from_source_or_new_name() -> None:
    entry = {"accountId": 11, "marketId": 7, "fieldId": 1859, "value": "OLD"}
    common: dict[str, Any] = {"clearing_entries": {"11": [entry]}, "market_names": {"7": "CME"}}
    by_name = build_plan(
        GROUP, "POD1", rows(["POD7"]), clearing_mode=ClearingMode.NEW_NAME, **common
    )
    assert by_name.clearing_rows() == [
        {"Step": "2", "New account": "POD7-A", "Exchange": "CME",
         "Source value": "OLD", "New value": "POD7-A"},
    ]  # fmt: skip
    same = build_plan(GROUP, "POD1", rows(["POD7"]), clearing_mode=ClearingMode.SOURCE, **common)
    assert same.clearing[0].value == "OLD"
    off = build_plan(GROUP, "POD1", rows(["POD7"]), **common)
    assert off.clearing == ()


def test_a_rows_own_clearing_value_wins_on_its_top_account_only() -> None:
    entries = {
        "10": [{"accountId": 10, "marketId": 7, "fieldId": 1859, "value": "OLD"}],
        "11": [{"accountId": 11, "marketId": 7, "fieldId": 1859, "value": "OLD-A"}],
    }
    plan = build_plan(
        GROUP, "POD1",
        [NewAccount("POD7", "Desk 7", "CLR7"), NewAccount("POD8")],
        clearing_mode=ClearingMode.SOURCE, clearing_entries=entries,
    )  # fmt: skip
    assert plan.ok, plan.errors
    assert [(plan.step(c.step).new_name, c.value) for c in plan.clearing] == [
        ("POD7", "CLR7"), ("POD7-A", "OLD-A"), ("POD8", "OLD"), ("POD8-A", "OLD-A"),
    ]  # fmt: skip
    assert [s.description for s in plan.steps if s.description] == ["Desk 7"]


def test_clearing_values_without_the_box_ticked_stop_the_plan() -> None:
    plan = build_plan(GROUP, "POD2", [NewAccount("POD7", clearing="CLR7")])
    assert not plan.ok and "is not ticked" in plan.errors[0]


def test_a_clearing_value_needs_a_source_clearing_account_to_copy() -> None:
    plan = build_plan(
        GROUP, "POD2", [NewAccount("POD7", clearing="CLR7")], clearing_mode=ClearingMode.SOURCE
    )
    assert not plan.ok and "POD2 has no Clearing Account set" in plan.errors[0]


# --- reading the source ----------------------------------------------------------


def test_read_source_finds_the_clearing_account_entries(tt: FakeTT) -> None:
    data = read_source(reader(tt), "ABC10001AL", need_clearing=True, max_pages=20)
    assert data.clearing_field_ids == frozenset({"1859"})
    entries = data.clearing_entries["300"]
    assert [e["marketId"] for e in entries] == [2, 7, 30, 91, 107]
    assert all(e["userId"] == 0 for e in entries)  # the per-user entry is left out
    assert data.market_names["91"] == "MX"
    assert all(r.method == "GET" for r in tt.requests if "token" not in r.url.path)


def test_read_source_without_clearing_reads_only_the_accounts(tt: FakeTT) -> None:
    data = read_source(reader(tt), "ABC10001AL", need_clearing=False, max_pages=20)
    assert data.clearing_entries == {} and len(data.tree) > 10
    assert {p.split("/")[1] for p in tt.paths("GET")} == {"ttaccount"}


# --- running ---------------------------------------------------------------------


def _plan(tt: FakeTT, mode: ClearingMode, new: NewAccount | None = None):  # noqa: ANN202
    data = read_source(reader(tt), "ABC10001AL", need_clearing=True, max_pages=20)
    plan = build_plan(
        data.tree, "ABC10001AL", [new or NewAccount("NEW10001AL")], clearing_mode=mode,
        clearing_entries=data.clearing_entries, market_names=data.market_names,
    )  # fmt: skip
    tt.requests.clear()
    return plan


def _run(tt: FakeTT, plan, waits: list[float] | None = None):  # noqa: ANN001, ANN202
    waits = [] if waits is None else waits
    return run_plan(plan, writer(tt), reader(tt), wait=waits.append)


def test_clone_and_create_clearing_entries_with_the_new_name(tt: FakeTT) -> None:
    plan = _plan(tt, ClearingMode.NEW_NAME)
    result = _run(tt, plan)
    assert result.ok and result.created == {1: "5001"}
    query, body = tt.clones[0]
    assert body == {
        "sourceAccountId": 300, "accountName": "NEW10001AL",
        "cloneChildren": "False", "parentId": 200,
    }  # fmt: skip
    assert {k: v for k, v in query.items() if k != "requestId"} == clone_params()
    assert query["requestId"].startswith("Lookout-CHANGEME--")
    [(kind, created)] = tt.otd_writes
    assert kind == "create" and len(created) == 5
    assert {(e["accountId"], e["value"]) for e in created} == {(5001, "NEW10001AL")}
    assert "orderTagDefaultId" not in created[0] and created[0]["fieldId"] == 1859
    assert [r["Status"] for r in result.rows] == ["Created"] * 6


def test_source_value_without_tt_copy_creates_the_entries(tt: FakeTT) -> None:
    plan = _plan(tt, ClearingMode.SOURCE)
    _run(tt, plan)
    [(_, created)] = tt.otd_writes
    assert sorted(e["value"] for e in created) == ["ABC10001"] * 4 + ["ABC99999"]


def test_a_description_is_sent_with_the_clone_and_checked(tt: FakeTT) -> None:
    plan = _plan(tt, ClearingMode.OFF, NewAccount("NEW10001AL", "Desk 1"))
    result = _run(tt, plan)
    assert result.ok and tt.clones[0][1]["description"] == "Desk 1"
    assert [(r["What"], r["Status"]) for r in result.rows] == [
        ("Account", "Created"), ("Description", "Set"),
    ]  # fmt: skip
    assert tt.paths("GET")[-1].endswith("/account/5001")


def test_a_description_tt_drops_is_flagged_and_the_run_goes_on(tt: FakeTT) -> None:
    tt.clone_keeps_description = False
    data = read_source(reader(tt), "ABC10001AL", need_clearing=False, max_pages=20)
    plan = build_plan(data.tree, "ABC10001AL", [NewAccount("N1", "D1"), NewAccount("N2")])
    result = _run(tt, plan)
    assert result.ok and result.created == {1: "5001", 2: "5002"}
    assert result.rows[1]["Status"] == "Not kept by TT"
    assert "set it by hand" in result.rows[1]["Detail"]
    assert "description" not in tt.clones[1][1]


def test_a_rows_clearing_value_is_created_on_the_new_account(tt: FakeTT) -> None:
    plan = _plan(tt, ClearingMode.NEW_NAME, NewAccount("NEW10001AL", clearing="CLR9"))
    _run(tt, plan)
    [(_, created)] = tt.otd_writes
    assert {(e["accountId"], e["value"]) for e in created} == {(5001, "CLR9")}


def test_group_run_wires_each_new_parent(tt: FakeTT) -> None:
    plan = build_plan(GROUP, "POD1", rows(["POD7", "POD8"]))
    result = _run(tt, plan)
    assert result.ok
    bodies = [(b["accountName"], b.get("parentId")) for _, b in tt.clones]
    assert bodies == [
        ("POD7", 1), ("POD7-A", 5001), ("X-POD7-A1", 5002), ("POD7-B", 5001),
        ("POD8", 1), ("POD8-A", 5005), ("X-POD8-A1", 5006), ("POD8-B", 5005),
    ]  # fmt: skip
    assert all(r.method == "POST" for r in tt.requests)


def test_a_failure_stops_the_run_and_says_what_was_not_done(tt: FakeTT) -> None:
    plan = build_plan(GROUP, "POD1", rows(["POD7"]))
    calls = 0

    def fail_second_clone(request):  # noqa: ANN001, ANN202
        nonlocal calls
        if request.url.path.endswith("/account/clone"):
            calls += 1
            if calls == 2:
                import httpx

                return httpx.Response(400, json={"status": "Failed", "status_message": "dup"})
        return None

    tt.hook = fail_second_clone
    result = _run(tt, plan)
    assert not result.ok and "step 2 (POD7-A)" in result.stopped and "dup" in result.stopped
    assert [(r["New account"], r["Status"]) for r in result.rows] == [
        ("POD7", "Created"), ("POD7-A", "Failed"), ("X-POD7-A1", "Not run"), ("POD7-B", "Not run"),
    ]  # fmt: skip
    assert len(tt.clones) == 1


def test_a_plan_with_errors_sends_nothing(tt: FakeTT) -> None:
    result = _run(tt, build_plan(GROUP, "POD1", rows(["POD2"])))
    assert not result.ok and tt.requests == []


def test_write_log_records_the_run_without_secrets(tt: FakeTT, tmp_path: Path) -> None:
    plan = _plan(tt, ClearingMode.NEW_NAME)
    w, r = writer(tt), reader(tt)
    result = run_plan(plan, w, r)
    path = write_log(tmp_path / "log", plan, result, [*r.calls, *w.calls],
                     now=datetime(2026, 11, 17, 9, 0))  # fmt: skip
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["source"] == "ABC10001AL (300)" and record["clearing_mode"] == "new_name"
    assert record["result"][0]["New id"] == "5001"
    text = path.read_text(encoding="utf-8")
    assert SECRET.split(":")[1] not in text and "fake-access-token" not in text


# --- the new account's id ----------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        {"account": [{"id": 77, "name": "NEW"}], "status": "Ok"},  # TT's reference
        {"accounts": [{"id": 77, "name": "NEW"}], "status": "Ok"},
        {"account": {"accountId": "77", "accountName": "new"}, "status": "Ok"},
        {"data": {"result": [{"id": 1, "name": "OTHER"}, {"id": 77, "name": " NEW "}]}},
        {"accountId": 77, "status": "Ok"},
        {"newAccountId": 77},
    ],
)
def test_the_new_id_is_found_in_any_reasonable_reply(reply: dict[str, Any]) -> None:
    assert id_in_reply(reply, "NEW") == "77"


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "Ok"},
        {"account": [{"id": 1, "name": "OTHER"}, {"id": 2, "name": "THIRD"}]},
        {"statusCode": 200, "status": "Ok"},
        {"account": [{"id": 0, "name": "NEW"}]},
    ],
)
def test_no_id_is_guessed(reply: dict[str, Any]) -> None:
    assert id_in_reply(reply, "NEW") == ""


def test_a_reply_without_an_id_is_looked_up_by_name_and_the_run_goes_on(tt: FakeTT) -> None:
    tt.clone_reply_has_id = False
    plan = build_plan(GROUP, "POD2", rows(["POD7", "POD8"]))
    result = _run(tt, plan)
    assert result.ok, result.stopped
    assert result.created == {1: "5001", 2: "5002"}
    assert "read from TT's accounts list" in result.rows[0]["Detail"]


def test_a_reply_without_an_id_and_no_such_account_stops_and_shows_the_reply(tt: FakeTT) -> None:
    import httpx

    def bare_reply(request):  # noqa: ANN001, ANN202
        if request.url.path.endswith("/account/clone"):
            return httpx.Response(200, json={"status": "Ok", "note": "queued"})
        return None

    tt.hook = bare_reply
    waits: list[float] = []
    result = _run(tt, build_plan(GROUP, "POD2", rows(["POD7", "POD8"])), waits)
    assert not result.ok
    assert "may still have been created" in result.stopped
    assert '"note": "queued"' in result.stopped or "queued" in result.stopped
    assert len(waits) == LOOKUP_TRIES - 1
    assert [r["Status"] for r in result.rows] == ["Failed", "Not run"]
