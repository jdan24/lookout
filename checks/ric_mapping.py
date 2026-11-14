"""Match a pasted RIC-based limits sheet to TT exchange + product symbol.

Ported from limit_compare's RIC tool (``fields.ts``, ``exchanges.ts``,
``enrich.ts``, ``mappingCsv.ts``). Behaviour is kept identical; parity tests compare
the results with the original TypeScript on the same inputs.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from checks.describe import name_score, rank_by_description, token_set
from checks.ric_reference import EXCHANGE_ALIASES, KNOWN_MAPPINGS
from checks.tabular import FieldSpec, RawTable, canon
from checks.tt_products import TtIndex, TtProduct

TT_EXCHANGE_COL = "TT Exchange"
TT_SYMBOL_COL = "TT Product Symbol"

RIC_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("ricRoot", "Ric Root",
              ("Ric Root", "RIC Root", "RICRoot", "RIC", "Ric", "Reuters Root", "RIC Code"), True),
    FieldSpec("maxOrder", "Max Order",
              ("Max Order", "Max Order Qty", "Max Order Quantity", "Max Order Size"), True),
    FieldSpec("maxNet", "Max Net",
              ("Max Net", "Max net", "Max Net Position", "Max Net Pos", "Max Position"), True),
    FieldSpec("exchange", "Exchange", ("Exchange", "Exch", "Exchange Name", "Market"), False),
    FieldSpec("description", "Product Description",
              ("Product Description", "Description", "Product", "Product Name"), False),
)  # fmt: skip

MatchStatus = Literal["mapped", "known", "direct", "check", "unmatched", "noTt"]
STATUSES: tuple[MatchStatus, ...] = ("mapped", "known", "direct", "check", "unmatched", "noTt")

MIN_GUESS = 0.45  # minimum description score for a "check" best guess
OVERRIDE_MARGIN = 0.3  # a different product must beat an exact-symbol hit by this much
MAX_CANDIDATES = 6


@dataclass(frozen=True)
class RicMapping:
    """A RIC root (+ the row's exchange; blank = any exchange) -> TT product."""

    ric_root: str
    exchange: str
    tt_exchange: str
    tt_symbol: str


@dataclass(frozen=True)
class EnrichedRow:
    ric_root: str
    exchange: str
    description: str
    cells: dict[str, str]
    tt_exchange: str
    tt_symbol: str
    status: MatchStatus
    candidates: list[TtProduct] = field(default_factory=list)


@dataclass(frozen=True)
class EnrichedResult:
    columns: list[str]
    rows: list[EnrichedRow]
    counts: dict[MatchStatus, int]


def mapping_key(ric_root: str, exchange: str) -> str:
    return f"{canon(ric_root)}|{canon(exchange)}"


def tt_exchanges_for(exchange: str, known_tt_exchanges: Iterable[str]) -> list[str] | None:
    """TT exchanges a pasted Exchange value may mean. None = blank or unknown: don't filter."""
    c = canon(exchange)
    if not c:
        return None
    if c in EXCHANGE_ALIASES:
        return EXCHANGE_ALIASES[c]
    for tt in known_tt_exchanges:
        if canon(tt) == c:
            return [tt]
    return None


def output_columns(headers: Sequence[str], cols: dict[str, str]) -> list[str]:
    """The pasted headers with the two TT columns inserted before the first limit column."""
    limit_idx = [
        headers.index(h) for h in (cols.get("maxOrder"), cols.get("maxNet")) if h and h in headers
    ]
    base = [h for h in headers if h not in (TT_EXCHANGE_COL, TT_SYMBOL_COL)]
    at = min(limit_idx) if limit_idx else len(headers)
    anchor = headers[at] if at < len(headers) else None
    pos = base.index(anchor) if anchor is not None and anchor in base else len(base)
    return [*base[:pos], TT_EXCHANGE_COL, TT_SYMBOL_COL, *base[pos:]]


@dataclass(frozen=True)
class _Pick:
    exchange: str
    symbol: str

    @classmethod
    def of(cls, product: TtProduct) -> _Pick:
        return cls(product.exchange, product.symbol)


def _unique(products: Iterable[TtProduct | None]) -> list[TtProduct]:
    """First occurrence of each product object (by identity, as the original does)."""
    found: list[TtProduct] = []
    for product in products:
        if product is not None and not any(product is seen for seen in found):
            found.append(product)
    return found


def enrich(
    raw: RawTable,
    cols: dict[str, str],
    tt: TtIndex | None,
    mappings: Sequence[RicMapping] = (),
    *,
    use_known: bool = True,
) -> EnrichedResult:
    """Find each row's TT product, in priority order:

    1. your saved mapping (RIC root + this exchange, then RIC root + any exchange);
    2. the curated RIC -> TT table, if its TT exchange fits the row;
    3. a TT product whose symbol equals the RIC root on the row's exchange and whose
       name fits the description -> "direct";
    4. the TT product whose name best fits the description -> "check", unless the fit
       is too weak -> "unmatched".
    """
    columns = output_columns(raw.headers, cols)
    counts: dict[MatchStatus, int] = dict.fromkeys(STATUSES, 0)
    user = {mapping_key(m.ric_root, m.exchange): m for m in mappings}

    def cell(row: dict[str, str], name: str) -> str:
        header = cols.get(name)
        return (row.get(header) or "").strip() if header else ""

    rows: list[EnrichedRow] = []
    for cells in raw.rows:
        ric_root = cell(cells, "ricRoot")
        exchange = cell(cells, "exchange")
        description = cell(cells, "description")
        status, picked, candidates = _resolve(ric_root, exchange, description, tt, user, use_known)
        counts[status] += 1
        rows.append(
            EnrichedRow(
                ric_root=ric_root,
                exchange=exchange,
                description=description,
                cells=cells,
                tt_exchange=picked.exchange if picked else "",
                tt_symbol=picked.symbol if picked else "",
                status=status,
                candidates=candidates,
            )
        )
    return EnrichedResult(columns=columns, rows=rows, counts=counts)


def _resolve(
    ric_root: str,
    exchange: str,
    description: str,
    tt: TtIndex | None,
    user: dict[str, RicMapping],
    use_known: bool,
) -> tuple[MatchStatus, _Pick | None, list[TtProduct]]:
    if tt is None:
        return "noTt", None, []
    if not ric_root and not description:
        return "unmatched", None, []

    mapped = user.get(mapping_key(ric_root, exchange)) or user.get(mapping_key(ric_root, ""))
    if mapped:
        return "mapped", _Pick(mapped.tt_exchange, mapped.tt_symbol), []

    allowed = tt_exchanges_for(exchange, tt.exchanges)

    def fits(tt_exchange: str) -> bool:
        return allowed is None or tt_exchange in allowed

    if use_known:
        for ric, tt_exchange, tt_symbol in KNOWN_MAPPINGS:
            if (
                canon(ric) == canon(ric_root)
                and fits(tt_exchange)
                and tt.has(tt_exchange, tt_symbol)
            ):
                return "known", _Pick(tt_exchange, tt_symbol), []

    pool = [p for x in allowed for p in tt.by_exchange.get(x, [])] if allowed else tt.products
    exact = [
        p for p in (tt.by_symbol.get(canon(ric_root), []) if ric_root else []) if fits(p.exchange)
    ]
    primary = [p for p in exact if canon(p.symbol) == canon(ric_root)]
    if primary:
        exact = primary

    ranked = rank_by_description(description, ric_root, pool, MAX_CANDIDATES) if description else []

    def alternatives(first: TtProduct | None = None) -> list[TtProduct]:
        return _unique([first, *(r.product for r in ranked), *exact])[:MAX_CANDIDATES]

    if exact:
        if not description:
            if len(exact) == 1:
                return "direct", _Pick.of(exact[0]), []
            return "check", _Pick.of(exact[0]), alternatives(exact[0])
        desc = token_set(description)
        scored = sorted(((p, name_score(desc, p)) for p in exact), key=lambda ps: -ps[1])
        best, best_score = scored[0]
        top = ranked[0] if ranked else None
        # A clearly better-named product beats a coincidental symbol match.
        if top and top.product is not best and top.name >= best_score + OVERRIDE_MARGIN:
            return "check", _Pick.of(top.product), alternatives(top.product)
        if best_score >= 0.34 or len(exact) == 1:
            return "direct", _Pick.of(best), []
        return "check", _Pick.of(best), alternatives(best)

    top = ranked[0] if ranked else None
    if top and top.total >= MIN_GUESS:
        return "check", _Pick.of(top.product), alternatives(top.product)
    elsewhere = tt.by_symbol.get(canon(ric_root), []) if ric_root else []
    return "unmatched", None, _unique([*alternatives(), *elsewhere])[:MAX_CANDIDATES]


# --- mapping file ----------------------------------------------------------------

MAPPING_HEADERS = ("ricRoot", "exchange", "ttExchange", "ttSymbol")


def parse_mappings_csv(text: str) -> tuple[list[RicMapping], int]:
    """A mapping CSV (headers matched loosely). Returns (mappings, rows skipped)."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""))
    records = [r for r in reader if any(c.strip() for c in r)]
    if not records:
        return [], 0
    header = [canon(h) for h in records[0]]
    mappings: list[RicMapping] = []
    skipped = 0
    for record in records[1:]:
        values = {h: (record[i].strip() if i < len(record) else "") for i, h in enumerate(header)}

        def get(key: str, values: dict[str, str] = values) -> str:
            return values.get(canon(key), "")

        mapping = RicMapping(get("ricRoot"), get("exchange"), get("ttExchange"), get("ttSymbol"))
        if mapping.ric_root and mapping.tt_exchange and mapping.tt_symbol:
            mappings.append(mapping)
        else:
            skipped += 1
    return mappings, skipped


def mappings_to_csv(mappings: Sequence[RicMapping]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(MAPPING_HEADERS)
    for m in mappings:
        writer.writerow([m.ric_root, m.exchange, m.tt_exchange, m.tt_symbol])
    return out.getvalue()
