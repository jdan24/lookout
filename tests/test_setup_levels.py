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
    assert got["CLI"] == "Client group (CLI)"
    assert got["CLI-ALPHA"] == "Pod" and got["ABCD1000ALP"] == "Trading account"
    assert got["CB-CLI"] == "Client group (CB-CLI)" and got["CB-ABCD1000ALP"] == "Trading account"
    assert got["LONE4000AAA"] == "Depth 1"  # account type 1 does not fit the pod level
    assert levels.places["240"].level is None
    assert levels.places["230"].empty and not levels.places["200"].empty
    assert set(levels.path_ids) == {"10", "11", "20"}
    assert "900" not in levels.places and "51" not in levels.places
    assert [p.account.name for p in levels.chain(levels.places["300"])] == [
        "ABCD1000ALP",
        "CLI-ALPHA",
        "CLI",
    ]
    table = {r["Account"]: r for r in levels_table(levels)}
    assert table["LONE4000AAA (240)"]["Level"] == "Does not fit any level"
    assert table["CLI-EMPTY (230)"]["Below it"] == "nothing"


def test_deeper_accounts_use_the_deepest_level() -> None:
    def item(id_: int, name: str, parent: int, type_: int = 3) -> dict:
        return {"id": id_, "name": name, "parentAccountId": parent, "accountType": type_}

    tree = AccountTree(
        [
            item(10, "TOPBANK", 0),
            item(11, "DESK", 10),
            item(100, "CLI", 11),
            item(200, "CLI-ALPHA", 100),
            item(300, "ABCD1000ALP", 200, 1),
            item(400, "ABCD1000SUB", 300, 1),
            item(20, "CBTOP", 0),
            item(500, "CB-CLI", 20),
        ]
    )
    levels = assign_levels(tree, synthetic.profile())
    sub = levels.places["400"]
    assert (sub.label, sub.level.label, sub.depth) == ("Sub-account", "Trading account", 3)


def test_several_levels_at_one_depth_first_fit_wins(setup: SetupData) -> None:
    member = synthetic.profile().tree("member")
    standalone = member.levels[1].model_copy(
        update={"label": "Standalone", "account_type": 1, "may_be_empty": False}
    )
    profile = with_tree(0, levels=[*member.levels, standalone])
    levels = assign_levels(setup.tree, profile)
    assert levels.places["240"].label == "Standalone"
    assert levels.places["200"].label == "Pod"


def test_path_must_exist(setup: SetupData) -> None:
    with pytest.raises(LevelError, match="'NOWHERE'"):
        assign_levels(setup.tree, with_tree(1, path=["CBTOP", "NOWHERE"]))
    with pytest.raises(LevelError, match="'CB-CLI' under DESK"):
        assign_levels(setup.tree, with_tree(1, path=["TOPBANK", "DESK", "CB-CLI"]))


def test_start_id_must_agree_unless_unverified(setup: SetupData) -> None:
    with pytest.raises(LevelError, match="id 424242"):
        assign_levels(setup.tree, with_tree(1, start_id="424242"))
    levels = assign_levels(setup.tree, with_tree(1, start_id="424242", id_verified=False))
    assert levels.places["500"].depth == 0
    assert "424242" in levels.notes[0] and "CB-CLI (500)" in levels.notes[0]


def test_trees_must_not_overlap(setup: SetupData) -> None:
    with pytest.raises(LevelError, match="overlap"):
        assign_levels(
            setup.tree, with_tree(1, path=["TOPBANK", "DESK", "CLI", "CLI-ALPHA"], start_id=None)
        )
