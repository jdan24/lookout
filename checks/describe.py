"""Score how well a product description matches a TT product name.

Ported from limit_compare's RIC tool (``describe.ts``). Token sets keep insertion order
(as JavaScript Sets do), so scores come out bit-for-bit the same.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from checks.tabular import canon
from checks.tt_products import TtProduct

NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "twelve": "12",
    "twenty": "20", "thirty": "30",
}  # fmt: skip

CURRENCIES = {
    "aud": "australian dollar", "gbp": "british pound", "cad": "canadian dollar",
    "eur": "euro", "jpy": "japanese yen", "chf": "swiss franc", "mxn": "mexican peso",
    "nzd": "new zealand dollar", "zar": "rand", "brl": "brazilian real",
    "cnh": "chinese renminbi", "inr": "indian rupee", "sek": "swedish krona",
    "nok": "norwegian krone", "krw": "korean won",
}  # fmt: skip

_A = re.ASCII  # JavaScript's \b and \d are ASCII-only
REWRITES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bu\.?\s?s\.?\b", _A), "us"),
    (re.compile(r"\bt\s?notes?\b", _A), "treasury note"),
    (re.compile(r"\bt\s?bonds?\b", _A), "treasury bond"),
    (re.compile(r"\b(\d+)\s*(?:yrs?|years?|y)\b", _A), r"\1 year"),
    (re.compile(r"\b(\d+)\s*(?:mths?|months?|mo|m)\b", _A), r"\1 month"),
    (re.compile(r"\bmths?\b", _A), "month"),
    (re.compile(r"\byrs?\b", _A), "year"),
    (re.compile(r"\beurostoxx\b", _A), "euro stoxx"),
    (re.compile(r"\bstoxx\s?50\b", _A), "stoxx 50"),
    (re.compile(r"\bnat\b", _A), "natural"),
    (re.compile(r"\blow sulph?ur\b", _A), "ls"),
    (re.compile(r"\bgasoil\b", _A), "gas oil"),
    (re.compile(r"\bhard red winter\b", _A), "hrw"),
    (re.compile(r"\bsoft red winter\b", _A), "srw"),
    (re.compile(r"\blong term\b", _A), "long"),
    (re.compile(r"\bshort term\b", _A), "short"),
    (re.compile(r"\bh shares?\b", _A), "h share"),
]

STOP = frozenset(
    "future futures fut ftr index the of and on monthly cross currency contract stnd iceu "
    "icsg icus nycc usd denominated sfe asx cboe cme eurex ice hkex sgx".split()
)

VARIANT = frozenset(
    "tas tic tac tam mini micro emicro weekly week dividend option options opt daily day mm "
    "md esg quanto variance trf etf balmo swap swapnote average calendar spread crack diff "
    "sri total return mid curve test basis adjusted ntr gtr climate flex financial last 1st "
    "line invoice btic pab reit capped equally weighted rolling inverse leveraged ultra "
    "e-mini emini".split()
)

_PUNCT = re.compile(r"[-/(),:;'\"+]")
_SPACES = re.compile(r"\s+")
_DOTS = re.compile(r"^\.+|\.+$")
_NUMBER = re.compile(r"^\d+$", _A)


def _stem(token: str) -> str:
    """Cheap singular: soybeans -> soybean, notes -> note (keeps "ss" and short words)."""
    return (
        token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
    )


def tokens(text: str) -> list[str]:
    """Identity-bearing tokens of a description or product name."""
    s = f" {text.lower()} "
    s = _SPACES.sub(" ", _PUNCT.sub(" ", s))
    for pattern, replacement in REWRITES:
        s = pattern.sub(replacement, s)
    out: list[str] = []
    for raw in s.split(" "):
        if not raw:
            continue
        word = _DOTS.sub("", raw)
        if not word:
            continue
        if word in NUMBER_WORDS:
            out.append(NUMBER_WORDS[word])
        elif word in CURRENCIES:
            out.extend(CURRENCIES[word].split(" "))
        elif word not in STOP:
            out.append(_stem(word))
    return out


def token_set(text: str) -> dict[str, None]:
    """Tokens as an insertion-ordered set."""
    return dict.fromkeys(tokens(text))


@lru_cache(maxsize=65536)
def _name_tokens(name: str) -> dict[str, None]:
    return token_set(name)


def name_score(desc_tokens: dict[str, None], product: TtProduct) -> float:
    """Roughly 0..1 (can go negative): share of description tokens found in the name,
    minus penalties for variant words, unrelated numbers and extra words in the name."""
    if not desc_tokens:
        return 0
    name = _name_tokens(product.name)
    if not name:
        return 0
    overlap = sum(1 for t in desc_tokens if t in name)
    score = overlap / len(desc_tokens)
    for t in name:
        if t in desc_tokens:
            continue
        if t in VARIANT or _NUMBER.match(t):
            score -= 0.25
        else:
            score -= 0.03
    return score


def _symbol_penalty(product: TtProduct) -> float:
    """Variant / test product codes ("C_Z", "BDE S") are rarely the outright."""
    return 0.3 if re.search(r"[_\s]", product.symbol) else 0


@dataclass(frozen=True)
class Scored:
    product: TtProduct
    name: float  # name fit only
    total: float  # name fit plus symbol bonus / penalty: the ranking score


def rank_by_description(
    description: str, ric_root: str, products: Iterable[TtProduct], limit: int = 5
) -> list[Scored]:
    desc = token_set(description)
    ric = canon(ric_root)
    scored: list[Scored] = []
    for product in products:
        name = name_score(desc, product)
        exact = bool(ric) and (
            canon(product.symbol) == ric or canon(product.alternate_symbol) == ric
        )
        total = name + (0.35 if exact else 0) - _symbol_penalty(product)
        if total > 0:
            scored.append(Scored(product=product, name=name, total=total))
    scored.sort(key=lambda s: (-s.total, len(s.product.symbol)))
    return scored[:limit]
