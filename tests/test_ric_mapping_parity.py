"""The Python RIC -> TT matcher gives the same answers as limit_compare's TypeScript."""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from checks.describe import name_score, rank_by_description, token_set, tokens
from checks.ric_mapping import (
    RIC_FIELDS,
    EnrichedResult,
    RicMapping,
    enrich,
    mappings_to_csv,
    output_columns,
    parse_mappings_csv,
    tt_exchanges_for,
)
from checks.tabular import auto_detect, is_complete, parse_pasted_table
from checks.tt_products import (
    ProductListError,
    TtProduct,
    build_tt_index,
    parse_tt_csv,
)

FIXTURES = Path(__file__).parent / "fixtures" / "limit_compare"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected_outputs.json").read_text("utf-8"))
FULL_CSV = os.environ.get("LOOKOUT_TT_PRODUCTS_CSV", "")


def paste_sample():
    return parse_pasted_table((FIXTURES / "ric_paste_sample.tsv").read_text("utf-8"))


@cache
def sample_index():
    return build_tt_index(parse_tt_csv((FIXTURES / "tt_products_sample.csv").read_text("utf-8")))


def as_expected(result: EnrichedResult) -> dict[str, Any]:
    return {
        "columns": result.columns,
        "counts": result.counts,
        "rows": [
            {
                "ricRoot": r.ric_root,
                "exchange": r.exchange,
                "status": r.status,
                "ttExchange": r.tt_exchange,
                "ttSymbol": r.tt_symbol,
                "candidates": [f"{p.exchange} {p.symbol}" for p in r.candidates],
            }
            for r in result.rows
        ],
    }


# --- parsing ---------------------------------------------------------------------


def test_paste_and_column_detection() -> None:
    raw = paste_sample()
    want = EXPECTED["ric_paste"]
    assert raw.headers == want["headers"]
    assert raw.rows == want["rows"]
    detected = auto_detect(RIC_FIELDS, raw.headers)
    assert detected.mapping == want["mapping"]
    assert is_complete(RIC_FIELDS, detected.mapping)


def test_csv_paste_also_parses() -> None:
    raw = parse_pasted_table("Ric Root,Max Order,Max Net\nTY,1,2\n")
    assert raw.headers == ["Ric Root", "Max Order", "Max Net"]
    assert raw.rows == [{"Ric Root": "TY", "Max Order": "1", "Max Net": "2"}]


def test_sample_product_list() -> None:
    products = parse_tt_csv((FIXTURES / "tt_products_sample.csv").read_text("utf-8"))
    want = [
        TtProduct(p["exchange"], p["symbol"], p["alternateSymbol"], p["name"])
        for p in EXPECTED["ric_sample_products"]
    ]
    assert [vars(p) for p in products] == [vars(p) for p in want]
    assert not any(p.exchange == "CME_Delayed" for p in products)


def test_non_tt_csv_is_rejected() -> None:
    with pytest.raises(ProductListError):
        parse_tt_csv("a,b\n1,2\n")
    with pytest.raises(ProductListError, match="No futures"):
        parse_tt_csv("Exchange,Product Symbol,Product Type\nCME,ZN,Option\n")


# --- matching building blocks ----------------------------------------------------


def test_tokens() -> None:
    for case in EXPECTED["ric_tokens"]:
        assert tokens(case["text"]) == case["tokens"], case["text"]


def test_name_scores_are_identical() -> None:
    products = {(p.exchange, p.symbol, p.name): p for p in sample_index().products}
    by_key: dict[tuple[str, str], list[TtProduct]] = {}
    for p in products.values():
        by_key.setdefault((p.exchange, p.symbol), []).append(p)
    checked = 0
    for case in EXPECTED["ric_name_scores"]:
        candidates = by_key[(case["exchange"], case["symbol"])]
        scores = {name_score(token_set(case["text"]), p) for p in candidates}
        assert case["score"] in scores, case
        checked += 1
    assert checked == len(EXPECTED["ric_name_scores"]) == 440


def test_ranking() -> None:
    products = sample_index().products
    for case in EXPECTED["ric_rank"]:
        got = [
            {"p": f"{s.product.exchange} {s.product.symbol}", "name": s.name, "total": s.total}
            for s in rank_by_description(case["text"], "", products, 5)
        ]
        assert got == case["ranked"], case["text"]


