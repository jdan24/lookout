from __future__ import annotations

import json
import tomllib
from datetime import datetime
from pathlib import Path

import pytest

from core.snapshots import (
    SnapshotError,
    SnapshotWriter,
    latest_id,
    list_clients,
    list_snapshots,
    load_snapshot,
    raw_file_name,
)
from core.version import __version__

BIG = "7193248756120394857"
T1 = datetime(2026, 11, 13, 9, 30, 0)
T2 = datetime(2026, 11, 13, 14, 5, 59)


def write_simple(base: Path, when: datetime, status: str = "complete") -> str:
    writer = SnapshotWriter(base, "acme", now=when)
    writer.save_raw("account_detail", "1001", {"account": [{"id": 1001}]}, node_key="acct:1001")
    return writer.finish(status=status, roots=["acct:1001"]).id  # type: ignore[arg-type]


def test_round_trip(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, "acme", now=T1)
    assert writer.id == "20261113_093000"

    detail = {"account": [{"id": 1001, "riskLimits": {"productLimits": [{"productId": BIG}]}}]}
    writer.save_raw("account_detail", "1001", detail, node_key="acct:1001")
    writer.save_raw(
        "accounts_list", "page1", '{"accounts": [], "lastPage": "true"}', node_key="global"
    )
    writer.note_unread("account_users", node_key="acct:1001")

    snapshot = writer.finish(
        status="partial",
        nodes=[{"key": "acct:1001", "name": "ROOT", "parent": None}],
        roots=["acct:1001"],
        calls=[{"seq": 1, "method": "GET", "path": "/ttaccount/x", "status": 200, "ms": 12}],
        errors=["account_users acct:1001: HTTP 500"],
        notes=["synthetic"],
        scope={"roots": ["1001"]},
        page_counts={"accounts_list": 1},
    )

    assert snapshot.client == "acme"
    assert snapshot.id == "20261113_093000"
    assert snapshot.status == "partial"
    assert not snapshot.is_complete
    assert snapshot.manifest == {
        "client": "acme",
        "snapshot_id": "20261113_093000",
        "created_at": "2026-11-13T09:30:00",
        "status": "partial",
        "tool_version": __version__,
        "environment": "ext_prod_live",
        "calls": 1,
        "errors": 1,
        "scope": {"roots": ["1001"]},
    }
    assert snapshot.model["roots"] == ["acct:1001"]
    assert snapshot.model["page_counts"] == {"accounts_list": 1}
    assert snapshot.pull_log["calls"][0]["path"] == "/ttaccount/x"
    assert snapshot.pull_log["notes"] == ["synthetic"]

    assert snapshot.raw("account_detail", "1001") == detail
    assert snapshot.raw_for("acct:1001", "account_detail") == detail
    assert snapshot.raw_for("global", "accounts_list") == {"accounts": [], "lastPage": "true"}
    assert snapshot.raw_for("acct:1001", "account_users") is None
    assert snapshot.raw_for("acct:9999", "account_detail") is None
    assert [e["status"] for e in snapshot.entries(node_key="acct:1001")] == ["ok", "error"]


def test_folder_layout(tmp_path: Path) -> None:
    write_simple(tmp_path, T1)
    folder = tmp_path / "acme" / "20261113_093000"
    assert sorted(p.name for p in folder.iterdir()) == [
        "manifest.json",
        "model.json",
        "pull_log.json",
        "raw",
    ]
    assert [p.name for p in (folder / "raw").iterdir()] == ["account_detail__1001.json"]
    assert (tmp_path / "acme" / "LATEST").read_text() == "20261113_093000"


def test_text_bodies_are_saved_exactly_as_received(tmp_path: Path) -> None:
    body = '{"productId":"' + BIG + '","maxPos":1.50,  "odd spacing":true}'
    writer = SnapshotWriter(tmp_path, "acme", now=T1)
    file = writer.save_raw("account_detail", "1", body, node_key="acct:1")
    assert (writer.path / "raw" / file).read_text(encoding="utf-8") == body


def test_big_ids_stay_strings(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, "acme", now=T1)
    writer.save_raw("account_detail", "1", {"productFamilyId": BIG}, node_key="acct:1")
    snapshot = writer.finish(status="complete")
    value = snapshot.raw("account_detail", "1")["productFamilyId"]
    assert value == BIG and isinstance(value, str)


def test_latest_and_listing(tmp_path: Path) -> None:
    assert list_snapshots(tmp_path, "acme") == []
    assert latest_id(tmp_path, "acme") is None
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path, "acme")

    first = write_simple(tmp_path, T1)
    second = write_simple(tmp_path, T2, status="partial")
    assert list_snapshots(tmp_path, "acme") == [second, first]
    assert latest_id(tmp_path, "acme") == second
    assert load_snapshot(tmp_path, "acme").id == second
    assert load_snapshot(tmp_path, "acme", first).id == first


def test_failed_snapshot_is_listed_but_never_latest(tmp_path: Path) -> None:
    good = write_simple(tmp_path, T1)
    failed = write_simple(tmp_path, T2, status="failed")
    assert list_snapshots(tmp_path, "acme") == [failed, good]
    assert latest_id(tmp_path, "acme") == good
    assert load_snapshot(tmp_path, "acme", failed).status == "failed"


