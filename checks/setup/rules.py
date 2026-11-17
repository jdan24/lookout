"""Setup rules, driven by the client profile: levels, check boxes, markets, names, users.

Order Tag Default rules are in ``otd.py``. Every rule returns rows in the common
result shape (``core.results``) and runs on a saved snapshot.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from checks.setup.levels import LevelMap, Place
from checks.setup.profile import CompanionAccountRule, Profile, Tree
from core.normalize import norm_id
from core.results import Colour, Reason, ResultRow
from core.setup_model import SetupData, UnexpectedValue


def row(place: Place, rule_id: str, setting: str, colour: Colour, **fields: Any) -> ResultRow:
    return ResultRow(
        node=place.account.display,
        node_label=place.label,
        rule_id=rule_id,
        setting=setting,
        colour=colour,
        **fields,
    )


def unread(place: Place, rule_id: str, setting: str) -> ResultRow:
    return row(
        place,
        rule_id,
        setting,
        Colour.AMBER,
        reason=Reason.UNKNOWN,
        message=f"{place.account.name} was not read in this snapshot. Pull again to check it.",
    )


def read_box(
    setup: SetupData,
    read: Callable[[str], bool | None],
    place: Place,
    rule_id: str,
    setting: str,
) -> tuple[bool | None, ResultRow | None]:
    """A checkbox value, or an amber row saying why it could not be judged."""
    name = place.account.name
    try:
        value = read(place.account.id)
    except UnexpectedValue as exc:
        return None, row(
            place,
            rule_id,
            setting,
            Colour.AMBER,
            actual=f"{exc.field_name} = {exc.value!r}",
            reason=Reason.UNKNOWN,
            message=(
                f"TT sent an unexpected value for {setting} on {name} ({exc.field_name} = "
                f"{exc.value!r}). Check it by hand in TT."
            ),
        )
    if value is not None:
        return value, None
    if setup.detail(place.account.id) is None:
        return None, unread(place, rule_id, setting)
    return None, row(
        place,
        rule_id,
        setting,
        Colour.AMBER,
        actual="Not sent by TT",
        reason=Reason.UNKNOWN,
        message=f"TT's record for {name} has no {setting} value. Check it by hand in TT.",
    )


def _on_off(value: bool) -> str:
    return "ON" if value else "OFF"


def _where(tree: Tree, setting: str, root_name: str) -> str:
    """Where a box should be ticked in this tree, e.g. "Team" or "Client group (FUND)"."""
    labels = []
    for level in tree.levels:
        if getattr(level, setting) == "on" and level.label not in labels:
            labels.append(level.label if level.depth else f"{level.label} ({root_name})")
    return " or ".join(labels) or "Nowhere"


# --- LEVEL -----------------------------------------------------------------------


def check_levels(levels: LevelMap) -> list[ResultRow]:
    """Accounts that fit no configured level: amber, and no other rule checks them."""
    rows: list[ResultRow] = []
    for place in levels.in_scope():
        if place.level is not None:
            continue
        options = place.tree.levels_at(place.depth)
        wanted = "; ".join(_fit_text(level) for level in options) or "no level at this depth"
        rows.append(
            row(
                place,
                "LEVEL",
                "Level",
                Colour.AMBER,
                expected=wanted,
                actual=(
                    f"Depth {place.depth}, account type {place.account.account_type}, "
                    f"{'nothing' if place.leaf else 'accounts'} below"
                ),
                reason=Reason.NO_RULE,
                message=(
                    f"{place.account.name} sits {place.depth} level(s) below {place.root.name} "
                    "but does not fit any level in the profile, so no rule checks it. Check it "
                    "by hand, or add a level for it."
                ),
            )
        )
    return rows


def _fit_text(level: Any) -> str:
    parts = [level.label]
    if level.account_type is not None:
        parts.append(f"account type {level.account_type}")
    if level.match_regex:
        parts.append(f"name matching {level.match_regex}")
    return ", ".join(parts)


# --- LIMITS and RESTRICTIONS -------------------------------------------------------


def _checkbox(
    place: Place, rule_id: str, setting: str, expected: bool, actual: bool, where: str, fix: str
) -> ResultRow:
    ok = expected == actual
    return row(
        place,
        rule_id,
        setting,
        Colour.GREEN if ok else Colour.RED,
        expected=_on_off(expected),
        expected_level=where,
        actual=_on_off(actual),
        actual_level=place.label,
        # ticked where it should not be = wrong level; not ticked where it should be = missing
        reason=None if ok else (Reason.WRONG_LEVEL if actual else Reason.MISSING),
        message="" if ok else fix,
    )


def check_apply_limits(setup: SetupData, levels: LevelMap) -> list[ResultRow]:
    """Apply Limits is ticked (with limit rows) where the profile says ON, unticked where OFF."""
    rows: list[ResultRow] = []
    for place in levels.fitted():
        assert place.level is not None
        want = place.level.apply_limits
        if want == "any":
            continue
        name = place.account.name
        where = _where(place.tree, "apply_limits", place.root.name)
        ticked, problem = read_box(setup, setup.apply_limits, place, "LIMITS", "Apply Limits")
        if problem is not None or ticked is None:
            rows.append(problem or unread(place, "LIMITS", "Apply Limits"))
            continue
        if want == "off":
            rows.append(
                _checkbox(
                    place,
                    "LIMITS",
                    "Apply Limits",
                    expected=False,
                    actual=ticked,
                    where=where,
                    fix=f"Untick Apply Limits on {name}. Limits belong on the {where} level.",
                )
            )
            continue

        count = len(setup.limit_rows(place.account.id))
        needs_rows = place.level.limit_rows_required
        if place.empty and not (ticked and (count or not needs_rows)):
            rows.append(
                row(
                    place,
                    "LIMITS",
                    "Apply Limits",
                    Colour.AMBER,
                    expected="ON",
                    expected_level=where,
                    actual=f"{_on_off(ticked)}, {count} limit rows",
                    actual_level=place.label,
                    reason=Reason.NO_RULE,
                    message=(
                        f"{name} is new and has nothing below it yet. Tick Apply Limits"
                        + (" and add its limit rows" if needs_rows else "")
                        + " before accounts are added."
                    ),
                )
            )
            continue
        rows.append(
            _checkbox(
                place,
                "LIMITS",
                "Apply Limits",
                expected=True,
                actual=ticked,
                where=where,
                fix=f"Tick Apply Limits on {place.level.label.lower()} {name}.",
            )
        )
        if needs_rows:
            ok = count > 0
            rows.append(
                row(
                    place,
                    "LIMITS",
                    "Limit rows",
                    Colour.GREEN if ok else Colour.RED,
                    expected="At least one limit row",
                    expected_level=where,
                    actual=f"{count} limit rows",
                    actual_level=place.label,
                    reason=None if ok else Reason.MISSING,
                    message="" if ok else f"Add the limit rows on {name}.",
                )
            )
    return rows


def check_apply_restrictions(setup: SetupData, levels: LevelMap) -> list[ResultRow]:
    """Apply Restrictions is ticked where the profile says ON and unticked where OFF."""
    rows: list[ResultRow] = []
    for place in levels.fitted():
        assert place.level is not None
        want = place.level.apply_restrictions
        if want == "any":
            continue
        name = place.account.name
        where = _where(place.tree, "apply_restrictions", place.root.name)
        ticked, problem = read_box(
            setup, setup.apply_restrictions, place, "RESTRICTIONS", "Apply Restrictions"
        )
        if problem is not None or ticked is None:
            rows.append(problem or unread(place, "RESTRICTIONS", "Apply Restrictions"))
            continue
        expected = want == "on"
        if place.empty and expected and not ticked:
            rows.append(
                row(
                    place,
                    "RESTRICTIONS",
                    "Apply Restrictions",
                    Colour.AMBER,
                    expected="ON",
                    expected_level=where,
                    actual="OFF",
                    actual_level=place.label,
                    reason=Reason.NO_RULE,
                    message=(
                        f"{name} is new and has nothing below it yet. Tick Apply Restrictions "
                        "before accounts are added."
                    ),
                )
            )
            continue
        fix = (
            f"Tick Apply Restrictions on {name}."
            if expected
            else f"Untick Apply Restrictions on {name}. In this tree it is set on {where} only."
        )
        rows.append(
            _checkbox(
                place,
                "RESTRICTIONS",
                "Apply Restrictions",
                expected=expected,
                actual=ticked,
                where=where,
                fix=fix,
            )
        )
    return rows


# --- MARKETS ---------------------------------------------------------------------


def check_markets(setup: SetupData, levels: LevelMap, profile: Profile) -> list[ResultRow]:
    """Limit rows may only use the markets of their own tree."""
    rows: list[ResultRow] = []
    names = profile.market_names()
    for place in levels.fitted():
        tree = place.tree
        if not tree.markets:
            continue
        symbols_by_market: dict[str, list[str]] = {}
        for limit in setup.limit_rows(place.account.id):
            market = norm_id(limit.get("marketId"))
            symbols_by_market.setdefault(market, []).append(str(limit.get("productSymbol") or "?"))

        for market, symbols in symbols_by_market.items():
            exchange = setup.market_name(market, names)
            setting = f"Limit market: {exchange}"
            products = ", ".join(sorted(set(symbols)))
            actual = f"{exchange} ({market}): {products}"
            expected = "One of: " + ", ".join(tree.markets.values())
            owner = profile.tree_of_market(market)
            if owner is not None and owner.name == tree.name:
                rows.append(
                    row(place, "MARKETS", setting, Colour.GREEN, expected=expected, actual=actual)
                )
            elif owner is None:
                rows.append(
                    row(
                        place,
                        "MARKETS",
                        setting,
                        Colour.AMBER,
                        expected=expected,
                        actual=actual,
                        reason=Reason.NO_RULE,
                        message=(
                            f"Market {market} is not listed in any tree of the profile. Check "
                            f"the {products} limit on {place.account.name} by hand, or add the "
                            "market to the profile."
                        ),
                    )
                )
            else:
                other = owner.start_name
                rows.append(
                    row(
                        place,
                        "MARKETS",
                        setting,
                        Colour.RED,
                        expected=expected,
                        expected_level=f"{place.level.label if place.level else ''} under {other}",
                        actual=actual,
                        actual_level=f"{place.level.label if place.level else ''} under "
                        f"{place.root.name}",
                        reason=Reason.WRONG_LEVEL,
                        message=(
                            f"{exchange} limits ({products}) do not belong under "
                            f"{place.root.name}. Move them from {place.account.name} to the "
                            f"matching account under {other}."
                        ),
                    )
                )
    return rows


# --- NAMING ----------------------------------------------------------------------


def check_naming(levels: LevelMap) -> list[ResultRow]:
    """Names match the pattern of their level."""
    rows: list[ResultRow] = []
    for place in levels.fitted():
        assert place.level is not None
        pattern = place.level.name_regex
        if not pattern:
            continue
        name = place.account.name
        ok = re.search(pattern, name) is not None
        hint = place.level.name_hint or pattern
        rows.append(
            row(
                place,
                "NAMING",
                "Name",
                Colour.GREEN if ok else Colour.RED,
                expected=hint,
                actual=name,
                reason=None if ok else Reason.MISMATCH,
                message=""
                if ok
                else f"Rename {name}: it should look like {hint} (pattern {pattern}).",
            )
        )
    return rows


# --- COMPANION -------------------------------------------------------------------


def companion_products(setup: SetupData, account_id: str, rule: CompanionAccountRule) -> list[str]:
    """The rule's products with a limit row on the account (empty list if none)."""
    wanted = set(rule.products)
    return sorted(
        {
            str(limit.get("productSymbol"))
            for limit in setup.limit_rows(account_id)
            if norm_id(limit.get("marketId")) == rule.market
            and limit.get("productSymbol") in wanted
        }
    )


