from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest

from checks.setup.profile import (
    DEFAULT_OTD_KEY,
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
    assert [t.name for t in profile.trees] == ["member", "non_member"]
    member = profile.tree("member")
    assert member.start_name == "CLI" and member.max_depth == 2
    assert [lv.label for lv in member.levels_at(5)] == ["Trading account"]
    assert member.markets["92"] == "ICE LIFFE"
    assert profile.tree_of_market("64").name == "non_member"
    assert profile.tree_of_market("216") is None
    assert profile.otd_default_key == DEFAULT_OTD_KEY
    clearing = member.order_tag_defaults[0]
    assert clearing.field_by_market() == {m: "1859" for m in ["7", "2", "32", "92", "93"]}
    assert clearing.applies_to("ABCD1000ALP") and not clearing.applies_to("ABCD1000ANX")
    assert profile.rules.companion_account.market == "32"


def test_levels_fit_by_account_type_and_pattern() -> None:
    pod = load_profile(synthetic.EXAMPLE_PROFILE).tree("member").levels_at(1)[0]
    assert pod.fits(Account("1", "CLI-X", "0", 3))
    assert not pod.fits(Account("1", "LONE4000AAA", "0", 1))


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
        (["trees", 0, "levels"], [{"depth": 1, "label": "Pod"}], "depth = 0"),
        (["trees", 0, "levels", 1, "name_regex"], "([", "regular expression"),
        (["trees", 0, "levels", 1, "apply_limits"], "yes", "apply_limits"),
        (["trees", 0, "order_tag_defaults", 0, "level"], "Nope", "Nope"),
        (["trees", 0, "order_tag_defaults", 0, "value"], "X", "exactly one of"),
        (["trees", 0, "order_tag_defaults", 0, "value_from_name"], "^(.*)$", "base"),
        (["trees", 0, "order_tag_defaults", 1, "field_id"], "9", "either fields"),
        (["trees", 1, "markets"], {"7": "CME"}, "market 7"),
        (["trees", 1, "name"], "member", "different"),
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