def test_latest_falls_back_when_the_pointer_is_stale(tmp_path: Path) -> None:
    good = write_simple(tmp_path, T1)
    (tmp_path / "acme" / "LATEST").write_text("20990101_000000")
    assert latest_id(tmp_path, "acme") == good
    (tmp_path / "acme" / "LATEST").unlink()
    assert latest_id(tmp_path, "acme") == good


def test_interrupted_pull_is_ignored(tmp_path: Path) -> None:
    good = write_simple(tmp_path, T1)
    interrupted = SnapshotWriter(tmp_path, "acme", now=T2)
    interrupted.save_raw("account_detail", "1", {}, node_key="acct:1")
    # finish() never called: no manifest.json
    assert list_snapshots(tmp_path, "acme") == [good]
    assert latest_id(tmp_path, "acme") == good
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path, "acme", interrupted.id)


def test_two_pulls_in_the_same_second_get_different_folders(tmp_path: Path) -> None:
    first = write_simple(tmp_path, T1)
    second = write_simple(tmp_path, T1)
    assert (first, second) == ("20261113_093000", "20261113_093000_2")
    assert list_snapshots(tmp_path, "acme") == [second, first]


def test_clients_are_kept_apart(tmp_path: Path) -> None:
    write_simple(tmp_path, T1)
    other = SnapshotWriter(tmp_path, "other", now=T2)
    other.finish(status="complete")
    assert list_snapshots(tmp_path, "acme") == ["20261113_093000"]
    assert list_snapshots(tmp_path, "other") == ["20261113_140559"]


def test_writer_refuses_misuse(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, "acme", now=T1)
    writer.save_raw("account_detail", "1", {}, node_key="acct:1")
    with pytest.raises(SnapshotError):
        writer.save_raw("account_detail", "1", {}, node_key="acct:1")
    with pytest.raises(SnapshotError):
        writer.finish(status="done")  # type: ignore[arg-type]
    writer.finish(status="complete")
    with pytest.raises(SnapshotError):
        writer.save_raw("account_detail", "2", {}, node_key="acct:2")
    with pytest.raises(SnapshotError):
        writer.finish(status="complete")


@pytest.mark.parametrize("bad", ["../etc", "a/b", "a\\b", "", "..", ".hidden", "a b", "x__/y"])
def test_unsafe_names_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(SnapshotError):
        raw_file_name(bad, "1")
    with pytest.raises(SnapshotError):
        raw_file_name("account_detail", bad)
    with pytest.raises(SnapshotError):
        SnapshotWriter(tmp_path, bad, now=T1)
    with pytest.raises(SnapshotError):
        list_snapshots(tmp_path, bad)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", ["../20261113_093000", "LATEST", "2026", "20261113_093000/raw"])
def test_load_refuses_bad_snapshot_ids(tmp_path: Path, bad: str) -> None:
    write_simple(tmp_path, T1)
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path, "acme", bad)


def test_corrupt_files_raise_snapshot_error(tmp_path: Path) -> None:
    snapshot_id = write_simple(tmp_path, T1)
    folder = tmp_path / "acme" / snapshot_id
    (folder / "raw" / "account_detail__1001.json").write_text("{not json")
    snapshot = load_snapshot(tmp_path, "acme", snapshot_id)
    with pytest.raises(SnapshotError):
        snapshot.raw("account_detail", "1001")

    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["status"] = "weird"
    (folder / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path, "acme", snapshot_id)


def test_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == __version__


def test_list_clients(tmp_path: Path) -> None:
    assert list_clients(tmp_path / "absent") == []
    write_simple(tmp_path, T1)
    SnapshotWriter(tmp_path, "other", now=T2).finish(status="complete")
    SnapshotWriter(tmp_path, "unfinished", now=T2)  # no manifest: not listed
    (tmp_path / "stray.txt").write_text("x")
    assert list_clients(tmp_path) == ["acme", "other"]


def test_a_snapshot_from_the_other_environment_is_refused(tmp_path: Path) -> None:
    uat = SnapshotWriter(tmp_path, "acme", now=T1, environment="ext_uat_cert").finish(
        status="complete"
    )
    assert uat.manifest["environment"] == "ext_uat_cert"
    assert load_snapshot(tmp_path, "acme", uat.id, environment="ext_uat_cert").id == uat.id
    with pytest.raises(SnapshotError, match="pulled from ext_uat_cert"):
        load_snapshot(tmp_path, "acme", uat.id, environment="ext_prod_live")

    # snapshots saved before UAT existed carry no environment: they are Live
    live = SnapshotWriter(tmp_path, "acme", now=T2).finish(status="complete")
    manifest = live.path / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["environment"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert load_snapshot(tmp_path, "acme", live.id, environment="ext_prod_live").id == live.id
    with pytest.raises(SnapshotError, match="pulled from ext_prod_live"):
        load_snapshot(tmp_path, "acme", live.id, environment="ext_uat_cert")
