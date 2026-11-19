"""Clone plans: which new accounts the Clone page will create, worked out before anything is sent.

Pure logic, no TT calls. ``core/clone_run.py`` reads the source from TT and carries a
plan out through the UAT-only write guard.

A source account is cloned once per new name. When the source has accounts below it,
each of them is cloned too, top down, under its new parent. A copy's name is the
source account's name with the source's own name swapped for the new one
(cloning POD1 as POD7 turns POD1-A into POD7-A).

The pasted table has one row per new account and exchange: New account (needed),
Exchange and Clearing account override. A row's clearing value goes on the new top
account, for that exchange only; the source's other exchanges, and the accounts below
a group, get the clearing value the page's choice gives them. A row may name an
exchange the source has no Clearing Account on: the source's entry is then copied
with the exchange switched.

The review can edit the plan before anything is sent (``PlanEdits``): rename or drop
an account, change or drop a clearing value.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
COLUMNS = ("New account", "Exchange", "Clearing account override")


class ClearingMode(StrEnum):
    OFF = "off"  # the Clearing Account Order Tag Default is not handled separately
    SOURCE = "source"  # the new account gets the source account's value
    NEW_NAME = "new_name"  # the new account gets its own name


@dataclass(frozen=True)
class NewAccount:
    """A new top account from the pasted table, with its per-exchange clearing values."""

    name: str
    # (exchange as typed, clearing account override) for this account, in row order.
    overrides: tuple[tuple[str, str], ...] = ()
    # Stable id for edits made in the review ("" = the name as first pasted).
    key: str = ""

    @property
    def id(self) -> str:
        return self.key or self.name


@dataclass(frozen=True)
class NewAccounts:
    accounts: tuple[NewAccount, ...] = ()
    errors: tuple[str, ...] = ()
    header_skipped: bool = False


def parse_new_accounts(text: str) -> NewAccounts:
    """The new accounts from pasted table data or a CSV file's text.

    Columns: new account (needed), exchange and clearing account override (both
    optional, but together). An account may have several rows, one per exchange.
    Cells are split on tabs when the text has any (a paste from Excel), otherwise on
    commas, with CSV quoting. Spaces around each cell are removed, blank rows dropped,
    and a header row (``Account``, ``Name``...) skipped.
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
    order: list[str] = []
    overrides: dict[str, list[tuple[str, str]]] = {}
    names: dict[str, str] = {}
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
        name, exchange, clearing = cells[: len(COLUMNS)]
        if not name:
            errors.append(f"Row {number} has no new account in its first column.")
            continue
        if bool(exchange) != bool(clearing):
            errors.append(
                f"Row {number} ({name}) needs both an exchange and a clearing account "
                "override, or neither."
            )
            continue
        folded = name.casefold()
        if folded not in names:
            names[folded] = name
            order.append(folded)
            overrides[folded] = []
        if exchange:
            if any(e.casefold() == exchange.casefold() for e, _ in overrides[folded]):
                errors.append(f"Row {number}: {name} on {exchange} is listed more than once.")
                continue
            overrides[folded].append((exchange, clearing))
    accounts = tuple(NewAccount(names[k], tuple(overrides[k])) for k in order)
    return NewAccounts(accounts, tuple(errors), header)


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
class PlanEdits:
    """Changes made in the review. Keys: a step's ``key``; a clearing row's ``key``."""

    names: Mapping[str, str] = field(default_factory=dict)  # step key -> new name
    removed: frozenset[str] = frozenset()  # step keys (their accounts below go too)
    values: Mapping[str, str] = field(default_factory=dict)  # clearing key -> value
    dropped: frozenset[str] = frozenset()  # clearing keys (that exchange gets none)


@dataclass(frozen=True)
class CloneStep:
    number: int  # 1-based, in the order the accounts are created
    source: Account
    new_name: str
    parent_step: int | None  # the step whose new account is the parent, if any
    parent_id: str  # the existing parent when parent_step is None ("" for none)
    parent_name: str  # for display
    key: str = ""  # "<row id>|<source account id>": stable across review edits