def check_companion(setup: SetupData, levels: LevelMap, profile: Profile) -> list[ResultRow]:
    """An account with a limit on certain products needs a companion account below it."""
    rule = profile.rules.companion_account
    if rule is None:
        return []
    rows: list[ResultRow] = []
    suffix = rule.suffix
    for place in levels.at(rule.level):
        name = place.account.name
        companions = [p for p in levels.children(place) if p.account.name.endswith(suffix)]
        if setup.detail(place.account.id) is None:
            if companions:
                rows.append(unread(place, "COMPANION", rule.label))
            continue
        symbols = companion_products(setup, place.account.id, rule)
        below = f"An {suffix} account below the {rule.level.lower()}"
        if symbols and companions:
            rows.append(
                row(
                    place,
                    "COMPANION",
                    rule.label,
                    Colour.GREEN,
                    expected=below,
                    actual=", ".join(p.account.name for p in companions),
                    actual_level=companions[0].label,
                )
            )
        elif symbols:
            rows.append(
                row(
                    place,
                    "COMPANION",
                    rule.label,
                    Colour.RED,
                    expected=below,
                    actual="None",
                    reason=Reason.MISSING,
                    message=(
                        f"{name} has a limit on {', '.join(symbols)} but no account ending in "
                        f"{suffix}. Add the {suffix} account under {name}."
                    ),
                )
            )
        else:
            colour = Colour.RED if rule.unnecessary == "red" else Colour.AMBER
            for extra in companions:
                rows.append(
                    row(
                        extra,
                        "COMPANION",
                        rule.label,
                        colour,
                        expected=f"A limit on {', '.join(rule.products)} on {name}",
                        expected_level=rule.level,
                        actual="No such limit",
                        reason=Reason.WRONG_LEVEL if colour is Colour.RED else Reason.NO_RULE,
                        message=(
                            f"{extra.account.name} ends in {suffix}, but {name} has no limit on "
                            f"{', '.join(rule.products)}. It looks unnecessary; confirm whether "
                            "it should stay."
                        ),
                    )
                )
    return rows