def test_exchange_aliases() -> None:
    known = sample_index().exchanges
    for case in EXPECTED["ric_exchanges"]:
        assert tt_exchanges_for(case["exchange"], known) == case["tt"], case["exchange"]


def test_output_columns() -> None:
    for case in EXPECTED["ric_output_columns"]:
        assert output_columns(case["headers"], case["cols"]) == case["out"]


def test_mapping_csv() -> None:
    text = 'Ric Root,Exchange,TT Exchange,TT Symbol\r\nFLG,"ICE, Europe",ICE_L,R\r\nX,,,\r\n'
    mappings, skipped = parse_mappings_csv(text)
    want = EXPECTED["ric_mapping_csv"]
    assert skipped == want["skipped"]
    assert [vars(m) for m in mappings] == [
        {"ric_root": m["ricRoot"], "exchange": m["exchange"], "tt_exchange": m["ttExchange"],
         "tt_symbol": m["ttSymbol"]}
        for m in want["mappings"]
    ]  # fmt: skip
    round_trip, _ = parse_mappings_csv(mappings_to_csv(mappings))
    assert round_trip == mappings


# --- end to end ------------------------------------------------------------------


def test_enrich_default() -> None:
    raw = paste_sample()
    cols = auto_detect(RIC_FIELDS, raw.headers).mapping
    assert as_expected(enrich(raw, cols, sample_index())) == EXPECTED["ric_enrich"]["default"]


def test_enrich_without_the_curated_table() -> None:
    raw = paste_sample()
    cols = auto_detect(RIC_FIELDS, raw.headers).mapping
    got = as_expected(enrich(raw, cols, sample_index(), use_known=False))
    assert got == EXPECTED["ric_enrich"]["no_known"]


def test_enrich_with_user_mappings() -> None:
    raw = paste_sample()
    cols = auto_detect(RIC_FIELDS, raw.headers).mapping
    mappings = [
        RicMapping(m["ricRoot"], m["exchange"], m["ttExchange"], m["ttSymbol"])
        for m in EXPECTED["ric_user_mappings"]
    ]
    assert (
        as_expected(enrich(raw, cols, sample_index(), mappings)) == EXPECTED["ric_enrich"]["mapped"]
    )


def test_enrich_without_a_product_list() -> None:
    raw = paste_sample()
    cols = auto_detect(RIC_FIELDS, raw.headers).mapping
    assert as_expected(enrich(raw, cols, None)) == EXPECTED["ric_enrich"]["no_tt"]


@cache
def full_index():
    return build_tt_index(parse_tt_csv(Path(FULL_CSV).expanduser().read_text("utf-8")))


needs_full_csv = pytest.mark.skipif(
    not FULL_CSV, reason="set LOOKOUT_TT_PRODUCTS_CSV to TT's full ExchangeProducts CSV"
)


@needs_full_csv
def test_real_world_sheet_with_the_full_tt_list() -> None:
    want = EXPECTED["ric_real_world"]
    assert len(full_index().products) == want["futures"]
    raw = parse_pasted_table((FIXTURES / "ric_real_world_sample.tsv").read_text("utf-8"))
    assert raw.rows == want["rows"]
    cols = auto_detect(RIC_FIELDS, raw.headers).mapping
    assert cols == want["mapping"]

    full = as_expected(enrich(raw, cols, full_index()))
    assert full == want["default"]
    correct = sum(
        f"{r['ttExchange']} {r['ttSymbol']}"
        == f"{row['Expected TT Exchange']} {row['Expected TT Symbol']}"
        for r, row in zip(full["rows"], raw.rows, strict=True)
    )
    assert correct == len(raw.rows) == 53  # every answer checked against TT's product names

    assert as_expected(enrich(raw, cols, full_index(), use_known=False)) == want["no_known"]


@needs_full_csv
def test_paste_sample_with_the_full_tt_list() -> None:
    raw = paste_sample()
    cols = auto_detect(RIC_FIELDS, raw.headers).mapping
    got = as_expected(enrich(raw, cols, full_index()))
    assert got == EXPECTED["ric_real_world"]["paste_sample_full"]


@needs_full_csv
def test_every_curated_mapping_exists_in_tt() -> None:
    from checks.ric_reference import KNOWN_MAPPINGS

    missing = [m for m in KNOWN_MAPPINGS if not full_index().has(m[1], m[2])]
    assert missing == []
