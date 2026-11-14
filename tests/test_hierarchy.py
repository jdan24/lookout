from __future__ import annotations

from typing import Any

import pytest

from core.hierarchy import (
    AccountTree,
    HierarchyError,
    account_key,
    parse_node_key,
    risk_group_key,
    user_key,
)


def acct(id_: Any, name: str, parent: Any = 0, type_: Any = 3, **extra: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "parentId": parent,
        "parentAccountId": parent,
        "accountType": type_,
        "companyId": 999,
        "revision": 1,
        **extra,
    }


# A fictional company with one client tree and one unrelated tree.
ACCOUNTS = [
    acct(10, "BANK"),
    acct(11, "DESK", 10),
    acct(100, "CLI", 11),
    acct(210, "CLI-BETA", 100),
    acct(200, "CLI-ALPHA", 100),
    acct(301, "ABCD1001ALP", 200, 1),
    acct(300, "ABCD1000ALP", 200, 1),
    acct(310, "ABCD2000BET", 210, 1),
    acct(311, "ABCD2000SUB", 310, 1),
    acct(220, "CLI-EMPTY", 100),
    acct(50, "OTHERCO"),
    acct(51, "OTHER-ACCT", 50, 1),
]


@pytest.fixture
def tree() -> AccountTree:
    return AccountTree(ACCOUNTS)


def names(accounts: list) -> list[str]:
    return [a.name for a in accounts]


def test_node_keys() -> None:
    assert account_key(7654321) == "acct:7654321"
    assert account_key("7654321") == "acct:7654321"
    assert risk_group_key(7) == "rg:7"
    assert user_key("12345") == "user:12345"
    assert parse_node_key("acct:7654321") == ("acct", "7654321")
    assert parse_node_key("global") == ("global", "")


@pytest.mark.parametrize("bad", [0, "", None, "0"])
def test_node_key_needs_an_id(bad: Any) -> None:
    with pytest.raises(ValueError):
        account_key(bad)


@pytest.mark.parametrize("bad", ["acct:", "acct", "thing:1", "", ":1"])
def test_parse_rejects_bad_keys(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_node_key(bad)


def test_accounts_are_indexed_by_string_id(tree: AccountTree) -> None:
    assert len(tree) == 12
    assert 100 in tree and "100" in tree and 999 not in tree
    cli = tree.get(100)
    assert (cli.id, cli.name, cli.parent_id, cli.account_type) == ("100", "CLI", "11", 3)
    assert cli.key == "acct:100"
    assert cli.display == "CLI (100)"
    assert tree.get("300").account_type == 1
    with pytest.raises(HierarchyError):
        tree.get(999)


def test_roots_parents_children(tree: AccountTree) -> None:
    assert names(tree.roots()) == ["BANK", "OTHERCO"]
    assert tree.parent(100).name == "DESK"
    assert tree.parent(10) is None
    assert names(tree.children(100)) == ["CLI-ALPHA", "CLI-BETA", "CLI-EMPTY"]
    assert names(tree.children(200)) == ["ABCD1000ALP", "ABCD1001ALP"]
    assert tree.is_leaf(300) and tree.is_leaf(220)
    assert not tree.is_leaf(200)


def test_ancestors_and_descendants(tree: AccountTree) -> None:
    assert names(tree.ancestors(311)) == ["ABCD2000BET", "CLI-BETA", "CLI", "DESK", "BANK"]
    assert tree.ancestors(10) == []
    assert names(tree.descendants(100)) == [
        "CLI-ALPHA",
        "ABCD1000ALP",
        "ABCD1001ALP",
        "CLI-BETA",
        "ABCD2000BET",
        "ABCD2000SUB",
        "CLI-EMPTY",
    ]
    assert names(tree.subtree(210)) == ["CLI-BETA", "ABCD2000BET", "ABCD2000SUB"]
    assert tree.descendants(300) == []


def test_subtree_never_includes_other_trees(tree: AccountTree) -> None:
    in_scope = {a.id for a in tree.subtree(100)}
    assert "51" not in in_scope and "50" not in in_scope
    assert "10" not in in_scope and "11" not in in_scope


def test_depth_and_labels(tree: AccountTree) -> None:
    assert tree.depth_below(100, 100) == 0
    assert tree.depth_below(100, 200) == 1
    assert tree.depth_below(100, 300) == 2
    assert tree.depth_below(100, 311) == 3
    assert tree.depth_below(100, 11) is None
    assert tree.depth_below(100, 51) is None
    assert tree.is_under(100, 300)
    assert not tree.is_under(100, 100)
    assert not tree.is_under(100, 51)


def test_find_by_name(tree: AccountTree) -> None:
    assert [a.id for a in tree.find_by_name("CLI")] == ["100"]
    assert tree.find_by_name("NOPE") == []


def test_parent_falls_back_to_parent_id_when_parent_account_id_is_empty() -> None:
    tree = AccountTree(
        [
            {"id": 1, "name": "TOP", "parentId": 0, "parentAccountId": 0, "accountType": 3},
            {"id": 2, "name": "A", "parentId": 1, "parentAccountId": 0, "accountType": 1},
            {"id": 3, "name": "B", "parentId": "", "parentAccountId": "1", "accountType": "1"},
            {"id": 4, "name": "C", "parentAccountId": 1},
        ]
    )
    assert names(tree.children(1)) == ["A", "B", "C"]
    assert tree.get(3).account_type == 1
    assert tree.get(4).account_type is None


def test_account_with_parent_outside_the_list_is_a_root() -> None:
    tree = AccountTree([acct(2, "ORPHAN", 777), acct(3, "CHILD", 2)])
    assert names(tree.roots()) == ["ORPHAN"]
    assert tree.parent(2) is None
    assert names(tree.ancestors(3)) == ["ORPHAN"]


def test_long_ids_are_kept_exact() -> None:
    big = "7193248756120394857"
    tree = AccountTree([acct(big, "BIG"), acct(5, "KID", big)])
    assert tree.get(big).id == big
    assert tree.parent(5).id == big


def test_bad_lists_are_rejected() -> None:
    with pytest.raises(HierarchyError):
        AccountTree([acct(1, "A"), acct(1, "A-again")])
    with pytest.raises(HierarchyError):
        AccountTree([acct(0, "NO-ID")])
    with pytest.raises(HierarchyError):
        AccountTree([acct(1, "A", 2), acct(2, "B", 1)])
    with pytest.raises(HierarchyError):
        AccountTree([acct(1, "SELF", 1)])


def test_empty_list() -> None:
    tree = AccountTree([])
    assert len(tree) == 0
    assert tree.roots() == []
