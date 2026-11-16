from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest

from checks.setup.profile import (
    DEFAULT_OTD_KEY,
    Profile,
    ProfileError,
    list_profiles,
    load_profile,
    parse_profile,
)
from core.hierarchy import Account
from tests.fixtures import synthetic


def example() -> dict[str, Any]:
    with synthetic.EXAMPLE_PROFILE.open("rb") as handle:
        return tomllib.load(handle)


def test_example_profile() -> None:
    profile = load_profile(synthetic.EXAMPLE_PROFILE)
    assert profile.client == "example" and profile.title == "Example client"
    assert [t.name for t in profile.trees] == ["north", "south"]
    north = profile.tree("north")
    assert north.start_name == "FUND" and north.max_depth == 2
    assert [lv.label for lv in north.levels_at(5)] == ["Trading account"]
    assert north.markets["107"] == "HKEX"
    assert profile.tree_of_market("66").name == "south"
    assert profile.tree_of_market("216") is None
    assert profile.otd_default_key == DEFAULT_OTD_KEY
    clearing = north.order_tag_defaults[0]
    assert clearing.field_by_market() == {m: "1859" for m in ["7", "2", "30", "107", "91"]}
    assert clearing.applies_to("ABC10001AL") and not clearing.applies_to("ABC10001IX")
    assert profile.rules.companion_account.market == "30"


def test_levels_fit_by_account_type_and_pattern() -> None:
    team = load_profile(synthetic.EXAMPLE_PROFILE).tree("north").levels_at(1)[0]
    assert team.fits(Account("1", "FUND-X", "0", 3))
    assert not team.fits(Account("1", "LON40004AA", "0", 1))


def _with(path: list[Any], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(example())
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (["client"], "../x", "client"),
        (["trees", 0, "levels"], [{"depth": 1, "label": "Team"}], "depth = 0"),
        (["trees", 0, "levels", 1, "name_regex"], "([", "regular expression"),
        (["trees", 0, "levels", 1, "apply_limits"], "yes", "apply_limits"),
        (["trees", 0, "order_tag_defaults", 0, "level"], "Nope", "Nope"),
        (["trees", 0, "order_tag_defaults", 0, "value"], "X", "exactly one of"),
        (["trees", 0, "order_tag_defaults", 0, "value_from_name"], "^(.*)$", "base"),
        (["trees", 0, "order_tag_defaults", 1, "field_id"], "9", "either fields"),
        (["trees", 1, "markets"], {"7": "CME"}, "market 7"),
        (["trees", 1, "name"], "north", "different"),
        (["trees", 0, "colour"], "blue", "colour"),
    ],
)
def test_bad_profiles_give_readable_errors(path: list[Any], value: Any, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        parse_profile(_with(path, value), "bad.toml")


def test_list_profiles_keeps_broken_files_with_their_error(tmp_path: Path) -> None:
    (tmp_path / "good.toml").write_bytes(synthetic.EXAMPLE_PROFILE.read_bytes())
    (tmp_path / "broken.toml").write_text("client = [", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    found = {f.path.name: f for f in list_profiles(tmp_path)}
    assert set(found) == {"good.toml", "broken.toml"}
    assert found["good.toml"].profile is not None
    assert found["broken.toml"].profile is None and "not valid TOML" in found["broken.toml"].error
    assert list_profiles(tmp_path / "missing") == []
    with pytest.raises(ProfileError, match="No profile file"):
        load_profile(tmp_path / "missing.toml")


def test_required_users_are_ids_and_the_old_users_key_still_loads() -> None:
    data = example()
    data["trees"][0]["required_users"] = [9001, "9002"]
    data["trees"][0]["levels"][0]["users"] = "here"  # from profiles written for the old rule
    tree = Profile.model_validate(data).trees[0]
    assert tree.required_users == ["9001", "9002"]
    assert Profile.model_validate(example()).trees[1].required_users == ["9001"]


@pytest.mark.parametrize("users", [["9001", 9001], [0], [""]])
def test_required_users_must_be_different_real_ids(users: list[Any]) -> None:
    data = example()
    data["trees"][0]["required_users"] = users
    with pytest.raises(ValueError, match="required_users"):
        Profile.model_validate(data)
