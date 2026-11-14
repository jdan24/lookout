"""Compare requested limits with the limits currently in place.

Ported from limit_compare's QE Limit update (``tools/qeLimits/fields.ts`` and
``limits/normalize.ts``, ``join.ts``, ``diff.ts``). Behaviour is kept identical;
parity tests compare the results with the original TypeScript on the same inputs.

Not ported: risk's approved-value overrides and the Excel / system-upload exports,
which belong to the approval workflow, not to verification.

One addition: ``exchange_aware=True`` also matches on exchange, for requests that
were already resolved to a TT exchange + symbol (the same symbol can exist on two
exchanges, e.g. ``G`` on ICE and ICE_L). The default behaves exactly as the original.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from checks.tabular import FieldSpec, RawTable, canon

CLIENT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("qCode", "Q Code", ("Q Code", "QCode", "Q"), False),
    FieldSpec("tsTicker", "TS Ticker", ("TS Ticker", "TSTicker", "TS"), True),
    FieldSpec("bbgTicker", "BBG Ticker", ("BBG Ticker", "Bloomberg Ticker", "BBG"), False),
    FieldSpec("contractDescription", "Contract Description",
              ("Contract Description", "Description", "Contract"), False),
    FieldSpec("currency", "Currency", ("Currency", "Ccy"), False),
    FieldSpec("supported", "Supported", ("Supported", "Support"), False, ("Supported",)),
    FieldSpec("assetClass", "Asset Class", ("Asset Class", "AssetClass", "Class"), False),
    FieldSpec("exchange", "Exchange", ("Exchange", "Exch"), False),
    FieldSpec("type", "Type", ("Type", "Ty"), True),
    FieldSpec("electronicVoice", "Electronic / Voice / Both",
              ("Electronic / Voice / Both", "Electronic/Voice/Both", "Electronic Voice Both",
               "Electronic / Voice / Bo"), False),
    FieldSpec("blockTrade", "Block Trade", ("Block Trade (Y/N)", "Block Trade", "Block"), False),
    FieldSpec("maxPosition", "Outrights/Spreads Max Position",
              ("Outrights/Spreads Max Position", "Outrights / Spreads Max Position",
               "Max Position"), True),
    FieldSpec("maxOrderQtyOutright", "Max Order Quantity (Outright)",
              ("Max Order Quantity (Outright)", "Max Order Quantity Outright",
               "Max Order Qty Outright"), True),
    FieldSpec("maxOrderQtySpreads", "Max Order Quantity (Spreads)",
              ("Max Order Quantity (Spreads)", "Max Order Quantity Spreads",
               "Max Order Qty Spreads"), True),
)  # fmt: skip

SYSTEM_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("account", "Account (TeName)",
              ("TeName", "Isv TeName", "IsvTeName", "Account"), False),
    FieldSpec("exchange", "Exchange", ("Exchange", "Exch"), False),
    FieldSpec("family", "Family", ("Family",), True),
    FieldSpec("type", "Type", ("Type",), True),
    FieldSpec("product", "Product", ("Product",), False),
    FieldSpec("maxPositionNet", "Max position product (net)",
              ("Max position product (net)", "Max position product net",
               "Max position (net)"), True),
    FieldSpec("maxOrderQty", "Max order quantity", ("Max order quantity", "Max order qty"), True),
    FieldSpec("spreadMaxOrderQty", "Spreads: Max order quantity",
              ("Spreads: Max order quantity", "Spreads Max order quantity",
               "Spread Max order quantity"), True),
)  # fmt: skip

Number = int | float
MetricCategory = Literal["increase", "decrease", "same", "new", "na"]
RowCategory = Literal["increase", "decrease", "unchanged", "new", "systemOnly", "notSupported"]
MatchMethod = Literal["direct", "mapped", "none", "systemOnly"]
ROW_CATEGORIES: tuple[RowCategory, ...] = (
    "increase", "decrease", "unchanged", "new", "systemOnly", "notSupported",
)  # fmt: skip
METRICS = ("maxPosition", "maxOrderQtyOutright", "maxOrderQtySpreads")


# --- parsing ---------------------------------------------------------------------

_NO_LIMIT = {"not set", "n/a", "na", "-", "—"}
_STRIP = re.compile(r"[,\s$%]")
_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_RADIX = {"0x": 16, "0o": 8, "0b": 2}


def _js_number(text: str) -> Number | None:
    """JavaScript's ``Number(text)`` for a string without spaces; None where it gives NaN
    or Infinity."""
    if _DECIMAL.fullmatch(text):
        value = float(text)
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return int(value) if value.is_integer() and abs(value) < 2**53 else value
    base = _RADIX.get(text[:2].lower())
    if base:
        try:
            return int(text[2:], base) if text[2:] and "_" not in text else None
        except ValueError:
            return None
    return None


def parse_limit(raw: str | None) -> Number | None:
    """A limit cell as a number, or None for "no limit".

    "", "Not Set", "N/A", "-" and any non-numeric text mean no limit. Thousands
    separators and stray currency / percent signs are ignored.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in _NO_LIMIT:
        return None
    cleaned = _STRIP.sub("", text)
    return _js_number(cleaned) if cleaned else None


