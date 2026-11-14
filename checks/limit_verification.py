"""Limit Verification: requested limits against the limit rows in a TT snapshot.

Two kinds of requested-limit sheet are accepted, as in limit_compare:

* a RIC sheet (Ric Root, Max Order, Max Net, optional Exchange / Description): each
  row is first matched to a TT exchange + symbol (``checks.ric_mapping``), then
  compared on exchange + symbol;
* a product universe (TS Ticker, Type, Max Position, Max Order Quantity (Outright)
  and (Spreads), optional Supported): compared on symbol + type, with an optional
  symbol map, exactly as QE Limit update does.

The "current" side is the product limit rows of one account in the snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from checks.limit_compare import (
    CLIENT_FIELDS,
    ClientProduct,
    DiffResult,
    DiffRow,
    MetricCell,
    Number,
    SymbolMapEntry,
    SystemLimit,
    compute_diff,
    normalize_client,
    parse_limit,
)
from checks.ric_mapping import RIC_FIELDS, EnrichedResult, EnrichedRow, RicMapping, enrich
from checks.tabular import FieldSpec, RawTable, auto_detect, is_complete
from checks.tt_products import TtIndex, TtProduct, build_tt_index
from core.hierarchy import Account
from core.normalize import norm_id
from core.results import Colour, Reason, ResultRow
from core.setup_model import SetupData

SheetFormat = Literal["ric", "universe"]
FORMAT_FIELDS: dict[SheetFormat, tuple[FieldSpec, ...]] = {
    "ric": RIC_FIELDS,
    "universe": CLIENT_FIELDS,
}
FORMAT_NAMES: dict[SheetFormat, str] = {
    "ric": "RIC sheet (Ric Root, Max Order, Max Net)",
    "universe": "Product universe (TS Ticker, Type, Max Position, Max Order Quantities)",
}

# TT productTypeId -> name. Only Future (34) is confirmed.
PRODUCT_TYPES = {"34": "Future"}

# The three compared limits, with the TT Setup column names.
METRIC_LABELS = {
    "maxPosition": "Max Position Product (net)",
    "maxOrderQtyOutright": "Outright max order quantity",
    "maxOrderQtySpreads": "Sprd/Strat max order quantity",
}

RULE_COMPARE = "LV1"  # a requested limit against the limit in TT
RULE_MAPPING = "LV2"  # a requested row could not be matched to a TT product with certainty
RULE_NOT_REQUESTED = "LV3"  # a limit row in TT that the sheet does not mention
RULE_NOT_SUPPORTED = "LV4"  # marked not supported in the sheet


# --- the snapshot side -----------------------------------------------------------


def _number(value: object) -> Number | None:
    return None if value is None else parse_limit(str(value))


def product_type_name(type_id: object) -> str:
    key = norm_id(type_id)
    return PRODUCT_TYPES.get(key, f"type {key or '?'}")


def accounts_with_limits(setup: SetupData) -> list[tuple[Account, int]]:
    """Accounts in the snapshot that have product limit rows, with how many."""
    found = []
    for account_id in setup.details:
        rows = setup.limit_rows(account_id, "productLimits")
        if rows and account_id in setup.tree:
            found.append((setup.tree.get(account_id), len(rows)))
    return sorted(found, key=lambda pair: (pair[0].name, pair[0].id))


def current_limits(setup: SetupData, account_id: str) -> list[SystemLimit]:
    """The account's product limit rows, in the shape the compare expects."""
    account = setup.tree.get(account_id)
    return [
        SystemLimit(
            family=str(row.get("productSymbol") or ""),
            type=product_type_name(row.get("productTypeId")),
            exchange=setup.market_name(row.get("marketId")),
            account=account.name,
            product=norm_id(row.get("productId")),
            cur_max_position=_number(row.get("maxPos")),
            cur_max_order_qty=_number(row.get("outright_maxOrderQty")),
            cur_spread_max_order_qty=_number(row.get("spread_maxOrderQty")),
            source=account.display,
        )
        for row in setup.limit_rows(account_id, "productLimits")
        if row.get("productSymbol")
    ]


def snapshot_products(setup: SetupData) -> list[TtProduct]:
    """Every exchange + symbol with a limit row anywhere in the snapshot (names unknown)."""
    seen: dict[tuple[str, str], TtProduct] = {}
    for account_id in setup.details:
        for row in setup.limit_rows(account_id, "productLimits"):
            symbol = str(row.get("productSymbol") or "")
            exchange = setup.market_name(row.get("marketId"))
            if symbol and (exchange, symbol) not in seen:
                seen[(exchange, symbol)] = TtProduct(exchange=exchange, symbol=symbol)
    return list(seen.values())


