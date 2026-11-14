from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from checks.setup.levels import LevelError, assign_levels, levels_table
from checks.setup.profile import Profile
from core.hierarchy import AccountTree
from core.setup_model import SetupData, load_setup
from tests.fixtures import synthetic


@pytest.fixture
def setup(tmp_path: Path) -> SetupData:
    return load_setup(synthetic.build_snapshot(tmp_path))


def with_tree(index: int, **changes: Any) -> Profile:
    profile = synthetic.profile()
    trees = list(profile.trees)
    trees[index] = trees[index].model_copy(update=changes)
    return profile.model_copy(update={"trees": trees})


def test_levels_on_the_synthetic_setup(setup: SetupData) -> None:
    levels = assign_levels(setup.tree, synthetic.profile())
    got = {p.account.name: p.label for p in levels.in_scope()}
    assert got["FUND"] == "Client group (FUND)"
    assert got["FUND-ALPHA"] == "Team" and got["ABC10001AL"] == "Trading account"
    assert got["S-FUND"] == "Client group (S-FUND)" and got["S-ABC10001AL"] == "Trading account"
    assert got["LON40004AA"] == "Depth 1"  # account type 1 does not fit the team level
    assert levels.places["240"].level is None
    assert levels.places["230"].empty and not levels.places["200"].empty
    assert set(levels.path_ids) == {"10", "11", "20"}
    assert "900" not in levels.places and "51" not in levels.places
    assert [p.account.name for p in levels.chain(levels.places["300"])] == [
        "ABC10001AL",
        "FUND-ALPHA",
        "FUND",
    ]
    table = {r["Account"]: r for r in levels_table(levels)}
    assert table["LON40004AA (240)"]["Level"] == "Does not fit any level"
    assert table["FUND-EMPTY (230)"]["Below it"] == "nothing"


def test_deeper_accounts_use_the_deepest_level() -> None:
    def item(id_: int, name: str, parent: int, type_: int = 3) -> dict:
        return {"id": id_, "name": name, "parentAccountId": parent, "accountType": type_}

    tree = AccountTree(
        [
            item(10, "NORTHCO", 0),
            item(11, "BROKER", 10),
            item(100, "FUND", 11),
            item(200, "FUND-ALPHA", 100),
            item(300, "ABC10001AL", 200, 1),
            item(400, "ABC10001SUB", 300, 1),
            item(20, "SOUTHCO", 0),
            item(500, "S-FUND", 20),
        ]
    )
    levels = assign_levels(tree, synthetic.profile())
    sub = levels.places["400"]
    assert (sub.label, sub.level.label, sub.depth) == ("Sub-account", "Trading account", 3)


def test_several_levels_at_one_depth_first_fit_wins(setup: SetupData) -> None:
    north = synthetic.profile().tree("north")
    standalone = north.levels[1].model_copy(
        update={"label": "Standalone", "account_type": 1, "may_be_empty": False}
    )
    profile = with_tree(0, levels=[*north.levels, standalone])
    levels = assign_levels(setup.tree, profile)
    assert levels.places["240"].label == "Standalone"
    assert levels.places["200"].label == "Team"


def test_path_must_exist(setup: SetupData) -> None:
    with pytest.raises(LevelError, match="'NOWHERE'"):
        assign_levels(setup.tree, with_tree(1, path=["SOUTHCO", "NOWHERE"]))
    with pytest.raises(LevelError, match="'S-FUND' under BROKER"):
        assign_levels(setup.tree, with_tree(1, path=["NORTHCO", "BROKER", "S-FUND"]))


def test_start_id_must_agree_unless_unverified(setup: SetupData) -> None:
    with pytest.raises(LevelError, match="id 424242"):
        assign_levels(setup.tree, with_tree(1, start_id="424242"))
    levels = assign_levels(setup.tree, with_tree(1, start_id="424242", id_verified=False))
    assert levels.places["500"].depth == 0
    assert "424242" in levels.notes[0] and "S-FUND (500)" in levels.notes[0]


def test_trees_must_not_overlap(setup: SetupData) -> None:
    with pytest.raises(LevelError, match="overlap"):
        assign_levels(
            setup.tree,
            with_tree(1, path=["NORTHCO", "BROKER", "FUND", "FUND-ALPHA"], start_id=None),
        )