def is_not_supported(raw: str | None) -> bool:
    """An explicit "N" in the Supported column."""
    return canon(raw or "") == "n"


def join_key(symbol: str, type_: str) -> str:
    return f"{canon(symbol)}|{canon(type_)}"


# --- records ---------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class ClientProduct:
    """One requested product, with the source row kept for display."""

    ts_ticker: str
    type: str
    req_max_position: Number | None
    req_max_order_qty_outright: Number | None
    req_max_order_qty_spreads: Number | None
    not_supported: bool = False
    exchange: str = ""
    q_code: str = ""
    bbg_ticker: str = ""
    contract_description: str = ""
    currency: str = ""
    supported: str = ""
    asset_class: str = ""
    electronic_voice: str = ""
    block_trade: str = ""
    idx: int = 0
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, eq=False)
class SystemLimit:
    """One limit row currently in place."""

    family: str
    type: str
    cur_max_position: Number | None
    cur_max_order_qty: Number | None
    cur_spread_max_order_qty: Number | None
    account: str = ""
    exchange: str = ""
    product: str = ""
    source: str = ""
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SymbolMapEntry:
    """Requested ticker (+ optional type) -> the system family it should match."""

    client_ticker: str
    type: str
    system_family: str


def _getter(mapping: dict[str, str]):  # noqa: ANN202
    def get(row: dict[str, str], name: str) -> str:
        header = mapping.get(name) or ""
        return (row.get(header) or "").strip() if header else ""

    return get


def normalize_client(raw: RawTable, mapping: dict[str, str]) -> list[ClientProduct]:
    get = _getter(mapping)
    out: list[ClientProduct] = []
    for idx, row in enumerate(raw.rows):
        ts_ticker, bbg, desc = (
            get(row, "tsTicker"),
            get(row, "bbgTicker"),
            get(row, "contractDescription"),
        )
        if not ts_ticker and not bbg and not desc and not get(row, "qCode"):
            continue  # spacer rows
        out.append(
            ClientProduct(
                q_code=get(row, "qCode"),
                ts_ticker=ts_ticker,
                bbg_ticker=bbg,
                contract_description=desc,
                currency=get(row, "currency"),
                supported=get(row, "supported"),
                asset_class=get(row, "assetClass"),
                exchange=get(row, "exchange"),
                type=get(row, "type"),
                electronic_voice=get(row, "electronicVoice"),
                block_trade=get(row, "blockTrade"),
                req_max_position=parse_limit(get(row, "maxPosition")),
                req_max_order_qty_outright=parse_limit(get(row, "maxOrderQtyOutright")),
                req_max_order_qty_spreads=parse_limit(get(row, "maxOrderQtySpreads")),
                not_supported=is_not_supported(get(row, "supported")),
                raw=row,
                idx=idx,
            )
        )
    return out