@dataclass(frozen=True)
class ClearingChange:
    step: int
    market_id: str
    market: str
    source_value: str
    value: str
    entry: Mapping[str, Any]  # the source's Order Tag Default (exchange switched if copied)
    key: str = ""  # "<step key>|<market id>"
    copied_from: str = ""  # the source exchange whose entry was copied, for a new exchange


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
                "Source value": c.source_value
                if not c.copied_from
                else f"(none; entry copied from {c.copied_from})",
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
    edits: PlanEdits | None = None,
) -> ClonePlan:
    """Work out every account to create and every Clearing Account value to set."""
    edits = edits or PlanEdits()
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
    given = [row.name for row in rows if row.overrides]
    if given and clearing_mode is ClearingMode.OFF:
        errors.append(
            "Clearing account overrides are given for " + ", ".join(given) + ", but "
            "'Clone the clearing account override' is not ticked. Tick it, or remove "
            "the Exchange and Clearing columns."
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
    row_overrides: dict[int, tuple[tuple[str, str], ...]] = {}
    for row in rows:
        top_key = f"{row.id}|{source.id}"
        if top_key in edits.removed:
            continue
        if row.name == source.name:
            errors.append(f"{row.name} is the account being cloned.")
            continue
        made: dict[str, int] = {}  # source id -> step number in this copy
        number = len(steps) + 1
        steps.append(
            CloneStep(
                number=number,
                source=source,
                new_name=edits.names.get(top_key, row.name),
                parent_step=None,
                parent_id=parent.id if parent else "",
                parent_name=parent.display if parent else "(top level)",
                key=top_key,
            )
        )
        top_name = steps[-1].new_name
        if row.overrides:
            row_overrides[number] = row.overrides
        made[source.id] = number
        for account in below:
            key = f"{row.id}|{account.id}"
            if source.name not in account.name or key in edits.removed:
                continue
            if account.parent_id not in made:
                continue  # its parent was dropped in the review
            parent_step = made[account.parent_id]
            number = len(steps) + 1
            steps.append(
                CloneStep(
                    number=number,
                    source=account,
                    # Children follow the top account's name (also when renamed in the review).
                    new_name=edits.names.get(key, account.name.replace(source.name, top_name)),
                    parent_step=parent_step,
                    parent_id="",
                    parent_name=steps[parent_step - 1].new_name,
                    key=key,
                )
            )
            made[account.id] = number

    existing = {a.name.casefold() for a in tree.all()}
    counts = Counter(step.new_name.casefold() for step in steps)
    for step in steps:
        if not step.new_name.strip():
            errors.append(f"Step {step.number} has no account name.")
        if step.new_name.casefold() in existing:
            errors.append(f"{step.new_name} already exists in TT.")
        if counts[step.new_name.casefold()] > 1:
            errors.append(f"{step.new_name} would be created more than once.")

    clearing: list[ClearingChange] = []
    if clearing_mode is not ClearingMode.OFF:
        names = market_names or {}
        by_name = {name.casefold(): mid for mid, name in names.items() if name}
        entries = clearing_entries or {}
        for step in steps:
            found = list(entries.get(step.source.id, ()))
            overrides = row_overrides.get(step.number, ())
            wanted: dict[str, str] = {}  # market id -> the row's clearing value
            for exchange, value in overrides:
                mid = by_name.get(exchange.casefold())
                if mid is None:
                    errors.append(f"{exchange} (for {step.new_name}) is not one of TT's exchanges.")
                    continue
                wanted[mid] = value
            have = {norm_id(e.get("marketId")) for e in found}
            missing = [mid for mid in wanted if mid not in have]
            template = found[0] if found else None
            if missing and template is None:
                errors.append(
                    f"{step.source.name} has no Clearing Account set on any exchange, so "
                    f"Lookout has no entry to copy for {step.new_name}."
                )
            elif missing and _is_product_level(template):
                errors.append(
                    f"{step.source.name}'s Clearing Account entries are set per product, so "
                    f"Lookout can't copy one to another exchange for {step.new_name}."
                )
            elif template is not None:
                for mid in missing:
                    copy = {**template, "marketId": int(mid) if mid.isdigit() else mid}
                    found.append(copy)
            for entry in found:
                source_value = str(entry.get("value") or "")
                market_id = norm_id(entry.get("marketId"))
                copied = market_id not in have
                key = f"{step.key}|{market_id}"
                if key in edits.dropped:
                    continue
                if market_id in wanted:
                    value = wanted[market_id]
                elif clearing_mode is ClearingMode.SOURCE:
                    value = source_value
                else:
                    value = step.new_name
                value = edits.values.get(key, value)
                clearing.append(
                    ClearingChange(
                        step=step.number,
                        market_id=market_id,
                        market=names.get(market_id, f"market {market_id}"),
                        source_value="" if copied else source_value,
                        value=value,
                        entry=entry,
                        key=key,
                        copied_from=names.get(norm_id(template.get("marketId")), "")
                        if copied and template
                        else "",
                    )
                )
                if not value.strip():
                    errors.append(
                        f"The clearing value for {step.new_name} on "
                        f"{names.get(market_id, market_id)} is blank; set it or remove the row."
                    )

    return ClonePlan(
        source=source,
        steps=tuple(steps),
        clearing=tuple(clearing),
        clearing_mode=clearing_mode,
        errors=tuple(dict.fromkeys(errors)),
    )


def _is_product_level(entry: Mapping[str, Any] | None) -> bool:
    """An entry tied to one product or product group (not the whole exchange)."""
    if entry is None:
        return False
    return bool(norm_id(entry.get("exchangeProductGroupId"))) or str(
        entry.get("productId") or "0"
    ).strip() not in ("", "0")
