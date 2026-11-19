"""Committing mappings to the team file (core/team_lists.py) and the page's handler."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from core import team_lists
from core.team_lists import MAPPINGS_FILE, CommitError, commit_mappings, parse_mappings
from reports import limits

TEAM = "ricRoot,exchange,ttExchange,ttSymbol\r\nTY,,CME,ZN\r\nFLG,ICE Europe,ICE_L,R\r\n"


def m(ric: str, exchange: str, tt_exchange: str, symbol: str) -> dict[str, str]:
    return {"ricRoot": ric, "exchange": exchange, "ttExchange": tt_exchange, "ttSymbol": symbol}


def clock() -> datetime:
    return datetime(2026, 11, 19, 10, 0, 0)


def test_commit_merges_and_keeps_the_old_version(tmp_path: Path) -> None:
    (tmp_path / MAPPINGS_FILE).write_text("﻿" + TEAM, encoding="utf-8")
    done = commit_mappings(
        tmp_path,
        [
            m("ty", "", "CME", "ZF"),
            m("C", "CBOT", "CME", "ZC"),
            m("FLG", "ICE Europe", "ICE_L", "R"),
        ],
        now=clock,
    )
    assert (done.added, done.changed, done.unchanged, done.total) == (1, 1, 1, 3)
    rows = parse_mappings((tmp_path / MAPPINGS_FILE).read_text(encoding="utf-8"))
    assert rows == [
        m("C", "CBOT", "CME", "ZC"),
        m("FLG", "ICE Europe", "ICE_L", "R"),
        m("ty", "", "CME", "ZF"),
    ]
    [backup] = (tmp_path / "history").iterdir()
    assert backup.name == "tt_symbol_mappings_20261119_100000_000000.csv"
    assert backup.read_bytes().decode("utf-8").endswith(TEAM)
    assert not (tmp_path / f"{MAPPINGS_FILE}.lock").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["history", MAPPINGS_FILE]


def test_first_commit_creates_the_file(tmp_path: Path) -> None:
    done = commit_mappings(tmp_path, [m("TY", "", "CME", "ZN")], now=clock)
    assert done.added == 1 and done.backup == "" and not (tmp_path / "history").exists()
    assert (tmp_path / MAPPINGS_FILE).read_bytes().decode("utf-8") == (
        "ricRoot,exchange,ttExchange,ttSymbol\r\nTY,,CME,ZN\r\n"
    )


def test_nothing_new_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / MAPPINGS_FILE).write_text(TEAM, encoding="utf-8")
    before = (tmp_path / MAPPINGS_FILE).stat().st_mtime_ns
    done = commit_mappings(tmp_path, [m("TY", "", "CME", "ZN")], now=clock)
    assert (done.added, done.changed, done.unchanged) == (0, 0, 1)
    assert (tmp_path / MAPPINGS_FILE).stat().st_mtime_ns == before
    assert not (tmp_path / "history").exists()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([], "No mappings"),
        ("TY", "No mappings"),
        ([{"ricRoot": "TY"}], "needs a RIC root"),
        ([m("TY", "", "CME", "Z\nN")], "control characters"),
        ([m("TY", "", "CME", "Z" * 101)], "too long"),
        ([{**m("TY", "", "CME", "ZN"), "ttSymbol": 5}], "not text"),
    ],
)
def test_bad_mappings_are_refused(tmp_path: Path, rows: object, message: str) -> None:
    with pytest.raises(CommitError, match=message):
        commit_mappings(tmp_path, rows)
    assert not (tmp_path / MAPPINGS_FILE).exists()


def test_a_busy_lock_refuses_and_a_stale_one_is_cleared(tmp_path: Path) -> None:
    lock = tmp_path / f"{MAPPINGS_FILE}.lock"
    lock.write_text("", encoding="utf-8")
    with pytest.raises(CommitError, match="Someone else is saving"):
        commit_mappings(tmp_path, [m("TY", "", "CME", "ZN")], sleep=lambda s: None)
    old = time.time() - team_lists.LOCK_STALE_SECONDS - 5
    os.utime(lock, (old, old))
    assert commit_mappings(tmp_path, [m("TY", "", "CME", "ZN")], sleep=lambda s: None).added == 1
    assert not lock.exists()


def test_history_keeps_the_last_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(team_lists, "HISTORY_KEEP", 2)
    (tmp_path / MAPPINGS_FILE).write_text(TEAM, encoding="utf-8")
    for i in range(4):
        commit_mappings(
            tmp_path, [m(f"R{i}", "", "CME", "ZN")], now=lambda i=i: datetime(2026, 1, 1, 0, 0, i)
        )
    assert len(list((tmp_path / "history").iterdir())) == 2


def test_an_unreachable_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommitError, match="can't be reached"):
        commit_mappings(tmp_path / "gone", [m("TY", "", "CME", "ZN")])


def test_the_page_handler_checks_the_token_and_answers(tmp_path: Path) -> None:
    ask = json.dumps({"id": 3, "token": "T", "mappings": [m("TY", "", "CME", "ZN")]})
    assert limits.handle_commit(ask, "other", tmp_path) == {
        "id": 3,
        "ok": False,
        "message": "The commit request was refused.",
    }
    assert not (tmp_path / MAPPINGS_FILE).exists()
    answer = limits.handle_commit(ask, "T", tmp_path)
    assert answer["ok"] is True and answer["id"] == 3 and "1 added" in str(answer["message"])
    assert answer["committed"] == [m("TY", "", "CME", "ZN")]
    again = limits.handle_commit(ask, "T", tmp_path)
    assert "nothing changed" in str(again["message"])
    bad = limits.handle_commit(json.dumps({"id": 4, "token": "T", "mappings": []}), "T", tmp_path)
    assert bad == {"id": 4, "ok": False, "message": "No mappings to commit."}
    assert limits.handle_commit("not json", "T", tmp_path)["ok"] is False


def test_the_token_reaches_only_the_upload_tab(tmp_path: Path) -> None:
    shared = limits.SharedLists(str(tmp_path))
    page = limits.app_for("<head></head>", "/account-upload", shared, "TOKEN1")
    assert '"commitToken": "TOKEN1"' in page
    assert "commitToken" not in limits.app_for("<head></head>", "/risk-audit")
