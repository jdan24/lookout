"""Display-only views for Setup Verification. Nothing here is judged."""

from __future__ import annotations

from checks.setup.levels import LevelMap
from checks.setup.profile import Profile
from core.flatten import category_of, flatten
from core.setup_model import SetupData

# TT field -> TT Setup column name
LIMIT_COLUMNS: dict[str, str] = {
    "productSymbol": "Product",
    "productTypeId": "Product type id",
    "productFamilyId": "Product family id",
    "productId": "Product id",
    "maxPos": "Max Position Product (net)",
    "maxLongPos": "Max long position (gross)",
    "maxShortPos": "Max short position (gross)",
    "outright_maxOrderQty": "Outright max order quantity",
    "spread_maxOrderQty": "Sprd/Strat max order quantity",
    "outright_tradingAllowed": "Outright trading allowed",
    "spread_tradingAllowed": "Spread trading allowed",
    "tradeOutAllowed": "Trade out allowed",
}


def _text(value: object) -> str:
    return "" if value is None else str(value)


def limits_by_level(setup: SetupData, levels: LevelMap, profile: Profile) -> list[dict[str, str]]:
    """Product limit rows on every in-scope account. All values as text."""
    names = profile.market_names()
    table: list[dict[str, str]] = []
    for place in levels.in_scope():
        for limit in setup.limit_rows(place.account.id, "productLimits"):
            row = {
                "Account": place.account.display,
                "Level": place.label,
                "Start account": place.root.name,
                "Exchange": setup.market_name(limit.get("marketId"), names),
            }
            row.update({title: _text(limit.get(field)) for field, title in LIMIT_COLUMNS.items()})
            table.append(row)
    return table


def settings_by_node(setup: SetupData, levels: LevelMap) -> list[dict[str, str]]:
    """Every raw field of every in-scope account, flattened to one row per value."""
    table: list[dict[str, str]] = []
    for place in levels.in_scope():
        record = setup.detail(place.account.id)
        if record is None:
            continue
        for json_path, value in flatten(record):
            table.append(
                {
                    "node": place.account.display,
                    "node_label": place.label,
                    "category": category_of(json_path),
                    "json_path": json_path,
                    "value": _text(value),
                }
            )
    return table
