from __future__ import annotations

from pathlib import Path

import pytest

from checks import limit_verification as lv
from checks.limit_compare import SymbolMapEntry
from checks.ric_mapping import RicMapping
from checks.tabular import parse_pasted_table
from checks.tt_products import TtProduct
from core.results import ResultRow
from core.setup_model import SetupData, load_setup
from tests.fixtures import synthetic

# CLI-ALPHA (200) has ZN (CME), FGBL (Eurex) and TFM (ICE): maxPos 100, order 10, spread 20.
RIC_SHEET = """Exch\tRIC\tDescription\tMax Order\tMax Net
CBT\tTY\tUS 10yr Note\t10\t100
EUX\tFGBL\tEuro Bund\t10\t200
ICE\tTFMBM\tDutch TTF\t5\t100
CBT\tC\tCorn\t1\t1
"""

UNIVERSE_SHEET = """TS Ticker,Type,Desk Supported,Outrights/Spreads Max Position,Max Order Quantity (Outright),Max Order Quantity (Spreads)
ZN,Future,Y,100,10,20
GBL,Future,Y,100,10,30
KE,Future,N,1,1,1
ZZZ,Future,Y,5,5,5
"""


@pytest.fixture
def setup(tmp_path: Path) -> SetupData:
    return load_setup(synthetic.build_snapshot(tmp_path))


def outcomes(rows: list[ResultRow]) -> dict[tuple[str, str], tuple[str, str]]:
    got = {
        (r.rule_id, r.setting): (r.colour.value, r.reason.value if r.reason else "") for r in rows
    }
    assert len(got) == len(rows)
    return got


def run(setup: SetupData, text: str, **kwargs) -> lv.LimitVerification:  # noqa: ANN003
    raw = parse_pasted_table(text)
    sheet_format, cols = lv.detect_format(raw)
    assert sheet_format is not None
    return lv.verify_limits(setup, "200", raw, sheet_format, cols, **kwargs)


def test_snapshot_side(setup: SetupData) -> None:
    accounts = [(a.name, n) for a, n in lv.accounts_with_limits(setup)]
    assert accounts == [
        ("CB-CLI-ALPHA", 2),
        ("CB-CLI-BETA", 2),
        ("CLI-ALPHA", 3),
        ("CLI-BETA", 4),
        ("CLI-GAMMA", 1),
    ]
    current = lv.current_limits(setup, "200")
    assert [(s.exchange, s.family, s.type, s.account) for s in current] == [
        ("CME", "ZN", "Future", "CLI-ALPHA"),
        ("Eurex", "FGBL", "Future", "CLI-ALPHA"),
        ("ICE", "TFM", "Future", "CLI-ALPHA"),
    ]
    assert (current[0].cur_max_position, current[0].cur_max_order_qty) == (100, 10)
    assert current[0].cur_spread_max_order_qty == 20
    assert lv.product_type_name(34) == "Future" and lv.product_type_name(77) == "type 77"


def test_format_detection() -> None:
    assert lv.detect_format(parse_pasted_table(RIC_SHEET))[0] == "ric"
    assert lv.detect_format(parse_pasted_table(UNIVERSE_SHEET))[0] == "universe"
    fmt, cols = lv.detect_format(parse_pasted_table("RIC\tMax Order\nTY\t1\n"))
    assert fmt is None and cols == {"ricRoot": "RIC", "maxOrder": "Max Order"}


def test_ric_sheet(setup: SetupData) -> None:
    result = run(setup, RIC_SHEET)
    assert result.format == "ric"
    assert [(r.ric_root, r.status, r.tt_exchange, r.tt_symbol) for r in result.enriched.rows] == [
        ("TY", "known", "CME", "ZN"),
        ("FGBL", "known", "Eurex", "FGBL"),
        ("TFMBM", "known", "ICE", "TFM"),
        ("C", "unmatched", "", ""),
    ]
    assert outcomes(result.rows) == {
        ("LV2", "C: TT product"): ("amber", "UNKNOWN"),
        ("LV1", "TY → CME ZN: Max Position Product (net)"): ("green", ""),
        ("LV1", "TY → CME ZN: Outright max order quantity"): ("green", ""),
        ("LV1", "FGBL → Eurex FGBL: Max Position Product (net)"): ("red", "MISMATCH"),
        ("LV1", "FGBL → Eurex FGBL: Outright max order quantity"): ("green", ""),
        ("LV1", "TFMBM → ICE TFM: Max Position Product (net)"): ("green", ""),
        ("LV1", "TFMBM → ICE TFM: Outright max order quantity"): ("red", "MISMATCH"),
    }
    fgbl = next(r for r in result.rows if r.setting.startswith("FGBL") and r.colour.value == "red")
    assert (fgbl.expected, fgbl.actual, fgbl.node) == ("200", "100", "CLI-ALPHA (200)")
    assert fgbl.message == (
        "Set Max Position Product (net) for FGBL (Eurex) on CLI-ALPHA to 200 "
        "(TT has 100; the request is higher)."
    )
    corn = next(r for r in result.rows if r.rule_id == "LV2")
    assert "Upload TT's product list" in corn.message and "on CBT" in corn.message


