"""Limit Verification: requested limits against the limit rows of one account in TT.

UI only. The logic is in ``checks/limit_verification.py`` (ported from limit_compare)
and runs on a saved snapshot.
"""

from __future__ import annotations

import streamlit as st

import core.config as app_config
from checks import limit_verification as lv
from checks.limit_compare import parse_symbol_map
from checks.ric_mapping import parse_mappings_csv
from checks.tabular import RawTable, TableError, parse_pasted_table, read_table_file
from checks.tt_products import ProductListError, TtProduct, parse_tt_csv
from core.hierarchy import HierarchyError
from core.setup_model import load_setup
from core.snapshots import SnapshotError, list_clients
from ui import components

TITLE = "Limit Verification"
DESCRIPTION = (
    "Compares requested limits (a pasted or uploaded sheet) with an account's limits in TT."
)
ICON = "📏"
CATEGORY = "Limits"


@st.cache_data(show_spinner="Reading TT's product list…", max_entries=2)
def _products(data: bytes) -> list[TtProduct]:
    return parse_tt_csv(data.decode("utf-8-sig", errors="replace"))


def _requested_sheet() -> RawTable | None:
    pasted_tab, upload_tab = st.tabs(["Paste", "Upload a file"])
    with pasted_tab:
        text = st.text_area(
            "Paste the requested limits (copy the table from Excel or Outlook, with headers)",
            height=180,
            key="lv_paste",
        )
    with upload_tab:
        upload = st.file_uploader(
            "Requested limits (CSV, TSV or XLSX)", type=["csv", "tsv", "txt", "xlsx"], key="lv_file"
        )
    try:
        if upload is not None:
            return read_table_file(upload.getvalue(), upload.name)
        if text.strip():
            return parse_pasted_table(text)
    except TableError as exc:
        st.error(str(exc))
    return None


def _columns(raw: RawTable) -> tuple[lv.SheetFormat | None, dict[str, str]]:
    detected_format, detected = lv.detect_format(raw)
    formats = list(lv.FORMAT_NAMES)
    sheet_format = st.radio(
        "Kind of sheet",
        formats,
        index=formats.index(detected_format) if detected_format else 0,
        format_func=lv.FORMAT_NAMES.get,
        horizontal=True,
        key="lv_format",
    )
    if sheet_format != detected_format:
        detected = {}
    cols: dict[str, str] = {}
    with st.expander("Columns", expanded=detected_format is None):
        choices = ["", *raw.headers]
        for spec in lv.FORMAT_FIELDS[sheet_format]:
            default = detected.get(spec.field, "")
            picked = st.selectbox(
                spec.label + ("" if spec.required else " (optional)"),
                choices,
                index=choices.index(default) if default in choices else 0,
                key=f"lv_col_{sheet_format}_{spec.field}",
            )
            if picked:
                cols[spec.field] = picked
    missing = [
        s.label for s in lv.FORMAT_FIELDS[sheet_format] if s.required and not cols.get(s.field)
    ]
    if missing:
        st.warning("Choose the column for: " + ", ".join(missing))
        return None, cols
    return sheet_format, cols


def _extras(sheet_format: lv.SheetFormat) -> dict:
    extras: dict = {}
    with st.expander("Optional files"):
        if sheet_format == "ric":
            st.caption(
                "TT's product list helps match RIC roots that have no limit row yet. Download it "
                "from TT (ExchangeProducts CSV) and drop it here; Lookout never fetches it."
            )
            products = st.file_uploader("TT product list (CSV)", type=["csv"], key="lv_products")
            if products is not None:
                try:
                    extras["product_list"] = _products(products.getvalue())
                except ProductListError as exc:
                    st.error(str(exc))
            mapping = st.file_uploader(
                "RIC mapping file (ricRoot, exchange, ttExchange, ttSymbol)",
                type=["csv"],
                key="lv_map",
            )
            if mapping is not None:
                mappings, skipped = parse_mappings_csv(
                    mapping.getvalue().decode("utf-8-sig", "replace")
                )
                extras["ric_mappings"] = mappings
                st.caption(
                    f"{len(mappings)} mappings loaded"
                    + (f", {skipped} rows skipped" if skipped else "")
                )
        else:
            symbol_map = st.file_uploader(
                "Symbol map (clientTicker, type, systemFamily)",
                type=["csv", "xlsx"],
                key="lv_symbols",
            )
            if symbol_map is not None:
                try:
                    extras["symbol_map"] = parse_symbol_map(
                        read_table_file(symbol_map.getvalue(), symbol_map.name)
                    )
                except TableError as exc:
                    st.error(str(exc))
    return extras


def render() -> None:
    st.title(f"{ICON} {TITLE}")
    st.caption(DESCRIPTION + " Read-only: fixes are made by hand in TT.")

    settings = app_config.load_config()
    snapshot_dir = settings.snapshot_dir
    clients = list_clients(snapshot_dir)
    if not clients:
        st.warning(
            f"No snapshots found in {snapshot_dir}. A snapshot is created by pulling from TT."
        )
        return
    client = st.selectbox("Client", clients, key="lv_client")
    snapshot = components.snapshot_picker(
        snapshot_dir, client, key="lv", environment=settings.tt.environment
    )
    if snapshot is None:
        return
    try:
        setup = load_setup(snapshot)
    except (HierarchyError, SnapshotError) as exc:
        st.error(f"This snapshot could not be read: {exc}")
        return
    except (ValueError, TypeError, KeyError) as exc:  # data TT sent in a shape we did not expect
        st.error(f"This snapshot holds data Lookout did not expect ({type(exc).__name__}: {exc}).")
        return

    accounts = lv.accounts_with_limits(setup)
    if not accounts:
        st.warning("No account in this snapshot has product limit rows.")
        return
    labels = {a.id: f"{a.display} · {n} limit rows" for a, n in accounts}
    account_id = st.selectbox("Account", list(labels), format_func=labels.get, key="lv_account")

    st.subheader("Requested limits")
    raw = _requested_sheet()
    if raw is None:
        st.info("Paste or upload the requested limits to compare them with TT.")
        return
    if not raw.rows:
        st.warning("The sheet has a header row but no data rows.")
        return
    sheet_format, cols = _columns(raw)
    if sheet_format is None:
        return
    try:
        result = lv.verify_limits(
            setup, account_id, raw, sheet_format, cols, **_extras(sheet_format)
        )
    except (ValueError, TypeError, KeyError) as exc:
        st.error(
            "The comparison could not be run on this data "
            f"({type(exc).__name__}: {exc}). Please report this with the snapshot id."
        )
        return

    if result.conflicts:
        st.warning(
            "TT has more than one limit row with different values for: "
            + ", ".join(result.conflicts)
            + ". The last one was used."
        )
    tabs = ["Results", "Current limits in TT"] + (["Product matching"] if result.enriched else [])
    shown = st.tabs(tabs)
    with shown[0]:
        components.results_table(
            result.rows, key="lv_results", file_name=f"limit_verification_{snapshot.id}.csv"
        )
    with shown[1]:
        components.plain_table(
            lv.current_table(result.current),
            key="lv_current",
            file_name=f"tt_limits_{snapshot.id}.csv",
        )
    if result.enriched:
        with shown[2]:
            st.caption(
                "How each RIC root was matched to a TT product: mapped (your file), known (curated "
                "table), direct (same symbol), check (guessed from the description), unmatched."
            )
            components.plain_table(
                lv.mapping_table(result.enriched), key="lv_mapping", file_name="ric_matching.csv"
            )