# --- USERS -----------------------------------------------------------------------


def check_users(setup: SetupData, levels: LevelMap) -> list[ResultRow]:
    """Every lowest-level account has all the tree's required users.

    A user counts when assigned on the account itself or on any account above it, up to
    the start account. Where a user is assigned is not judged, only that it is there.
    """
    names: dict[str, str] = {}
    for assigned in setup.users:
        for user in setup.direct_users(assigned) or []:
            if user["name"]:
                names.setdefault(user["id"], user["name"])

    def who(user_id: str) -> str:
        return f"{names[user_id]} ({user_id})" if user_id in names else f"user {user_id}"

    rows: list[ResultRow] = []
    for place in levels.fitted():
        required = place.tree.required_users
        if not required or place.depth < place.tree.max_depth:
            continue
        chain = levels.chain(place)
        source: dict[str, str] = {}  # user id -> name of the nearest account it is assigned on
        unread: list[str] = []
        for above in chain:
            users = setup.direct_users(above.account.id)
            if users is None:
                unread.append(above.account.name)
                continue
            for user in users:
                source.setdefault(user["id"], above.account.name)
        missing = [user_id for user_id in required if user_id not in source]
        name = place.account.name
        common = {
            "expected": ", ".join(who(user_id) for user_id in required),
            "expected_level": "This account or any account above it",
            "actual_level": place.label,
        }
        found = ", ".join(f"{who(u)} from {source[u]}" for u in required if u in source)
        if not missing:
            rows.append(row(place, "USERS", "Users", Colour.GREEN, actual=found, **common))
        elif unread:
            rows.append(
                row(
                    place,
                    "USERS",
                    "Users",
                    Colour.AMBER,
                    actual=found or "None found",
                    reason=Reason.UNKNOWN,
                    message=(
                        f"Users on {', '.join(unread)} were not read, so "
                        f"{', '.join(who(u) for u in missing)} may be assigned there. Pull again."
                    ),
                    **common,
                )
            )
        else:
            rows.append(
                row(
                    place,
                    "USERS",
                    "Users",
                    Colour.RED,
                    actual="Missing: " + ", ".join(who(u) for u in missing),
                    reason=Reason.MISSING,
                    message=(
                        f"Assign {', '.join(who(u) for u in missing)} on {name} or on an "
                        f"account above it, up to {place.root.name}."
                    ),
                    **common,
                )
            )
    return rows


# --- LEGACY ----------------------------------------------------------------------


def check_legacy_groups(setup: SetupData, levels: LevelMap, profile: Profile) -> list[ResultRow]:
    """Accounts with the client's naming that sit outside its start accounts."""
    rule = profile.rules.legacy_groups
    if rule is None:
        return []
    pattern = re.compile(rule.name_regex)
    starts = " or ".join(tree.start_name for tree in profile.trees)
    rows: list[ResultRow] = []
    for account in sorted(setup.tree.all(), key=lambda a: (a.name, a.id)):
        if account.id in levels.places or account.id in levels.path_ids:
            continue
        if not pattern.search(account.name):
            continue
        parent = setup.tree.parent(account.id)
        rows.append(
            ResultRow(
                node=account.display,
                node_label="Outside the client's trees",
                rule_id="LEGACY",
                setting="Legacy group",
                colour=Colour.AMBER,
                expected=f"Under {starts}",
                actual=f"Under {parent.display}" if parent else "Top level",
                reason=Reason.TO_BE_MIGRATED,
                message=f"To Be Migrated: {account.name} sits outside {starts}.",
            )
        )
    return rows
