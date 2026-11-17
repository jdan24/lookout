"""Behaviour of the profile-driven rules beyond the parity baseline."""

from __future__ import annotations

from pathlib import Path

import pytest

from checks.setup import otd
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
    users = got[("USERS", "ABC10001AL", "Users")]
    assert users.actual == "Missing: cat.trader (9003)"
    assert "Assign cat.trader (9003) on ABC10001AL or on an account above it" in users.message
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
        ("USERS", "ABC10001AL", "Users"),  # FUND's users were not read
        ("USERS", "ABC20002BE", "Users"),
    ]:
        assert outcome(got[key]) == ("amber", "UNKNOWN"), key
    assert "Users on FUND were not read" in got[("USERS", "ABC10001AL", "Users")].message
    # the south tree's users were read, so they are still judged
    assert outcome(got[("USERS", "S-ABC10001AL", "Users")]) == ("green", "")


def test_partial_order_tag_defaults_give_unknown_not_missing(tmp_path: Path) -> None:
    setup = load_setup(synthetic.build_snapshot(tmp_path, otd_reached_end=False))
    _add_limit(setup, "210", 2, "FGBL")  # FUND-BETA uses Eurex
    _add_limit(setup, "200", 91, "SXF")  # FUND-ALPHA uses MX
    got = run(setup)
    unknown = got[("OTD", "ABC20002BE", "Clearing Account (Eurex)")]
    assert outcome(unknown) == ("amber", "UNKNOWN") and "not read to the end" in unknown.message
    assert outcome(got[("OTD", "ABC10001AL", "Clearing Account (MX)")]) == ("red", "MISMATCH")
    assert outcome(got[("OTD", "ABC20002BE", "Clearing Account (CME)")]) == ("red", "WRONG_LEVEL")


def _add_limit(setup: SetupData, account_id: str, market: int, symbol: str) -> None:
    setup.details[account_id]["riskLimits"]["productLimits"].append(synthetic.limit(market, symbol))


def test_account_defaults_are_not_checked_on_markets_without_limits(setup: SetupData) -> None:
    got = run(setup)
    # FUND-BETA (the level that holds limits) has CME limits but none on Eurex or HKEX.
    assert outcome(got[("OTD", "ABC20002BE", "Clearing Account (CME)")]) == ("red", "WRONG_LEVEL")
    skipped = got[("OTD", "ABC20002BE", "Clearing Account (Eurex)")]
    assert outcome(skipped) == ("grey", "NOT_APPLICABLE")
    assert skipped.actual == "No limits for Eurex on FUND-BETA" and skipped.actual_level == "Team"
    assert skipped.message.startswith("Not checked: FUND-BETA has no limits for Eurex")
    # every kind of account default is skipped the same way
    assert outcome(got[("OTD", "ABC10001AL", "Exchange Account (HKEX)")])[0] == "grey"
    assert outcome(got[("OTD", "ABC10001AL", "Clearing Firm ID (HKEX)")])[0] == "grey"

    _add_limit(setup, "210", 2, "FGBL")  # once the team has a Eurex limit, it is checked again
    assert outcome(run(setup)[("OTD", "ABC20002BE", "Clearing Account (Eurex)")]) == (
        "red",
        "MISSING",
    )


def test_a_limit_of_any_kind_on_the_market_counts(setup: SetupData) -> None:
    setup.details["210"]["riskLimits"]["contractLimits"].append(synthetic.limit(2, "FGBL"))
    assert outcome(run(setup)[("OTD", "ABC20002BE", "Clearing Account (Eurex)")])[0] == "red"


def test_account_defaults_are_checked_as_before_when_limits_cannot_be_told(
    tmp_path: Path, setup: SetupData
) -> None:
    key = ("OTD", "ABC20002BE", "Clearing Account (Eurex)")
    # no level says where limits live: nothing is skipped
    profile = synthetic.profile()
    trees = [
        t.model_copy(
            update={
                "levels": [lv.model_copy(update={"limit_rows_required": False}) for lv in t.levels]
            }
        )
        for t in profile.trees
    ]
    got = run(setup, profile.model_copy(update={"trees": trees}))
    assert outcome(got[key]) == ("red", "MISSING")
    assert not [row for row in got.values() if row.colour.value == "grey"]
    # the account that holds the limits was not read: checked, not skipped
    unread = load_setup(synthetic.build_snapshot(tmp_path, skip_details=(210,)))
    assert outcome(run(unread)[key]) == ("red", "MISSING")


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


