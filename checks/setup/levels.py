"""Levels: where each account sits in a client's trees, worked out from its profile.

The start account of each tree is found by walking ``path`` from a top-level account.
Every account below it gets the profile level for its depth (the first one that fits),
or none if no level fits. Every rule works from this map.
"""

from __future__ import annotations

from dataclasses import dataclass

from checks.setup.profile import Level, Profile, Tree
from core.hierarchy import Account, AccountTree

SUB_ACCOUNT = "Sub-account"


class LevelError(Exception):
    """The snapshot does not contain the accounts the profile describes."""


@dataclass(frozen=True)
class Place:
    account: Account
    tree: Tree
    root: Account  # the tree's start account
    depth: int  # 0 = the start account
    level: Level | None  # None: no configured level fits this account
    label: str  # shown in results, e.g. "Client group (FUND)", "Team", "Sub-account"
    leaf: bool  # nothing below it

    @property
    def empty(self) -> bool:
        """A new account at a level allowed to be empty, with nothing below it yet."""
        return self.leaf and self.level is not None and self.level.may_be_empty and self.depth > 0


@dataclass(frozen=True)
class LevelMap:
    places: dict[str, Place]  # in-scope accounts by id, tree order
    path_ids: frozenset[str]  # accounts above the start accounts
    notes: tuple[str, ...] = ()

    def in_scope(self) -> list[Place]:
        return list(self.places.values())

    def fitted(self) -> list[Place]:
        return [p for p in self.places.values() if p.level is not None]

    def at(self, label: str) -> list[Place]:
        return [p for p in self.fitted() if p.level is not None and p.level.label == label]

    def children(self, place: Place) -> list[Place]:
        return [p for p in self.places.values() if p.account.parent_id == place.account.id]

    def chain(self, place: Place) -> list[Place]:
        """The place and every place above it, up to its start account."""
        found = [place]
        current = self.places.get(place.account.parent_id)
        while current is not None and current.tree.name == place.tree.name:
            found.append(current)
            current = self.places.get(current.account.parent_id)
        return found

    def roots(self) -> list[Place]:
        return [p for p in self.places.values() if p.depth == 0]


def level_label(level: Level, depth: int, root: Account) -> str:
    if depth == 0:
        return f"{level.label} ({root.name})"
    if depth > level.depth:
        return SUB_ACCOUNT
    return level.label


def resolve_start(tree: AccountTree, spec: Tree) -> tuple[Account, list[Account], str]:
    """Walk ``path`` by name. Returns the start account, the accounts above it, and a note."""
    first = spec.path[0]
    matches = tree.find_by_name(first)
    if len(matches) != 1:
        raise LevelError(
            f"Tree {spec.name!r}: expected exactly one account named {first!r}, "
            f"found {len(matches)}."
        )
    current = matches[0]
    above: list[Account] = []
    for name in spec.path[1:]:
        below = [a for a in tree.children(current.id) if a.name == name]
        if len(below) != 1:
            raise LevelError(
                f"Tree {spec.name!r}: expected exactly one account named {name!r} under "
                f"{current.display}, found {len(below)}."
            )
        above.append(current)
        current = below[0]

    note = ""
    if spec.start_id and spec.start_id != current.id:
        message = (
            f"Tree {spec.name!r}: the profile gives id {spec.start_id} for {spec.start_name!r}, "
            f"but TT has {current.display}."
        )
        if spec.id_verified:
            raise LevelError(message)
        note = message + " Using the id found in TT; correct start_id in the profile."
    return current, above, note


def assign_levels(tree: AccountTree, profile: Profile) -> LevelMap:
    places: dict[str, Place] = {}
    path_ids: set[str] = set()
    notes: list[str] = []
    for spec in profile.trees:
        root, above, note = resolve_start(tree, spec)
        if note:
            notes.append(note)
        path_ids.update(a.id for a in above)
        for account in tree.subtree(root.id):
            if account.id in places:
                raise LevelError(
                    f"{account.display} is in tree {places[account.id].tree.name!r} and "
                    f"{spec.name!r}. Trees must not overlap."
                )
            depth = tree.depth_below(root.id, account.id) or 0
            level = next((lv for lv in spec.levels_at(depth) if lv.fits(account)), None)
            label = level_label(level, depth, root) if level else f"Depth {depth}"
            places[account.id] = Place(
                account=account,
                tree=spec,
                root=root,
                depth=depth,
                level=level,
                label=label,
                leaf=tree.is_leaf(account.id),
            )
    return LevelMap(places=places, path_ids=frozenset(path_ids), notes=tuple(notes))
