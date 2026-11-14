"""TT's public futures product list, indexed for matching RIC roots to TT symbols.

Ported from limit_compare's RIC tool (``tt.ts``). The list is TT's ExchangeProducts CSV,
which the user downloads and uploads; Lookout never fetches it (its host is not on
the read-only allow-list).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from checks.tabular import canon

REQUIRED_COLUMNS = ("Exchange", "Product Symbol", "Product Type")
_DELAYED = re.compile(r"_Delayed$", re.IGNORECASE)


@dataclass(frozen=True, eq=False)
class TtProduct:
    exchange: str
    symbol: str
    alternate_symbol: str = ""
    name: str = ""


class ProductListError(ValueError):
    """The file is not a TT ExchangeProducts CSV."""


def parse_tt_csv(text: str) -> list[TtProduct]:
    """Futures only; delayed-data venues are skipped."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿"), newline=""))
    reader.fieldnames = [h.strip() for h in reader.fieldnames or []]
    for need in REQUIRED_COLUMNS:
        if need not in reader.fieldnames:
            raise ProductListError(f'Not a TT ExchangeProducts CSV: missing "{need}" column.')

    def get(row: dict[str, str | None], key: str) -> str:
        return (row.get(key) or "").strip()

    products: list[TtProduct] = []
    for row in reader:
        if get(row, "Product Type") != "Future":
            continue
        exchange, symbol = get(row, "Exchange"), get(row, "Product Symbol")
        if not exchange or not symbol or _DELAYED.search(exchange):
            continue
        products.append(
            TtProduct(
                exchange=exchange,
                symbol=symbol,
                alternate_symbol=get(row, "Alternate Symbol"),
                name=get(row, "Product Name"),
            )
        )
    if not products:
        raise ProductListError("No futures found in the TT file.")
    return products


# --- ordering like JavaScript's String.localeCompare (ICU root collation) -------------

# ICU puts whitespace and punctuation first, then symbols, then digits, then letters,
# and compares letters without case first (lowercase before uppercase on a tie).
_ICU_PUNCTUATION = " _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$"
_PUNCT_RANK = {ch: i for i, ch in enumerate(_ICU_PUNCTUATION)}


def _collation_key(text: str) -> tuple:
    primary = []
    for ch in text:
        low = ch.lower()
        if ch in _PUNCT_RANK:
            primary.append((0, _PUNCT_RANK[ch], ""))
        elif ch.isdigit():
            primary.append((1, int(ch) if ch.isascii() else 0, ch))
        elif ch.isalpha():
            primary.append((2, 0, low))
        else:
            primary.append((0, len(_PUNCT_RANK), ch))
    tertiary = tuple(ch.isupper() for ch in text)
    return (tuple(primary), tertiary)


@dataclass
class TtIndex:
    products: list[TtProduct]
    by_symbol: dict[str, list[TtProduct]] = field(default_factory=dict)  # canon symbol/alt
    by_exchange_symbol: dict[str, TtProduct] = field(default_factory=dict)  # "EX|canon"
    by_exchange: dict[str, list[TtProduct]] = field(default_factory=dict)  # sorted by symbol

    @property
    def exchanges(self) -> list[str]:
        """Exchange names in first-seen order."""
        return list(self.by_exchange)

    def has(self, exchange: str, symbol: str) -> bool:
        return f"{exchange}|{canon(symbol)}" in self.by_exchange_symbol


def build_tt_index(products: Iterable[TtProduct]) -> TtIndex:
    index = TtIndex(products=list(products))

    def add(key: str, product: TtProduct) -> None:
        if not key:
            return
        bucket = index.by_symbol.setdefault(key, [])
        if not any(product is seen for seen in bucket):  # identity, as the original
            bucket.append(product)

    for product in index.products:
        add(canon(product.symbol), product)
        add(canon(product.alternate_symbol), product)
        index.by_exchange_symbol[f"{product.exchange}|{canon(product.symbol)}"] = product
        index.by_exchange.setdefault(product.exchange, []).append(product)
    for bucket in index.by_exchange.values():
        bucket.sort(key=lambda p: _collation_key(p.symbol))
    return index