def normalize_system(raw: RawTable, mapping: dict[str, str]) -> list[SystemLimit]:
    get = _getter(mapping)
    out: list[SystemLimit] = []
    for row in raw.rows:
        family = get(row, "family")
        if not family:
            continue  # rows without a family cannot be joined
        out.append(
            SystemLimit(
                account=get(row, "account"),
                exchange=get(row, "exchange"),
                family=family,
                type=get(row, "type"),
                product=get(row, "product"),
                cur_max_position=parse_limit(get(row, "maxPositionNet")),
                cur_max_order_qty=parse_limit(get(row, "maxOrderQty")),
                cur_spread_max_order_qty=parse_limit(get(row, "spreadMaxOrderQty")),
                source=raw.file_name,
                raw=row,
            )
        )
    return out


# --- joining ---------------------------------------------------------------------


@dataclass
class SystemLookup:
    by_family_type: dict[str, SystemLimit]
    by_family: dict[str, SystemLimit]
    conflicts: list[str]


def _keys(exchange: str, family: str, type_: str, exchange_aware: bool) -> tuple[str, str]:
    prefix = f"{canon(exchange)}|" if exchange_aware else ""
    return prefix + join_key(family, type_), prefix + canon(family)


def build_system_lookup(
    systems: Sequence[SystemLimit], *, exchange_aware: bool = False
) -> SystemLookup:
    """Later rows win on a key clash; clashes with different limits are reported."""
    by_family_type: dict[str, SystemLimit] = {}
    by_family: dict[str, SystemLimit] = {}
    conflicts: dict[str, None] = {}
    for s in systems:
        ft_key, f_key = _keys(s.exchange, s.family, s.type, exchange_aware)
        previous = by_family_type.get(ft_key)
        if previous is not None and (
            previous.cur_max_position,
            previous.cur_max_order_qty,
            previous.cur_spread_max_order_qty,
        ) != (s.cur_max_position, s.cur_max_order_qty, s.cur_spread_max_order_qty):
            conflicts[f"{s.family} / {s.type}"] = None
        by_family_type[ft_key] = s
        by_family[f_key] = s
    return SystemLookup(by_family_type, by_family, list(conflicts))


@dataclass
class SymbolMapIndex:
    by_ticker_type: dict[str, str]
    by_ticker: dict[str, str]


def build_symbol_map_index(entries: Sequence[SymbolMapEntry]) -> SymbolMapIndex:
    index = SymbolMapIndex({}, {})
    for e in entries:
        if not e.client_ticker or not e.system_family:
            continue
        if e.type:
            index.by_ticker_type[join_key(e.client_ticker, e.type)] = e.system_family
        else:
            index.by_ticker[canon(e.client_ticker)] = e.system_family
    return index


def match_client(
    ts_ticker: str,
    type_: str,
    lookup: SystemLookup,
    symbol_map: SymbolMapIndex,
    *,
    exchange: str = "",
    exchange_aware: bool = False,
) -> tuple[SystemLimit | None, Literal["direct", "mapped", "none"]]:
    """1. family + type; 2. family only; 3. the symbol map, then 1 and 2 again."""
    ft_key, f_key = _keys(exchange, ts_ticker, type_, exchange_aware)
    direct = lookup.by_family_type.get(ft_key) or lookup.by_family.get(f_key)
    if direct is not None:
        return direct, "direct"
    mapped_family = symbol_map.by_ticker_type.get(join_key(ts_ticker, type_)) or (
        symbol_map.by_ticker.get(canon(ts_ticker))
    )
    if mapped_family:
        mft, mf = _keys(exchange, mapped_family, type_, exchange_aware)
        via_map = lookup.by_family_type.get(mft) or lookup.by_family.get(mf)
        if via_map is not None:
            return via_map, "mapped"
    return None, "none"


# --- diff ------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricCell:
    current: Number | None
    requested: Number | None
    category: MetricCategory


EMPTY_CELL = MetricCell(None, None, "na")


def compare(current: Number | None, requested: Number | None) -> MetricCell:
    if requested is None:
        category: MetricCategory = "na"  # nothing requested (with or without a current limit)
    elif current is None:
        category = "new"
    elif requested > current:
        category = "increase"
    elif requested < current:
        category = "decrease"
    else:
        category = "same"
    return MetricCell(current, requested, category)


