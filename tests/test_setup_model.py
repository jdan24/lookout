from __future__ import annotations

from pathlib import Path

from core.setup_model import load_setup
from tests.fixtures import synthetic


def test_setup_is_rebuilt_from_a_snapshot(tmp_path: Path) -> None:
    setup = load_setup(synthetic.build_snapshot(tmp_path))

    assert len(setup.tree) == len(synthetic.ACCOUNTS)
    assert setup.snapshot_status == "complete"
    assert setup.tree.get(200).name == "FUND-ALPHA"

    # details exist for in-scope accounts only
    assert set(setup.details) == {str(i) for i in synthetic.in_scope_ids()}
    assert setup.detail("51") is None and setup.detail("10") is None

    assert setup.markets["7"] == "CME"
    assert setup.otd_fields["1859"]["name"] == "Clearing Account"
    assert setup.otd_reached_end is True
    assert {str(e["accountId"]) for e in setup.otd_entries} >= {"0", "100", "300"}
    assert "51" not in {str(e["accountId"]) for e in setup.otd_entries}


def test_checkbox_helpers_handle_the_inverted_no_limits_flag(tmp_path: Path) -> None:
    setup = load_setup(synthetic.build_snapshot(tmp_path))
    assert setup.detail("200")["riskLimitSettings"]["noLimits"] == 0
    assert setup.apply_limits("200") is True
    assert setup.detail("300")["riskLimitSettings"]["noLimits"] == 1
    assert setup.apply_limits("300") is False
    assert setup.apply_restrictions("200") is True
    assert setup.apply_restrictions("600") is False
    assert setup.apply_limits("51") is None
    assert setup.apply_restrictions("51") is None


def test_limit_rows_and_long_ids(tmp_path: Path) -> None:
    setup = load_setup(synthetic.build_snapshot(tmp_path))
    rows = setup.limit_rows("200")
    assert [r["productSymbol"] for r in rows] == ["ZN", "FGBL", "jFCE"]
    assert rows[0]["productFamilyId"] == synthetic.BIG_FAMILY_ID
    assert setup.limit_rows("200", "contractLimits") == []
    assert setup.limit_rows("300") == []
    assert setup.limit_rows("51") == []


def test_direct_users(tmp_path: Path) -> None:
    setup = load_setup(synthetic.build_snapshot(tmp_path, skip_users=(210,)))
    assert setup.direct_users("100") == [
        {"id": "9001", "name": "ann.trader"},
        {"id": "9002", "name": "bob.trader"},
    ]
    assert setup.direct_users("200") == []
    # users endpoint not read: unknown, even though the account detail was read
    assert setup.detail("210") is not None
    assert setup.direct_users("210") is None
    assert setup.direct_users("51") is None


def test_unread_parts_are_reported_as_unknown(tmp_path: Path) -> None:
    snapshot = synthetic.build_snapshot(tmp_path, otd_reached_end=False, skip_details=(310,))
    setup = load_setup(snapshot)
    assert setup.snapshot_status == "partial"
    assert setup.otd_reached_end is False
    assert setup.detail("310") is None
    assert setup.apply_limits("310") is None


def test_market_name_lookup(tmp_path: Path) -> None:
    setup = load_setup(synthetic.build_snapshot(tmp_path))
    assert setup.market_name(7) == "CME"
    assert setup.market_name("107", {"107": "HKEX"}) == "HKEX"
    assert setup.market_name(216) == "market 216"
