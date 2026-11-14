"""Behaviour of the profile-driven rules beyond the parity baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from checks.setup import otd, rules, views
from checks.setup.levels import assign_levels
from checks.setup.profile import CompanionAccountRule, OptionalRules, Profile
from checks.setup.verify import run_checks, verify_snapshot
from core.results import ResultRow
from core.setup_model import SetupData, load_setup
from tests.fixtures import synthetic


@pytest.fixture
def setup(tmp_path: Path) -> SetupData:
    return load_setup(synthetic.build_snapshot(tmp_path))


def keyed(rows: list[ResultRow]) -> dict[tuple[str, str, str], ResultRow]:
    return {(r.rule_id, r.node.split(" (")[0], r.setting): r for r in rows}


def outcome(row: ResultRow) -> tuple[str, str]:
    return row.colour.value, row.reason.value if row.reason else ""


def run(setup: SetupData, profile: Profile | None = None) -> dict[tuple[str, str, str], ResultRow]:
    return keyed(run_checks(setup, profile or synthetic.profile()).rows)


def test_every_non_green_row_says_what_to_do(setup: SetupData) -> None:
    for row in run_checks(setup, synthetic.profile()).rows:
        if row.colour.value != "green":
            assert row.reason is not None and len(row.message) > 20, row


def test_messages(setup: SetupData) -> None:
    got = run(setup)
    assert (
        got[("LIMITS", "FUND-BETA", "Apply Limits")].message
        == "Tick Apply Limits on team FUND-BETA."
    )
    assert (
        "Limits belong on the Team level" in got[("LIMITS", "ABC20002BE", "Apply Limits")].message
    )
    restr = got[("RESTRICTIONS", "S-FUND-BETA", "Apply Restrictions")]
    assert restr.expected_level == "Client group (S-FUND)"
    assert "set on Client group (S-FUND) only" in restr.message
    assert "Move them from FUND-BETA" in got[("MARKETS", "FUND-BETA", "Limit market: ASX")].message
    assert (
        "not listed in any tree"
        in got[("MARKETS", "FUND-BETA", "Limit market: market 216")].message
    )
    naming = got[("NAMING", "ABC20022BE", "Name")]
    assert naming.expected == "S-<account number><2-letter team code>"
    assert (
        "Add the IX account under FUND-GAMMA"
        in got[("COMPANION", "FUND-GAMMA", "Index account")].message
    )
    level = got[("LEVEL", "LON40004AA", "Level")]
    assert level.expected == "Team, account type 3" and "does not fit any level" in level.message
    assert got[("OTD", "ABC20002BE", "Clearing Account (CME)")].actual_level == "Team"
    firm = got[("OTD", "ABC30003GA", "Clearing Firm ID (Euronext)")]
    assert firm.expected_level == "Trading account, Team or Client group"
    assert "Set 77777 on ABC30003GA" in firm.message
    assert got[("USERS", "FUND-BETA", "User bob.trader")].expected_level == "Client group (FUND)"
    legacy = got[("LEGACY", "FUND-OLDPOD", "Legacy group")]
    assert legacy.expected == "Under FUND or S-FUND"


def test_unread_accounts_are_amber_unknown(tmp_path: Path) -> None:
    setup = load_setup(
        synthetic.build_snapshot(tmp_path, skip_details=(210, 310, 100), skip_users=(100,))
    )
    got = run(setup)
    for key in [
        ("LIMITS", "FUND-BETA", "Apply Limits"),
        ("RESTRICTIONS", "ABC20002BE", "Apply Restrictions"),
        ("COMPANION", "FUND-BETA", "Index account"),
        ("USERS", "FUND", "Users"),
        ("USERS", "FUND-ALPHA", "Users"),
    ]:
        assert outcome(got[key]) == ("amber", "UNKNOWN"), key
    # the south tree's users were read, so they are still judged
    assert outcome(got[("USERS", "S-FUND-ALPHA", "Users")]) == ("green", "")


def test_partial_order_tag_defaults_give_unknown_not_missing(tmp_path: Path) -> None:
    got = run(load_setup(synthetic.build_snapshot(tmp_path, otd_reached_end=False)))
    unknown = got[("OTD", "ABC20002BE", "Clearing Account (Eurex)")]
    assert outcome(unknown) == ("amber", "UNKNOWN") and "not read to the end" in unknown.message
    assert outcome(got[("OTD", "ABC10001AL", "Clearing Account (MX)")]) == ("red", "MISMATCH")
    assert outcome(got[("OTD", "ABC20002BE", "Clearing Account (CME)")]) == ("red", "WRONG_LEVEL")


def test_enum_values_are_shown_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fields = [
        *synthetic.OTD_FIELDS,
        {
            "fieldId": 77,
            "name": "Some Choice",
            "marketId": 7,
            "valueEnums": [{"valueEnumId": 3, "valueEnumName": "Third"}],
        },
    ]
    entries = [
        *synthetic.OTD_ENTRIES,
        synthetic.otd(97, 300, 7, 77, "", valueEnumId=3),
        synthetic.otd(98, 300, 2, 77, "", valueEnumId=0),  # neither value nor enum: blank
    ]
    monkeypatch.setattr(synthetic, "OTD_FIELDS", fields)
    monkeypatch.setattr(synthetic, "OTD_ENTRIES", entries)
    setup = load_setup(synthetic.build_snapshot(tmp_path))
    index = otd.OrderTagDefaultIndex(setup, synthetic.profile().otd_default_key)
    assert index.on_account("300", "7", "77") == "Third"
    assert index.on_account("300", "2", "77") is None
    assert index.on_account("300", "7", "1859") == "ABC10001"  # the user-specific entry is ignored


def test_optional_rules_can_be_switched_off(setup: SetupData) -> None:
    profile = synthetic.profile().model_copy(update={"rules": OptionalRules()})
    ids = {key[0] for key in run(setup, profile)}
    assert "COMPANION" not in ids and "LEGACY" not in ids and "OTD" in ids


def test_unnecessary_companion_can_be_red(setup: SetupData) -> None:
    profile = synthetic.profile()
    rule: CompanionAccountRule = profile.rules.companion_account
    red = profile.model_copy(
        update={
            "rules": profile.rules.model_copy(
                update={"companion_account": rule.model_copy(update={"unnecessary": "red"})}
            )
        }
    )
    row = run(setup, red)[("COMPANION", "ABC20012IX", "Index account")]
    assert outcome(row) == ("red", "WRONG_LEVEL")


def test_inherited_users_need_a_level_with_users_here(setup: SetupData) -> None:
    profile = synthetic.profile()
    north = profile.tree("north")
    root = north.levels[0].model_copy(update={"users": "any"})
    trees = [north.model_copy(update={"levels": [root, *north.levels[1:]]}), profile.trees[1]]
    got = run(setup, profile.model_copy(update={"trees": trees}))
    assert outcome(got[("USERS", "FUND-ALPHA", "Users")]) == ("amber", "NO_RULE")
    assert "Fix the profile" in got[("USERS", "FUND-ALPHA", "Users")].message


def test_display_only_values_are_listed_not_judged(setup: SetupData) -> None:
    profile = synthetic.profile()
    shown = rules.manual_check_values(setup, assign_levels(setup.tree, profile), profile)
    items = {(r["node"], r["item"]) for r in shown}
    assert ("FUND-ALPHA (200)", "Description") in items
    assert ("Company default (any account)", "Operator ID (user 9001)") in items
    assert not any(r["node"].startswith("OTHER") for r in shown)


def test_views(tmp_path: Path) -> None:
    profile = synthetic.profile()
    setup = load_setup(synthetic.build_snapshot(tmp_path, skip_details=(300,)))
    levels = assign_levels(setup.tree, profile)
    limits = views.limits_by_level(setup, levels, profile)
    assert {r["Account"] for r in limits} == {
        "FUND-ALPHA (200)",
        "FUND-BETA (210)",
        "FUND-GAMMA (220)",
        "S-FUND-ALPHA (600)",
        "S-FUND-BETA (610)",
    }
    first: dict[str, Any] = limits[0]
    assert (first["Level"], first["Start account"], first["Exchange"]) == ("Team", "FUND", "CME")
    assert first["Product family id"] == synthetic.BIG_FAMILY_ID
    assert next(r for r in limits if r["Product"] == "QQ")["Exchange"] == "market 216"
    assert all(isinstance(v, str) for row in limits for v in row.values())

    settings = views.settings_by_node(setup, levels)
    team = {r["json_path"]: r for r in settings if r["node"] == "FUND-ALPHA (200)"}
    assert team["riskLimitSettings.noLimits"]["value"] == "0"
    nodes = {r["node"] for r in settings}
    assert "ABC10001AL (300)" not in nodes
    assert not any("OTHER" in n or "NORTHCO" in n for n in nodes)


def test_verify_snapshot(tmp_path: Path) -> None:
    result = verify_snapshot(synthetic.build_snapshot(tmp_path), synthetic.profile())
    counts = {c: sum(r.colour.value == c for r in result.rows) for c in ("green", "amber", "red")}
    assert counts["red"] > 0 and counts["amber"] > 0 and counts["green"] > counts["red"]


def test_users_endpoint_not_read_is_unknown_even_when_the_detail_was(tmp_path: Path) -> None:
    got = run(load_setup(synthetic.build_snapshot(tmp_path, skip_users=(210,))))
    beta = [r for key, r in got.items() if key[0] == "USERS" and key[1] == "FUND-BETA"]
    assert [outcome(r) for r in beta] == [("amber", "UNKNOWN")]


def test_unexpected_or_missing_checkbox_values_are_amber_not_a_crash(setup: SetupData) -> None:
    setup.details["200"]["riskLimitSettings"]["noLimits"] = 2
    del setup.details["210"]["restrictions"]["applyRestrictions"]
    got = run(setup)
    odd = got[("LIMITS", "FUND-ALPHA", "Apply Limits")]
    assert outcome(odd) == ("amber", "UNKNOWN") and "noLimits = 2" in odd.message
    gone = got[("RESTRICTIONS", "FUND-BETA", "Apply Restrictions")]
    assert outcome(gone) == ("amber", "UNKNOWN") and "no Apply Restrictions value" in gone.message
