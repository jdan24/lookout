"""The TT account tree, built from the lightweight accounts list. Generic: no client names."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from core.normalize import NO_ID, norm_id

GLOBAL_KEY = "global"
_KEY_PREFIXES = {"acct", "rg", "user"}


def account_key(account_id: Any) -> str:
    return _key("acct", account_id)


def risk_group_key(risk_group_id: Any) -> str:
    return _key("rg", risk_group_id)


def user_key(user_id: Any) -> str:
    return _key("user", user_id)


def _key(prefix: str, raw_id: Any) -> str:
    value = norm_id(raw_id)
    if value == NO_ID:
        raise ValueError(f"Cannot build a {prefix} node key without an id.")
    return f"{prefix}:{value}"


def parse_node_key(key: str) -> tuple[str, str]:
    """Split a node key into (kind, id). ``global`` has no id."""
    if key == GLOBAL_KEY:
        return GLOBAL_KEY, NO_ID
    prefix, _, value = key.partition(":")
    if prefix not in _KEY_PREFIXES or not value:
        raise ValueError(f"Not a node key: {key!r}")
    return prefix, value


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    parent_id: str  # "" for a top-level account
    account_type: int | None

    @property
    def key(self) -> str:
        return account_key(self.id)

    @property
    def display(self) -> str:
        return f"{self.name} ({self.id})"


def _parent_of(item: Mapping[str, Any]) -> str:
    # The list carries both fields; the detail record carries only parentAccountId.
    return norm_id(item.get("parentAccountId")) or norm_id(item.get("parentId"))


def _account_type(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class HierarchyError(Exception):
    """The accounts list does not describe a usable tree."""


class AccountTree:
    """Accounts indexed by id, with parent and child lookups."""

    def __init__(self, accounts: Iterable[Mapping[str, Any]]) -> None:
        self._accounts: dict[str, Account] = {}
        for item in accounts:
            account_id = norm_id(item.get("id"))
            if account_id == NO_ID:
                raise HierarchyError(f"Account without an id: {dict(item)!r}")
            if account_id in self._accounts:
                raise HierarchyError(f"Account id appears twice: {account_id}")
            self._accounts[account_id] = Account(
                id=account_id,
                name=str(item.get("name") or ""),
                parent_id=_parent_of(item),
                account_type=_account_type(item.get("accountType")),
            )

        self._children: dict[str, list[str]] = {account_id: [] for account_id in self._accounts}
        for account in self._accounts.values():
            if account.parent_id in self._children:
                self._children[account.parent_id].append(account.id)
        for child_ids in self._children.values():
            child_ids.sort(key=lambda cid: (self._accounts[cid].name, cid))
        self._check_no_cycles()

    def _check_no_cycles(self) -> None:
        for account_id in self._accounts:
            seen = {account_id}
            current = self._accounts[account_id].parent_id
            while current in self._accounts:
                if current in seen:
                    raise HierarchyError(f"Parent loop in the accounts list at {current}")
                seen.add(current)
                current = self._accounts[current].parent_id

    def __len__(self) -> int:
        return len(self._accounts)

    def __contains__(self, account_id: Any) -> bool:
        return norm_id(account_id) in self._accounts

    def get(self, account_id: Any) -> Account:
        try:
            return self._accounts[norm_id(account_id)]
        except KeyError:
            raise HierarchyError(f"Account {account_id!r} is not in the accounts list") from None

    def find_by_name(self, name: str) -> list[Account]:
        return [a for a in self._accounts.values() if a.name == name]

    def all(self) -> list[Account]:
        return list(self._accounts.values())

    def roots(self) -> list[Account]:
        """Accounts whose parent is absent from the list (including true top-level ones)."""
        tops = [a for a in self._accounts.values() if a.parent_id not in self._accounts]
        return sorted(tops, key=lambda a: (a.name, a.id))

    def parent(self, account_id: Any) -> Account | None:
        return self._accounts.get(self.get(account_id).parent_id)

    def children(self, account_id: Any) -> list[Account]:
        return [self._accounts[cid] for cid in self._children[self.get(account_id).id]]

    def is_leaf(self, account_id: Any) -> bool:
        return not self._children[self.get(account_id).id]

    def ancestors(self, account_id: Any) -> list[Account]:
        """Nearest first: parent, grandparent, ... up to the top."""
        chain: list[Account] = []
        current = self.parent(account_id)
        while current is not None:
            chain.append(current)
            current = self._accounts.get(current.parent_id)
        return chain

    def descendants(self, account_id: Any) -> list[Account]:
        """Everything below the account, parents before children, siblings by name."""
        found: list[Account] = []
        stack = list(reversed(self.children(account_id)))
        while stack:
            account = stack.pop()
            found.append(account)
            stack.extend(reversed(self.children(account.id)))
        return found

    def subtree(self, root_id: Any) -> list[Account]:
        """The root followed by all its descendants."""
        return [self.get(root_id), *self.descendants(root_id)]

    def depth_below(self, root_id: Any, account_id: Any) -> int | None:
        """0 for the root, 1 for its children, ...; ``None`` if not under the root."""
        root = self.get(root_id)
        target = self.get(account_id)
        if target.id == root.id:
            return 0
        for steps, ancestor in enumerate(self.ancestors(target.id), start=1):
            if ancestor.id == root.id:
                return steps
        return None

    def is_under(self, root_id: Any, account_id: Any) -> bool:
        depth = self.depth_below(root_id, account_id)
        return depth is not None and depth > 0