def _with_required_users(tree_name: str, users: list[str]) -> Profile:
    profile = synthetic.profile()
    trees = [
        t.model_copy(update={"required_users": users}) if t.name == tree_name else t
        for t in profile.trees
    ]
    return profile.model_copy(update={"trees": trees})


def test_users_are_checked_on_the_lowest_level_only_and_inherited_ones_count(
    setup: SetupData,
) -> None:
    got = run(setup)
    users = {key[1]: row for key, row in got.items() if key[0] == "USERS"}
    # only trading accounts; no client group, team or unfitted account
    assert set(users) == {
        "ABC10001AL", "ABC10001IX", "ABC20002BE", "BETA-ACCT", "ABC20012IX", "ABC30003GA",
        "S-ABC10001AL", "S-ABC20002BE", "ABC20022BE",
    }  # fmt: skip
    # Cat is assigned on FUND-BETA only: accounts under it pass, others miss Cat.
    beta = users["ABC20002BE"]
    assert outcome(beta) == ("green", "")
    assert "cat.trader (9003) from FUND-BETA" in beta.actual
    assert "ann.trader (9001) from FUND" in beta.actual
    assert outcome(users["ABC30003GA"]) == ("red", "MISSING")
    # Bob sits on both FUND and FUND-BETA; a repeated assignment is not a finding any more.
    assert not [key for key in got if key[0] == "USERS" and key[2] != "Users"]


def test_a_user_assigned_on_the_account_itself_counts(setup: SetupData) -> None:
    setup.users["320"] = [dict(synthetic.USER_CAT)]
    row = run(setup)[("USERS", "ABC30003GA", "Users")]
    assert outcome(row) == ("green", "")
    assert "cat.trader (9003) from ABC30003GA" in row.actual


def test_a_required_user_never_seen_is_named_by_id(setup: SetupData) -> None:
    row = run(setup, _with_required_users("south", ["9001", "4242"]))[
        ("USERS", "S-ABC10001AL", "Users")
    ]
    assert outcome(row) == ("red", "MISSING")
    assert row.actual == "Missing: user 4242"


def test_no_required_users_means_no_users_rows(setup: SetupData) -> None:
    got = run(setup, _with_required_users("north", []))
    assert {key[1] for key in got if key[0] == "USERS"} == {
        "S-ABC10001AL", "S-ABC20002BE", "ABC20022BE",
    }  # fmt: skip


def test_verify_snapshot(tmp_path: Path) -> None:
    result = verify_snapshot(synthetic.build_snapshot(tmp_path), synthetic.profile())
    counts = {c: sum(r.colour.value == c for r in result.rows) for c in ("green", "amber", "red")}
    assert counts["red"] > 0 and counts["amber"] > 0 and counts["green"] > counts["red"]


def test_users_endpoint_not_read_is_unknown_even_when_the_detail_was(tmp_path: Path) -> None:
    got = run(load_setup(synthetic.build_snapshot(tmp_path, skip_users=(210,))))
    # Cat may be on FUND-BETA, which was not read: unknown, not missing.
    assert outcome(got[("USERS", "ABC20002BE", "Users")]) == ("amber", "UNKNOWN")
    # FUND-GAMMA's accounts do not depend on FUND-BETA, so Cat is plainly missing there.
    assert outcome(got[("USERS", "ABC30003GA", "Users")]) == ("red", "MISSING")


def test_unexpected_or_missing_checkbox_values_are_amber_not_a_crash(setup: SetupData) -> None:
    setup.details["200"]["riskLimitSettings"]["noLimits"] = 2
    del setup.details["210"]["restrictions"]["applyRestrictions"]
    got = run(setup)
    odd = got[("LIMITS", "FUND-ALPHA", "Apply Limits")]
    assert outcome(odd) == ("amber", "UNKNOWN") and "noLimits = 2" in odd.message
    gone = got[("RESTRICTIONS", "FUND-BETA", "Apply Restrictions")]
    assert outcome(gone) == ("amber", "UNKNOWN") and "no Apply Restrictions value" in gone.message
