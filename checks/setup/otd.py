"""Order Tag Default rules: the values on an account's "Exchanges" tab.

Only "default" entries are judged: those whose key fields are all at their default
(any profile, any user, any algo, any product). Other entries are information only.
Values are looked up from the account itself up through its tree to the start
account, then the company default (account 0).

A market is only checked where it is in use: when the account that holds the limits
(the nearest level with ``limit_rows_required``) has no limit on that market, the
default is not needed there and the row is grey, "not checked".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from checks.setup.levels import LevelMap, Place
from checks.setup.profile import OrderTagDefaultRule, Profile, Tree
from core.normalize import NO_ID, norm_id
from core.results import Colour, Reason, ResultRow
from core.setup_model import SetupData

COMPANY_LEVEL = "Company default"


@dataclass(frozen=True)
class Found:
    value: str
    holder_id: str  # account id the entry sits on; "" for the company default
    level: str  # label of that account


class OrderTagDefaultIndex:
    """Default entries indexed by (account, market, field)."""

    def __init__(self, setup: SetupData, default_key: dict[str, str]) -> None:
        self.reached_end = setup.otd_reached_end
        self._fields = setup.otd_fields
        key_values = {k: norm_id(v) for k, v in default_key.items()}
        self._entries: dict[tuple[str, str, str], dict[str, Any]] = {}
        for entry in setup.otd_entries:
            if any(norm_id(entry.get(key)) != want for key, want in key_values.items()):
                continue
            key = (
                norm_id(entry.get("accountId")),
                norm_id(entry.get("marketId")),
                norm_id(entry.get("fieldId")),
            )
            self._entries.setdefault(key, entry)

    def _value(self, entry: dict[str, Any]) -> str:
        text = entry.get("value")
        if text not in (None, ""):
            return str(text)
        enum_id = norm_id(entry.get("valueEnumId"))
        if enum_id == NO_ID:
            return ""
        field = self._fields.get(norm_id(entry.get("fieldId")), {})
        for option in field.get("valueEnums") or []:
            if norm_id(option.get("valueEnumId")) == enum_id:
                return str(option.get("valueEnumName") or "")
        return f"enum {enum_id}"

    def on_account(self, account_id: str, market_id: str, field_id: str) -> str | None:
        """The value set on this account itself, or None if nothing (or a blank) is set."""
        entry = self._entries.get((norm_id(account_id), norm_id(market_id), norm_id(field_id)))
        if entry is None:
            return None
        return self._value(entry) or None

    def resolve(
        self, place: Place, levels: LevelMap, market_id: str, field_id: str
    ) -> Found | None:
        """First value found going up: the account, the accounts above it, company default."""
        chain = [(p.account.id, p.label) for p in levels.chain(place)]
        chain.append((NO_ID, COMPANY_LEVEL))
        for holder_id, level in chain:
            value = self.on_account(holder_id, market_id, field_id)
            if value is not None:
                return Found(value=value, holder_id=holder_id, level=level)
        return None


def _missing(index: OrderTagDefaultIndex) -> tuple[Colour, Reason, str]:
    if index.reached_end:
        return Colour.RED, Reason.MISSING, ""
    return (
        Colour.AMBER,
        Reason.UNKNOWN,
        " The Order Tag Defaults list was not read to the end, so it may exist. Pull again.",
    )


def _holder_name(found: Found, setup: SetupData) -> str:
    return setup.tree.get(found.holder_id).name if found.holder_id else "the company default"


def _inherit_text(tree: Tree, level: str) -> str:
    """e.g. "Trading account, Team or Client group": the level and every level above it."""
    depth = next(lv.depth for lv in tree.levels if lv.label == level)
    labels: list[str] = []
    for lv in sorted(tree.levels, key=lambda lv: -lv.depth):
        if lv.depth <= depth and lv.label not in labels:
            labels.append(lv.label)
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]


def check_order_tag_defaults(
    setup: SetupData, levels: LevelMap, profile: Profile
) -> list[ResultRow]:
    index = OrderTagDefaultIndex(setup, profile.otd_default_key)
    names = profile.market_names()
    rows: list[ResultRow] = []
    for tree in profile.trees:
        for rule in tree.order_tag_defaults:
            places = [
                p
                for p in levels.at(rule.level)
                if p.tree.name == tree.name and rule.applies_to(p.account.name)
            ]
            for place in places:
                holder = _limit_holder(setup, levels, place)
                for market_id, field_id in rule.field_by_market().items():
                    exchange = setup.market_name(market_id, names)
                    if holder is not None and not _has_limit(setup, holder, market_id):
                        rows.append(_not_checked(rule, place, holder, exchange))
                    elif rule.set_on == "self":
                        rows.append(
                            _on_self(
                                setup, levels, index, rule, place, market_id, field_id, exchange
                            )
                        )
                    else:
                        rows.append(
                            _inherited(
                                setup, levels, index, rule, place, market_id, field_id, exchange
                            )
                        )
    return rows


def _limit_holder(setup: SetupData, levels: LevelMap, place: Place) -> Place | None:
    """The account whose limit rows cover ``place``: the nearest one, itself included, at
    a level with ``limit_rows_required``. None if there is no such level or it was not
    read; the default is then checked as usual."""
    for above in levels.chain(place):
        if above.level is not None and above.level.limit_rows_required:
            return above if setup.detail(above.account.id) is not None else None
    return None


def _has_limit(setup: SetupData, holder: Place, market_id: str) -> bool:
    wanted = norm_id(market_id)
    return any(
        norm_id(limit.get("marketId")) == wanted for limit in setup.limit_rows(holder.account.id)
    )


def _not_checked(
    rule: OrderTagDefaultRule, place: Place, holder: Place, exchange: str
) -> ResultRow:
    return ResultRow(
        node=place.account.display,
        node_label=place.label,
        rule_id="OTD",
        setting=f"{rule.label} ({exchange})",
        colour=Colour.GREY,
        expected="Not checked",
        actual=f"No limits for {exchange} on {holder.account.name}",
        actual_level=holder.label,
        reason=Reason.NOT_APPLICABLE,
        message=(
            f"Not checked: {holder.account.name} has no limits for {exchange}, so "
            f"{rule.label} is not needed for {exchange} on {place.account.name}."
        ),
    )


def _on_self(
    setup: SetupData,
    levels: LevelMap,
    index: OrderTagDefaultIndex,
    rule: OrderTagDefaultRule,
    place: Place,
    market_id: str,
    field_id: str,
    exchange: str,
) -> ResultRow:
    name = place.account.name
    common: dict[str, Any] = {
        "node": place.account.display,
        "node_label": place.label,
        "rule_id": "OTD",
        "setting": f"{rule.label} ({exchange})",
        "expected_level": place.label,
    }
    expected = _expected(rule, name)
    if expected is None:
        return ResultRow(
            **common,
            colour=Colour.AMBER,
            reason=Reason.UNKNOWN,
            message=(
                f"Cannot work out the expected {rule.label} from the name {name}. "
                "Fix the account name first."
            ),
        )
    own = index.on_account(place.account.id, market_id, field_id)
    if own is not None:
        ok = own == expected
        return ResultRow(
            **common,
            colour=Colour.GREEN if ok else Colour.RED,
            expected=expected,
            actual=own,
            actual_level=place.label,
            reason=None if ok else Reason.MISMATCH,
            message="" if ok else f"Set {rule.label} for {exchange} to {expected} on {name}.",
        )
    inherited = index.resolve(place, levels, market_id, field_id)
    if inherited is not None:
        return ResultRow(
            **common,
            colour=Colour.RED,
            expected=expected,
            actual=inherited.value,
            actual_level=inherited.level,
            reason=Reason.WRONG_LEVEL,
            message=(
                f"{rule.label} for {exchange} is set on {_holder_name(inherited, setup)}, not "
                f"on {name}. Set it to {expected} on {name} itself."
            ),
        )
    colour, reason, note = _missing(index)
    return ResultRow(
        **common,
        colour=colour,
        expected=expected,
        actual="Not set",
        reason=reason,
        message=f"Set {rule.label} for {exchange} to {expected} on {name}.{note}",
    )


def _inherited(
    setup: SetupData,
    levels: LevelMap,
    index: OrderTagDefaultIndex,
    rule: OrderTagDefaultRule,
    place: Place,
    market_id: str,
    field_id: str,
    exchange: str,
) -> ResultRow:
    name = place.account.name
    common: dict[str, Any] = {
        "node": place.account.display,
        "node_label": place.label,
        "rule_id": "OTD",
        "setting": f"{rule.label} ({exchange})",
        "expected_level": _inherit_text(place.tree, rule.level),
    }
    expected = _expected(rule, name)
    if expected is None:
        return ResultRow(
            **common,
            colour=Colour.AMBER,
            reason=Reason.UNKNOWN,
            message=f"Cannot work out the expected {rule.label} from the name {name}.",
        )
    common["expected"] = expected
    found = index.resolve(place, levels, market_id, field_id)
    if found is None:
        colour, reason, note = _missing(index)
        return ResultRow(
            **common,
            colour=colour,
            actual="Not set",
            reason=reason,
            message=(
                f"Set {rule.label} for {exchange} to {expected} for {name} (on the account or "
                f"an account above it, up to {place.root.name}).{note}"
            ),
        )
    ok = found.value == expected
    holder = _holder_name(found, setup)
    where = "on it" if found.holder_id == place.account.id else f"from {holder}"
    return ResultRow(
        **common,
        colour=Colour.GREEN if ok else Colour.RED,
        actual=found.value,
        actual_level=found.level,
        reason=None if ok else Reason.MISMATCH,
        message=""
        if ok
        else (
            f"{name} gets {rule.label} = {found.value} for {exchange} {where}; it should be "
            f"{expected}. Set {expected} on {name}, or correct {holder}."
        ),
    )


def _expected(rule: OrderTagDefaultRule, name: str) -> str | None:
    if rule.value is not None:
        return rule.value
    match = re.fullmatch(rule.value_from_name or "", name)
    return match.group("base") if match else None