def product_index(setup: SetupData, product_list: Sequence[TtProduct] = ()) -> TtIndex:
    """TT's product list if given, plus anything with a limit row in the snapshot."""
    have = {(p.exchange, p.symbol) for p in product_list}
    extra = [p for p in snapshot_products(setup) if (p.exchange, p.symbol) not in have]
    return build_tt_index([*product_list, *extra])


# --- the requested side ----------------------------------------------------------


def detect_format(raw: RawTable) -> tuple[SheetFormat | None, dict[str, str]]:
    """Which kind of sheet this is, and its detected columns. Universe wins a tie."""
    universe = auto_detect(CLIENT_FIELDS, raw.headers).mapping
    if is_complete(CLIENT_FIELDS, universe):
        return "universe", universe
    ric = auto_detect(RIC_FIELDS, raw.headers).mapping
    if is_complete(RIC_FIELDS, ric):
        return "ric", ric
    return None, (ric if len(ric) >= len(universe) else universe)


def _ric_requests(enriched: EnrichedResult, cols: dict[str, str]) -> list[ClientProduct]:
    def cell(row: EnrichedRow, name: str) -> str:
        header = cols.get(name)
        return (row.cells.get(header) or "").strip() if header else ""

    return [
        ClientProduct(
            ts_ticker=row.tt_symbol,
            type="Future",
            exchange=row.tt_exchange,
            req_max_position=parse_limit(cell(row, "maxNet")),
            req_max_order_qty_outright=parse_limit(cell(row, "maxOrder")),
            req_max_order_qty_spreads=None,  # a RIC sheet has no spread limit
            idx=idx,
        )
        for idx, row in enumerate(enriched.rows)
        if row.tt_symbol
    ]


# --- result rows -----------------------------------------------------------------


def fmt(value: Number | None) -> str:
    if value is None:
        return "Not set"
    return f"{value:,}"


@dataclass
class LimitVerification:
    format: SheetFormat
    account: Account
    rows: list[ResultRow]
    diff: DiffResult
    current: list[SystemLimit]
    enriched: EnrichedResult | None = None  # RIC sheets only
    conflicts: list[str] = field(default_factory=list)


def _product_label(diff_row: DiffRow, ric_row: EnrichedRow | None) -> str:
    if ric_row is not None:
        return f"{ric_row.ric_root} → {ric_row.tt_exchange} {ric_row.tt_symbol}"
    if diff_row.client is not None:
        return f"{diff_row.client.ts_ticker} ({diff_row.client.type})"
    system = diff_row.system
    assert system is not None
    return f"{system.family} ({system.exchange})"


def _metric_rows(
    node: str,
    account: Account,
    label: str,
    tt_name: str,
    diff_row: DiffRow,
    guessed: bool,
) -> list[ResultRow]:
    rows: list[ResultRow] = []
    for metric, cell in diff_row.cells().items():
        if cell.requested is None:
            continue  # nothing requested for this limit
        setting = f"{label}: {METRIC_LABELS[metric]}"
        base = {
            "node": node,
            "node_label": "Account",
            "rule_id": RULE_COMPARE,
            "setting": setting,
            "expected": fmt(cell.requested),
            "expected_level": "Account",
            "actual": fmt(cell.current),
            "actual_level": "Account" if diff_row.system is not None else "",
        }
        colour, reason, message = _judge(cell, account, tt_name, METRIC_LABELS[metric], diff_row)
        if guessed:
            colour, reason = Colour.AMBER, Reason.UNKNOWN
            message = (
                f"The TT product was guessed from the description ({tt_name}); confirm it with "
                "a mapping file. " + (message or "The limit matches the request.")
            )
        rows.append(ResultRow(**base, colour=colour, reason=reason, message=message))
    return rows


def _judge(
    cell: MetricCell, account: Account, tt_name: str, metric: str, diff_row: DiffRow
) -> tuple[Colour, Reason | None, str]:
    if cell.category == "same":
        return Colour.GREEN, None, ""
    if diff_row.system is None:
        return (
            Colour.RED,
            Reason.MISSING,
            f"There is no {tt_name} limit row on {account.name}. Add it with "
            f"{metric} = {fmt(cell.requested)}.",
        )
    if cell.category == "new":
        return (
            Colour.RED,
            Reason.MISSING,
            f"{metric} is not set on the {tt_name} limit row of {account.name}. "
            f"Set it to {fmt(cell.requested)}.",
        )
    direction = "higher" if cell.category == "increase" else "lower"
    return (
        Colour.RED,
        Reason.MISMATCH,
        f"Set {metric} for {tt_name} on {account.name} to {fmt(cell.requested)} "
        f"(TT has {fmt(cell.current)}; the request is {direction}).",
    )


