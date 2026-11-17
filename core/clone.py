"""Clone plans: which new accounts the Clone page will create, worked out before anything is sent.

Pure logic, no TT calls. ``core/clone_run.py`` reads the source from TT and carries a
plan out through the UAT-only write guard.

A source account is cloned once per new name. When the source has accounts below it,
each of them is cloned too, top down, under its new parent. A copy's name is the
source account's name with the source's own name swapped for the new one
(cloning POD1 as POD7 turns POD1-A into POD7-A).

A row's description and clearing value go on the new top account only; the accounts
below it get the clearing value the page's choice gives them, and no description.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.hierarchy import Account, AccountTree
from core.normalize import norm_id

# TT's on/off switches on the clone call. All are sent as off: TT copies none of the
# source's other settings, and Lookout sets only the Clearing Account itself.
CLONE_SWITCHES: tuple[str, ...] = (
    "orderTagDefaults", "algoSettings", "exchangeSettings", "creditLimitsAndSod",
    "counterparties", "assignNewAccountsToSameConnections", "fixSessions", "limits",
    "assignNewAccountsToSameUsers",
)  # fmt: skip
CLEARING_FIELD_NAME = "Clearing Account"
# A first row whose first cell is one of these is a header and is skipped.
HEADER_WORDS = frozenset(
    {"account", "accountname", "accountnumber", "accountno", "name", "newaccount",
     "newaccountname", "newaccountnumber", "newname"}
)  # fmt: skip
COLUMNS = ("New account", "Description", "Clearing account override")


class ClearingMode(StrEnum):
    OFF = "off"  # the Clearing Account Order Tag Default is not handled separately
    SOURCE = "source"  # the new account gets the source account's value
    NEW_NAME = "new_name"  # the new account gets its own name


@dataclass(frozen=True)
class NewAccount:
    """One row of the pasted table or CSV file."""

    name: str
    description: str = ""
    clearing: str = ""  # this row's clearing account override; "" uses the page's choice


@dataclass(frozen=True)
class NewAccounts:
    accounts: tuple[NewAccount, ...] = ()
    errors: tuple[str, ...] = ()
    header_skipped: bool = False


def parse_new_accounts(text: str) -> NewAccounts:
    """The new accounts from pasted table data or a CSV file's text.

    Columns: new account (needed), description, clearing account override (both
    optional). Cells are split on tabs when the text has any (a paste from Excel),
    otherwise on commas, with CSV quoting. Spaces around each cell are removed, blank
    rows dropped, and a header row (``Account``, ``Name``...) skipped.
    """
    text = text.lstrip("\ufeff")
    delimiter = "\t" if "\t" in text else ","
    rows = [
        [cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ]
    numbered = [(n, row) for n, row in enumerate(rows, start=1) if any(row)]
    header = bool(numbered) and _is_header(numbered[0][1])
    if header:
        numbered = numbered[1:]
    accounts: list[NewAccount] = []
    errors: list[str] = []
    for number, row in numbered:
        cells = row + [""] * (len(COLUMNS) - len(row))
        if any(cells[len(COLUMNS) :]):
            errors.append(
                f"Row {number} has more than {len(COLUMNS)} columns; expected: "
                + ", ".join(COLUMNS)
                + "."
            )
            continue
        name, description, clearing = cells[: len(COLUMNS)]
        if not name:
            errors.append(f"Row {number} has no new account in its first column.")
            continue
        accounts.append(NewAccount(name, description, clearing))
    return NewAccounts(tuple(accounts), tuple(errors), header)


def _is_header(row: Sequence[str]) -> bool:
    first = "".join(ch for ch in row[0].casefold() if ch.isalnum()) if row else ""
    return first in HEADER_WORDS


def clone_params() -> dict[str, str]:
    """The clone call's query switches, every one off."""
    return {name: "false" for name in CLONE_SWITCHES}


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
    description: str = ""  # sent with the clone; only the row's own (top) account has one


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
                "Description": s.description,
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
    new_accounts: Sequence[NewAccount],
    *,
    clearing_mode: ClearingMode = ClearingMode.OFF,
    clearing_entries: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    market_names: Mapping[str, str] | None = None,
) -> ClonePlan:
    """Work out every account to create and every Clearing Account value to set."""
    source, problem = find_source(tree, source_text)
    if source is None:
        return ClonePlan(None, clearing_mode=clearing_mode, errors=(problem,))

    errors: list[str] = []
    if not new_accounts:
        errors.append("Paste or upload at least one new account.")
    seen: set[str] = set()
    rows: list[NewAccount] = []
    for row in new_accounts:
        if row.name.casefold() in seen:
            errors.append(f"{row.name} is listed more than once.")
            continue
        seen.add(row.name.casefold())
        rows.append(row)
    given = [row.name for row in rows if row.clearing]
    if given and clearing_mode is ClearingMode.OFF:
        errors.append(
            "Clearing account overrides are given for " + ", ".join(given) + ", but "
            "'Clone the clearing account override' is not ticked. Tick it, or remove "
            "the third column."
        )

    below = tree.descendants(source.id)
    for account in below:
        if source.name not in account.name:
            errors.append(
                f"{account.name} is below {source.name}, but its name does not contain "
                f"{source.name}, so Lookout cannot name its copy."
            )

    parent = tree.parent(source.id)
    steps: list[CloneStep] = []
    row_clearing: dict[int, str] = {}  # step number -> the row's own clearing value
    for row in rows:
        if row.name == source.name:
            errors.append(f"{row.name} is the account being cloned.")
            continue
        made: dict[str, int] = {}  # source id -> step number in this copy
        number = len(steps) + 1
        steps.append(
            CloneStep(
                number=number,
                source=source,
                new_name=row.name,
                parent_step=None,
                parent_id=parent.id if parent else "",
                parent_name=parent.display if parent else "(top level)",
                description=row.description,
            )
        )
        if row.clearing:
            row_clearing[number] = row.clearing
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
                    new_name=account.name.replace(source.name, row.name),
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
            found = entries.get(step.source.id, ())
            if step.number in row_clearing and not found:
                errors.append(
                    f"{step.source.name} has no Clearing Account set, so Lookout does not "
                    f"know which exchanges to set {row_clearing[step.number]} on for "
                    f"{step.new_name}."
                )
            for entry in found:
                source_value = str(entry.get("value") or "")
                market_id = norm_id(entry.get("marketId"))
                if step.number in row_clearing:
                    value = row_clearing[step.number]
                elif clearing_mode is ClearingMode.SOURCE:
                    value = source_value
                else:
                    value = step.new_name
                clearing.append(
                    ClearingChange(
                        step=step.number,
                        market_id=market_id,
                        market=names.get(market_id, f"market {market_id}"),
                        source_value=source_value,
                        value=value,
                        entry=entry,
                    )
                )

    return ClonePlan(
        source=source,
        steps=tuple(steps),
        clearing=tuple(clearing),
        clearing_mode=clearing_mode,
        errors=tuple(dict.fromkeys(errors)),
    )