def test_ric_sheet_product_missing_from_tt_and_not_requested(setup: SetupData) -> None:
    text = "Exch\tRIC\tMax Order\tMax Net\nCBT\tTY\t10\t100\nCBT\tC\t1\t1\n"
    corn = TtProduct("CME", "ZC", name="Corn Futures")
    result = run(setup, text, product_list=[corn])
    got = outcomes(result.rows)
    assert got[("LV1", "C → CME ZC: Max Position Product (net)")] == ("red", "MISSING")
    assert got[("LV3", "FGBL (Eurex, Future)")] == ("amber", "NO_RULE")
    assert got[("LV3", "TFM (ICE, Future)")] == ("amber", "NO_RULE")
    missing = next(r for r in result.rows if r.reason and r.reason.value == "MISSING")
    assert missing.message == (
        "There is no ZC (CME) limit row on CLI-ALPHA. Add it with Max Position Product (net) = 1."
    )


def test_ric_sheet_user_mapping_and_guess(setup: SetupData) -> None:
    text = "Exch\tRIC\tDescription\tMax Order\tMax Net\nCBT\tXX\tTen year note\t10\t100\n"
    guessed = run(setup, text, product_list=[TtProduct("CME", "ZN", name="10-Year T-Note")])
    assert guessed.enriched.rows[0].status == "check"
    assert {r.colour.value for r in guessed.rows if r.rule_id == "LV1"} == {"amber"}
    assert all("guessed" in r.message for r in guessed.rows if r.rule_id == "LV1")

    mapped = run(setup, text, ric_mappings=[RicMapping("XX", "", "CME", "ZN")])
    assert mapped.enriched.rows[0].status == "mapped"
    assert {r.colour.value for r in mapped.rows if r.rule_id == "LV1"} == {"green"}


def test_universe_sheet(setup: SetupData) -> None:
    result = run(setup, UNIVERSE_SHEET, symbol_map=[SymbolMapEntry("GBL", "Future", "FGBL")])
    assert result.format == "universe"
    assert outcomes(result.rows) == {
        ("LV1", "ZN (Future): Max Position Product (net)"): ("green", ""),
        ("LV1", "ZN (Future): Outright max order quantity"): ("green", ""),
        ("LV1", "ZN (Future): Sprd/Strat max order quantity"): ("green", ""),
        ("LV1", "GBL (Future): Max Position Product (net)"): ("green", ""),
        ("LV1", "GBL (Future): Outright max order quantity"): ("green", ""),
        ("LV1", "GBL (Future): Sprd/Strat max order quantity"): ("red", "MISMATCH"),
        ("LV4", "KE (Future)"): ("amber", "NO_RULE"),
        ("LV1", "ZZZ (Future): Max Position Product (net)"): ("red", "MISSING"),
        ("LV1", "ZZZ (Future): Outright max order quantity"): ("red", "MISSING"),
        ("LV1", "ZZZ (Future): Sprd/Strat max order quantity"): ("red", "MISSING"),
        ("LV3", "TFM (ICE, Future)"): ("amber", "NO_RULE"),
    }


def test_universe_sheet_without_the_symbol_map(setup: SetupData) -> None:
    got = outcomes(run(setup, UNIVERSE_SHEET).rows)
    assert got[("LV1", "GBL (Future): Max Position Product (net)")] == ("red", "MISSING")
    assert got[("LV3", "FGBL (Eurex, Future)")] == ("amber", "NO_RULE")


def test_tables(setup: SetupData) -> None:
    result = run(setup, RIC_SHEET)
    mapping = lv.mapping_table(result.enriched)
    assert mapping[0]["TT Product Symbol"] == "ZN" and mapping[0]["Match"] == "known"
    current = lv.current_table(result.current)
    assert current[0]["Product"] == "ZN"
    assert current[0]["Max Position Product (net)"] == "100"
    assert lv.fmt(1234567) == "1,234,567" and lv.fmt(None) == "Not set"


def test_every_non_green_row_says_what_to_do(setup: SetupData) -> None:
    for text in (RIC_SHEET, UNIVERSE_SHEET):
        for row in run(setup, text).rows:
            if row.colour.value != "green":
                assert row.reason is not None and len(row.message) > 20
