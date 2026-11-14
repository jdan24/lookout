"""The generic, profile-driven rules give the same results as the original client rules.

``tests/fixtures/setup_baseline.json`` holds the output of the original rules on the
synthetic fixture; ``docs/example_client.toml`` describes the same setup as a profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checks.setup.profile import load_profile
from checks.setup.verify import verify_snapshot
from tests.fixtures import synthetic

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "fixtures" / "setup_baseline.json"
EXAMPLE = ROOT / "docs" / "example_client.toml"

# old rule id -> new rule id
RULE_IDS = {
    "A1": "LIMITS",
    "A2": "RESTRICTIONS",
    "B1": "MARKETS",
    "B2": "NAMING",
    "B3": "COMPANION",
    "B4": "OTD",
    "B5": "OTD",
    "B6": "OTD",
    "B7": "USERS",
    "B9": "LEGACY",
}


def _key(rule_id: str, node: str, setting: str) -> tuple[str, str, str]:
    if (rule_id, setting) == ("A1", "Placement"):
        return ("LEVEL", node, "Level")
    return (RULE_IDS.get(rule_id, rule_id), node, setting)


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str, str], list[str]]:
    snapshot = synthetic.build_snapshot(tmp_path_factory.mktemp("snap"))
    rows = verify_snapshot(snapshot, load_profile(EXAMPLE)).rows
    got: dict[tuple[str, str, str], list[str]] = {}
    for r in rows:
        key = (r.rule_id, r.node, r.setting)
        assert key not in got, f"duplicate row {key}"
        got[key] = [
            r.colour.value,
            r.reason.value if r.reason else "",
            r.expected,
            r.expected_level,
            r.actual,
            r.actual_level,
        ]
    return got


def baseline() -> dict[tuple[str, str, str], list[str]]:
    rows = json.loads(BASELINE.read_text(encoding="utf-8"))["rows"]
    return {_key(r[0], r[1], r[2]): r[3:] for r in rows}


def test_same_rows_colours_and_reasons(results: dict) -> None:
    old = baseline()
    assert set(results) == set(old)
    differences = {k: (old[k][:2], results[k][:2]) for k in old if old[k][:2] != results[k][:2]}
    assert differences == {}


def test_same_expected_and_actual_values(results: dict) -> None:
    old = baseline()
    differences = {
        k: (old[k][2:], results[k][2:])
        for k in old
        if k[0] not in ("LEVEL", "LEGACY")
        and (old[k][2], old[k][4]) != (results[k][2], results[k][4])
    }
    # The generic companion rule names the products; the old rule named the product group.
    unnecessary = ("COMPANION", "ABC20012IX (312)", "Index account")
    assert differences.pop(unnecessary)[1][:3] == [
        "A limit on jFCE, iFIB, lPSI, bBEL on FUND-BETA",
        "Team",
        "No such limit",
    ]
    assert differences == {}