def _to_result_rows(
    account: Account,
    diff: DiffResult,
    enriched: EnrichedResult | None,
) -> list[ResultRow]:
    node = account.display
    rows: list[ResultRow] = []

    if enriched is not None:
        for row in enriched.rows:
            if row.tt_symbol:
                continue
            hint = (
                "Upload TT's product list, or add a mapping for it."
                if row.status != "noTt"
                else "Upload TT's product list."
            )
            rows.append(
                ResultRow(
                    node=node,
                    node_label="Account",
                    rule_id=RULE_MAPPING,
                    setting=f"{row.ric_root or row.description}: TT product",
                    colour=Colour.AMBER,
                    expected="A TT exchange + symbol",
                    actual="Not found",
                    reason=Reason.UNKNOWN,
                    message=(
                        f"Could not find the TT product for {row.ric_root or row.description}"
                        f"{' on ' + row.exchange if row.exchange else ''}. {hint}"
                    ),
                )
            )

    for diff_row in diff.rows:
        ric_row = (
            enriched.rows[diff_row.client.idx]
            if enriched is not None and diff_row.client is not None
            else None
        )
        label = _product_label(diff_row, ric_row)
        if diff_row.category == "notSupported":
            rows.append(
                ResultRow(
                    node=node,
                    node_label="Account",
                    rule_id=RULE_NOT_SUPPORTED,
                    setting=label,
                    colour=Colour.AMBER,
                    reason=Reason.NO_RULE,
                    message=f"{label} is marked not supported (Supported = N); not checked.",
                )
            )
        elif diff_row.category == "systemOnly":
            system = diff_row.system
            assert system is not None
            rows.append(
                ResultRow(
                    node=node,
                    node_label="Account",
                    rule_id=RULE_NOT_REQUESTED,
                    setting=f"{system.family} ({system.exchange}, {system.type})",
                    colour=Colour.AMBER,
                    expected="Not in the requested sheet",
                    actual=(
                        f"Max Pos {fmt(system.cur_max_position)} · "
                        f"Order {fmt(system.cur_max_order_qty)} · "
                        f"Spread {fmt(system.cur_spread_max_order_qty)}"
                    ),
                    actual_level="Account",
                    reason=Reason.NO_RULE,
                    message=(
                        f"{account.name} has a {system.family} ({system.exchange}) limit row that "
                        "the requested sheet does not mention. Confirm it should stay."
                    ),
                )
            )
        else:
            client = diff_row.client
            assert client is not None
            tt_name = (
                f"{diff_row.system.family} ({diff_row.system.exchange})"
                if diff_row.system is not None
                else (
                    f"{client.ts_ticker} ({client.exchange})"
                    if client.exchange
                    else client.ts_ticker
                )
            )
            guessed = ric_row is not None and ric_row.status == "check"
            rows.extend(_metric_rows(node, account, label, tt_name, diff_row, guessed))
    return rows


def verify_limits(
    setup: SetupData,
    account_id: str,
    raw: RawTable,
    sheet_format: SheetFormat,
    cols: dict[str, str],
    *,
    product_list: Sequence[TtProduct] = (),
    ric_mappings: Sequence[RicMapping] = (),
    symbol_map: Sequence[SymbolMapEntry] = (),
) -> LimitVerification:
    """Compare a requested-limits sheet with one account's limit rows in the snapshot."""
    account = setup.tree.get(account_id)
    current = current_limits(setup, account.id)
    enriched: EnrichedResult | None = None
    if sheet_format == "ric":
        enriched = enrich(raw, cols, product_index(setup, product_list), ric_mappings)
        diff = compute_diff(_ric_requests(enriched, cols), current, exchange_aware=True)
    else:
        diff = compute_diff(normalize_client(raw, cols), current, symbol_map)
    return LimitVerification(
        format=sheet_format,
        account=account,
        rows=_to_result_rows(account, diff, enriched),
        diff=diff,
        current=current,
        enriched=enriched,
        conflicts=diff.conflicts,
    )


def mapping_table(enriched: EnrichedResult) -> list[dict[str, str]]:
    """How each RIC row was matched, for display and download."""
    return [
        {
            "RIC root": row.ric_root,
            "Exchange": row.exchange,
            "Description": row.description,
            "TT Exchange": row.tt_exchange,
            "TT Product Symbol": row.tt_symbol,
            "Match": row.status,
            "Alternatives": ", ".join(f"{p.exchange} {p.symbol}" for p in row.candidates),
        }
        for row in enriched.rows
    ]


def current_table(current: Sequence[SystemLimit]) -> list[dict[str, str]]:
    return [
        {
            "Exchange": s.exchange,
            "Product": s.family,
            "Type": s.type,
            "Product id": s.product,
            METRIC_LABELS["maxPosition"]: fmt(s.cur_max_position),
            METRIC_LABELS["maxOrderQtyOutright"]: fmt(s.cur_max_order_qty),
            METRIC_LABELS["maxOrderQtySpreads"]: fmt(s.cur_spread_max_order_qty),
        }
        for s in current
    ]
