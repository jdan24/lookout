"""The Python requested-vs-current compare gives the same answers as limit_compare."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from checks.limit_compare import (
    CLIENT_FIELDS,
    SYSTEM_FIELDS,
    DiffResult,
    SymbolMapEntry,
    compute_diff,
    normalize_client,
    normalize_system,
    parse_limit,
    parse_symbol_map,
)
from checks.tabular import RawTable, auto_detect, read_csv_bytes, read_xlsx_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "limit_compare"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected_outputs.json").read_text("utf-8"))


def inputs():
    client_raw = read_xlsx_bytes(
        (FIXTURES / "client_sample.xlsx").read_bytes(), "client_sample.xlsx"
    )
    system_raw = read_csv_bytes((FIXTURES / "system_sample.csv").read_bytes(), "system_sample.csv")
    c_map = auto_detect(CLIENT_FIELDS, client_raw.headers).mapping
    s_map = auto_detect(SYSTEM_FIELDS, system_raw.headers).mapping
    return client_raw, system_raw, c_map, s_map


def as_expected(diff: DiffResult) -> dict[str, Any]:
    def cell(c):  # noqa: ANN001, ANN202
        return {"current": c.current, "requested": c.requested, "category": c.category}

    return {
        "counts": diff.counts,
        "unmatched": [r.key for r in diff.unmatched],
        "rows": [
            {
                "key": r.key,
                "category": r.category,
                "matchMethod": r.match_method,
                "tsTicker": r.client.ts_ticker if r.client else None,
                "family": r.system.family if r.system else None,
                "account": r.account,
                "maxPosition": cell(r.max_position),
                "maxOrderQtyOutright": cell(r.max_order_qty_outright),
                "maxOrderQtySpreads": cell(r.max_order_qty_spreads),
            }
            for r in diff.rows
        ],
    }


def test_reading_and_column_detection() -> None:
    client_raw, system_raw, c_map, s_map = inputs()
    assert client_raw.headers == EXPECTED["qe_client_raw"]["headers"]
    assert client_raw.rows == EXPECTED["qe_client_raw"]["rows"]
    assert system_raw.headers == EXPECTED["qe_system_raw"]["headers"]
    assert c_map == EXPECTED["qe_mappings"]["client"]
    assert s_map == EXPECTED["qe_mappings"]["system"]


def test_normalised_records() -> None:
    client_raw, system_raw, c_map, s_map = inputs()
    clients = normalize_client(client_raw, c_map)
    want = EXPECTED["qe_clients"]
    assert [(c.ts_ticker, c.type, c.not_supported, c.idx) for c in clients] == [
        (w["tsTicker"], w["type"], w["notSupported"], w["idx"]) for w in want
    ]
    assert [
        (c.req_max_position, c.req_max_order_qty_outright, c.req_max_order_qty_spreads)
        for c in clients
    ] == [
        (w["reqMaxPosition"], w["reqMaxOrderQtyOutright"], w["reqMaxOrderQtySpreads"]) for w in want
    ]
    systems = normalize_system(system_raw, s_map)
    assert [
        (s.account, s.family, s.type, s.cur_max_position, s.cur_max_order_qty, s.cur_spread_max_order_qty)
        for s in systems
    ] == [
        (w["account"], w["family"], w["type"], w["curMaxPosition"], w["curMaxOrderQty"],
         w["curSpreadMaxOrderQty"])
        for w in EXPECTED["qe_systems"]
    ]  # fmt: skip


def test_diff_without_symbol_map() -> None:
    client_raw, system_raw, c_map, s_map = inputs()
    diff = compute_diff(normalize_client(client_raw, c_map), normalize_system(system_raw, s_map))
    assert as_expected(diff) == EXPECTED["qe_diff"]["no_map"]


def test_diff_with_symbol_map() -> None:
    client_raw, system_raw, c_map, s_map = inputs()
    symbol_map = parse_symbol_map(
        read_csv_bytes((FIXTURES / "symbol_map_sample.csv").read_bytes(), "symbol_map_sample.csv")
    )
    assert symbol_map == [SymbolMapEntry("GBL", "Future", "FGBL")]
    diff = compute_diff(
        normalize_client(client_raw, c_map), normalize_system(system_raw, s_map), symbol_map
    )
    assert as_expected(diff) == EXPECTED["qe_diff"]["with_map"]


def test_parse_limit() -> None:
    for case in EXPECTED["qe_parse_limit"]:
        assert parse_limit(case["text"]) == case["value"], case["text"]


def test_exchange_aware_matching_keeps_same_symbols_on_different_exchanges_apart() -> None:
    from checks.limit_compare import ClientProduct, SystemLimit

    systems = [
        SystemLimit("G", "Future", 100, 10, None, account="POD", exchange="ICE"),
        SystemLimit("G", "Future", 200, 20, None, account="POD", exchange="ICE_L"),
    ]
    request = ClientProduct("G", "Future", 200, 20, None, exchange="ICE_L")
    plain = compute_diff([request], systems)
    assert plain.conflicts == ["G / Future"]  # the original cannot tell them apart
    aware = compute_diff([request], systems, exchange_aware=True)
    assert aware.conflicts == []
    assert aware.rows[0].system is systems[1]
    assert aware.rows[0].category == "unchanged"
    assert [r.system.exchange for r in aware.rows if r.category == "systemOnly"] == ["ICE"]
    assert RawTable  # imported for type completeness
