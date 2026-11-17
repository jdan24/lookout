"""Clone plans: which new accounts the Clone page will create, worked out before anything is sent.

Pure logic, no TT calls. ``core/clone_run.py`` reads the source from TT and carries a
plan out through the UAT-only write guard.

A source account is cloned once per new name. When the source has accounts below it,
each of them is cloned too, top down, under its new parent. A copy's name is the
source account's name with the source's own name swapped for the new one
(cloning POD1 as POD7 turns POD1-A into POD7-A).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.hierarchy import Account, AccountTree
from core.normalize import norm_id

# TT's on/off switches on the clone call that the page offers, with their labels.
CLONE_FLAGS: dict[str, str] = {
    "orderTagDefaults": "All Order Tag Defaults",
    "algoSettings": "Algo settings",
    "exchangeSettings": "Exchange settings",
    "creditLimitsAndSod": "Credit limits and SOD",
    "counterparties": "Counterparties",
    "assignNewAccountsToSameConnections": "Same connections",
    "fixSessions": "FIX sessions",
}
# Switches the page does not offer: always sent as off.
ALWAYS_OFF: tuple[str, ...] = ("limits", "assignNewAccountsToSameUsers")
CLEARING_FIELD_NAME = "Clearing Account"


class ClearingMode(StrEnum):
    OFF = "off"  # the Clearing Account Order Tag Default is not handled separately
    SOURCE = "source"  # the new account gets the source account's value
    NEW_NAME = "new_name"  # the new account gets its own name


def clean_names(text: str) -> list[str]:
    """One name per line, with spaces and tabs around it removed and blank lines dropped."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def clone_params(flags: Mapping[str, bool]) -> dict[str, str]:
    """The clone call's query switches: every offered one, plus the ones always off."""
    params = {name: "true" if flags.get(name) else "false" for name in CLONE_FLAGS}
    params.update({name: "false" for name in ALWAYS_OFF})
    return params


def is_default_entry(entry: Mapping[str, Any]) -> bool:
    """An account-wide Order Tag Default: not tied to a user."""
    return not norm_id(entry.get("userId"))


@dataclass(frozen=True)
class CloneStep:
    number: int  # 1-based, in the order the accounts are created
    source: Account
    new_name: str
    parent_step: int | None  # the step whose new account is the parent, if any
    parent_id: str  # the existing parent when parent_step is None ("" for none)
    parent_name: str  # for display


@dataclass(frozen=True)
class ClearingChange:
    step: int
    market_id: str
    market: str
    source_value: str
    value: str
    entry: Mapping[str, Any]  # the source account's Order Tag Default


@dataclass(frozen=True)
class ClonePlan:
    source: Account | None
    steps: tuple[CloneStep, ...] = ()
    clearing: tuple[ClearingChange, ...] = ()
    flags: Mapping[str, bool] = field(default_factory=dict)
    clearing_mode: ClearingMode = ClearingMode.OFF
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.steps)

    def step(self, number: int) -> CloneStep:
        return self.steps[number - 1]

    def rows(self) -> list[dict[str, str]]:
        """The accounts to create, for the review table."""
        return [
            {
                "Step": str(s.number),
                "New account": s.new_name,
                "Under": s.parent_name + (f" (new, step {s.parent_step})" if s.parent_step else ""),
                "Copied from": s.source.display,
            }
            for s in self.steps
        ]

    def clearing_rows(self) -> list[dict[str, str]]:
        return [
            {
                "Step": str(c.step),
                "New account": self.step(c.step).new_name,
                "Exchange": c.market,
                "Source value": c.source_value,
                "New value": c.value,
            }
            for c in self.clearing
        ]


def find_source(tree: AccountTree, text: str) -> tuple[Account | None, str]:
    """The account named ``text`` (or with that id), or None and why not."""
    wanted = text.strip()
    if not wanted:
        return None, "Enter the account or group to clone."
    found = tree.find_by_name(wanted)
    if not found and wanted.isdigit() and wanted in tree:
        found = [tree.get(wanted)]
    if not found:
        return None, f"No account named {wanted} was found in TT."
    if len(found) > 1:
        ids = ", ".join(a.id for a in found)
        return None, f"More than one account is named {wanted} (ids {ids}); enter the id instead."
    return found[0], ""


def build_plan(
    tree: AccountTree,
    source_text: str,
    new_names: Sequence[str],
    *,
    flags: Mapping[str, bool] | None = None,
    clearing_mode: ClearingMode = ClearingMode.OFF,
    clearing_entries: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    market_names: Mapping[str, str] | None = None,
) -> ClonePlan:
    """Work out every account to create and every Clearing Account value to set."""
    flags = dict(flags or {})
    source, problem = find_source(tree, source_text)
    if source is None:
        return ClonePlan(None, flags=flags, clearing_mode=clearing_mode, errors=(problem,))

    errors: list[str] = []
    if not new_names:
        errors.append("Paste at least one new account name.")
    seen: set[str] = set()
    for name in new_names:
        if name.casefold() in seen:
            errors.append(f"{name} is listed more than once.")
        seen.add(name.casefold())

    below = tree.descendants(source.id)
    for account in below:
        if source.name not in account.name:
            errors.append(
                f"{account.name} is below {source.name}, but its name does not contain "
                f"{source.name}, so Lookout cannot name its copy."
            )

    parent = tree.parent(source.id)
    steps: list[CloneStep] = []
    for new_name in dict.fromkeys(new_names):
        if new_name == source.name:
            errors.append(f"{new_name} is the account being cloned.")
            continue
        made: dict[str, int] = {}  # source id -> step number in this copy
        number = len(steps) + 1
        steps.append(
            CloneStep(
                number=number,
                source=source,
                new_name=new_name,
                parent_step=None,
                parent_id=parent.id if parent else "",
                parent_name=parent.display if parent else "(top level)",
            )
        )
        made[source.id] = number
        for account in below:
            if source.name not in account.name:
                continue
            parent_step = made[account.parent_id]
            number = len(steps) + 1
            steps.append(
                CloneStep(
                    number=number,
                    source=account,
                    new_name=account.name.replace(source.name, new_name),
                    parent_step=parent_step,
                    parent_id="",
                    parent_name=steps[parent_step - 1].new_name,
                )
            )
            made[account.id] = number

    existing = {a.name.casefold() for a in tree.all()}
    counts = Counter(step.new_name.casefold() for step in steps)
    for step in steps:
        if step.new_name.casefold() in existing:
            errors.append(f"{step.new_name} already exists in TT.")
        if counts[step.new_name.casefold()] > 1:
            errors.append(f"{step.new_name} would be created more than once.")

    clearing: list[ClearingChange] = []
    if clearing_mode is not ClearingMode.OFF:
        names = market_names or {}
        entries = clearing_entries or {}
        for step in steps:
            for entry in entries.get(step.source.id, ()):
                source_value = str(entry.get("value") or "")
                market_id = norm_id(entry.get("marketId"))
                clearing.append(
                    ClearingChange(
                        step=step.number,
                        market_id=market_id,
                        market=names.get(market_id, f"market {market_id}"),
                        source_value=source_value,
                        value=source_value
                        if clearing_mode is ClearingMode.SOURCE
                        else step.new_name,
                        entry=entry,
                    )
                )

    return ClonePlan(
        source=source,
        steps=tuple(steps),
        clearing=tuple(clearing),
        flags=flags,
        clearing_mode=clearing_mode,
        errors=tuple(dict.fromkeys(errors)),
    )