def rollup(cells: Sequence[MetricCell]) -> RowCategory:
    for wanted in ("increase", "decrease", "new"):
        if any(c.category == wanted for c in cells):
            return wanted  # type: ignore[return-value]
    return "unchanged"


@dataclass(frozen=True)
class DiffRow:
    key: str
    category: RowCategory
    match_method: MatchMethod
    client: ClientProduct | None
    system: SystemLimit | None
    account: str
    max_position: MetricCell
    max_order_qty_outright: MetricCell
    max_order_qty_spreads: MetricCell

    def cells(self) -> dict[str, MetricCell]:
        return {
            "maxPosition": self.max_position,
            "maxOrderQtyOutright": self.max_order_qty_outright,
            "maxOrderQtySpreads": self.max_order_qty_spreads,
        }


@dataclass(frozen=True)
class DiffResult:
    rows: list[DiffRow]
    counts: dict[RowCategory, int]
    unmatched: list[DiffRow]
    conflicts: list[str]


def compute_diff(
    clients: Sequence[ClientProduct],
    systems: Sequence[SystemLimit],
    symbol_entries: Sequence[SymbolMapEntry] = (),
    *,
    exchange_aware: bool = False,
) -> DiffResult:
    """Requested vs current, row by row; current rows nobody asked for are added as
    ``systemOnly``."""
    lookup = build_system_lookup(systems, exchange_aware=exchange_aware)
    symbol_map = build_symbol_map_index(symbol_entries)
    matched: list[SystemLimit] = []
    rows: list[DiffRow] = []
    counts: dict[RowCategory, int] = dict.fromkeys(ROW_CATEGORIES, 0)

    for c in clients:
        if c.not_supported:
            rows.append(
                DiffRow(
                    f"client-{c.idx}",
                    "notSupported",
                    "none",
                    c,
                    None,
                    "",
                    EMPTY_CELL,
                    EMPTY_CELL,
                    EMPTY_CELL,
                )  # fmt: skip
            )
            counts["notSupported"] += 1
            continue
        system, method = match_client(
            c.ts_ticker,
            c.type,
            lookup,
            symbol_map,
            exchange=c.exchange,
            exchange_aware=exchange_aware,
        )
        if system is not None and not any(system is m for m in matched):
            matched.append(system)
        pos = compare(system.cur_max_position if system else None, c.req_max_position)
        out = compare(system.cur_max_order_qty if system else None, c.req_max_order_qty_outright)
        spr = compare(
            system.cur_spread_max_order_qty if system else None, c.req_max_order_qty_spreads
        )
        category: RowCategory = rollup([pos, out, spr]) if system is not None else "new"
        counts[category] += 1
        rows.append(
            DiffRow(
                f"client-{c.idx}",
                category,
                method,
                c,
                system,
                system.account if system else "",
                pos,
                out,
                spr,
            )  # fmt: skip
        )

    so_idx = 0
    for s in systems:
        if any(s is m for m in matched):
            continue
        counts["systemOnly"] += 1
        rows.append(
            DiffRow(
                f"system-{so_idx}",
                "systemOnly",
                "systemOnly",
                None,
                s,
                s.account,
                MetricCell(s.cur_max_position, None, "na"),
                MetricCell(s.cur_max_order_qty, None, "na"),
                MetricCell(s.cur_spread_max_order_qty, None, "na"),
            )  # fmt: skip
        )
        so_idx += 1

    unmatched = [r for r in rows if r.category == "new" and r.match_method == "none"]
    return DiffResult(rows=rows, counts=counts, unmatched=unmatched, conflicts=lookup.conflicts)


def parse_symbol_map(raw: RawTable) -> list[SymbolMapEntry]:
    """A symbol-map table with clientTicker, type and systemFamily columns (loosely named)."""
    headers = {canon(h): h for h in raw.headers}

    def get(row: dict[str, str], name: str) -> str:
        header = headers.get(canon(name))
        return (row.get(header) or "").strip() if header else ""

    return [
        SymbolMapEntry(get(r, "clientTicker"), get(r, "type"), get(r, "systemFamily"))
        for r in raw.rows
        if get(r, "clientTicker") and get(r, "systemFamily")
    ]
