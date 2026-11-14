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
        got[("LIMITS", "CLI-BETA", "Apply Limits")].message == "Tick Apply Limits on pod CLI-BETA."
    )
    assert (
        "Limits belong on the Pod level" in got[("LIMITS", "ABCD2000BET", "Apply Limits")].message
    )
    restr = got[("RESTRICTIONS", "CB-CLI-BETA", "Apply Restrictions")]
    assert restr.expected_level == "Client group (CB-CLI)"
    assert "set on Client group (CB-CLI) only" in restr.message
    assert "Move them from CLI-BETA" in got[("MARKETS", "CLI-BETA", "Limit market: ASX")].message
    assert (
        "not listed in any tree" in got[("MARKETS", "CLI-BETA", "Limit market: market 216")].message
    )
    naming = got[("NAMING", "ABCD2002BET", "Name")]
    assert naming.expected == "CB-<GMI account><3-letter pod code>"
    assert (
        "Add the NX account under CLI-GAMMA"
        in got[("COMPANION", "CLI-GAMMA", "Endex account")].message
    )
    level = got[("LEVEL", "LONE4000AAA", "Level")]
    assert level.expected == "Pod, account type 3" and "does not fit any level" in level.message
    assert got[("OTD", "ABCD2000BET", "Clearing Account (CME)")].actual_level == "Pod"
    firm = got[("OTD", "ABCD3000GAM", "Clearing Firm ID (ICE)")]
    assert firm.expected_level == "Trading account, Pod or Client group"
    assert "Set 55555 on ABCD3000GAM" in firm.message
    assert got[("USERS", "CLI-BETA", "User bob.trader")].expected_level == "Client group (CLI)"
    legacy = got[("LEGACY", "CLI-OLDPOD", "Legacy group")]
    assert legacy.expected == "Under CLI or CB-CLI"


def test_unread_accounts_are_amber_unknown(tmp_path: Path) -> None:
    setup = load_setup(
        synthetic.build_snapshot(tmp_path, skip_details=(210, 310, 100), skip_users=(100,))
    )
    got = run(setup)
    for key in [
        ("LIMITS", "CLI-BETA", "Apply Limits"),
        ("RESTRICTIONS", "ABCD2000BET", "Apply Restrictions"),
        ("COMPANION", "CLI-BETA", "Endex account"),
        ("USERS", "CLI", "Users"),
        ("USERS", "CLI-ALPHA", "Users"),
    ]:
        assert outcome(got[key]) == ("amber", "UNKNOWN"), key
    # the CB tree's users were read, so they are still judged
    assert outcome(got[("USERS", "CB-CLI-ALPHA", "Users")]) == ("green", "")


def test_partial_order_tag_defaults_give_unknown_not_missing(tmp_path: Path) -> None:
    got = run(load_setup(synthetic.build_snapshot(tmp_path, otd_reached_end=False)))
    unknown = got[("OTD", "ABCD2000BET", "Clearing Account (Eurex)")]
    assert outcome(unknown) == ("amber", "UNKNOWN") and "not read to the end" in unknown.message
    assert outcome(got[("OTD", "ABCD1000ALP", "Clearing Account (CFE)")]) == ("red", "MISMATCH")
    assert outcome(got[("OTD", "ABCD2000BET", "Clearing Account (CME)")]) == ("red", "WRONG_LEVEL")


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
    assert index.on_account("300", "7", "1859") == "ABCD1000"  # the user-specific entry is ignored


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
    row = run(setup, red)[("COMPANION", "ABCD2001BNX", "Endex account")]
    assert outcome(row) == ("red", "WRONG_LEVEL")


def test_inherited_users_need_a_level_with_users_here(setup: SetupData) -> None:
    profile = synthetic.profile()
    member = profile.tree("member")
    root = member.levels[0].model_copy(update={"users": "any"})
    trees = [member.model_copy(update={"levels": [root, *member.levels[1:]]}), profile.trees[1]]
    got = run(setup, profile.model_copy(update={"trees": trees}))
    assert outcome(got[("USERS", "CLI-ALPHA", "Users")]) == ("amber", "NO_RULE")
    assert "Fix the profile" in got[("USERS", "CLI-ALPHA", "Users")].message


def test_display_only_values_are_listed_not_judged(setup: SetupData) -> None:
    profile = synthetic.profile()
    shown = rules.manual_check_values(setup, assign_levels(setup.tree, profile), profile)
    items = {(r["node"], r["item"]) for r in shown}
    assert ("CLI-ALPHA (200)", "Description") in items
    assert ("Company default (any account)", "Operator ID (user 9001)") in items
    assert not any(r["node"].startswith("OTHER") for r in shown)


def test_views(tmp_path: Path) -> None:
    profile = synthetic.profile()
    setup = load_setup(synthetic.build_snapshot(tmp_path, skip_details=(300,)))
    levels = assign_levels(setup.tree, profile)
    limits = views.limits_by_level(setup, levels, profile)
    assert {r["Account"] for r in limits} == {
        "CLI-ALPHA (200)",
        "CLI-BETA (210)",
        "CLI-GAMMA (220)",
        "CB-CLI-ALPHA (600)",
        "CB-CLI-BETA (610)",
    }
    first: dict[str, Any] = limits[0]
    assert (first["Level"], first["Start account"], first["Exchange"]) == ("Pod", "CLI", "CME")
    assert first["Product family id"] == synthetic.BIG_FAMILY_ID
    assert next(r for r in limits if r["Product"] == "QQ")["Exchange"] == "market 216"
    assert all(isinstance(v, str) for row in limits for v in row.values())

    settings = views.settings_by_node(setup, levels)
    pod = {r["json_path"]: r for r in settings if r["node"] == "CLI-ALPHA (200)"}
    assert pod["riskLimitSettings.noLimits"]["value"] == "0"
    nodes = {r["node"] for r in settings}
    assert "ABCD1000ALP (300)" not in nodes
    assert not any("OTHER" in n or "TOPBANK" in n for n in nodes)


def test_verify_snapshot(tmp_path: Path) -> None:
    result = verify_snapshot(synthetic.build_snapshot(tmp_path), synthetic.profile())
    counts = {c: sum(r.colour.value == c for r in result.rows) for c in ("green", "amber", "red")}
    assert counts["red"] > 0 and counts["amber"] > 0 and counts["green"] > counts["red"]
