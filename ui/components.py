"""Shared Streamlit building blocks used by the landing page and every report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from core.config import MODE_NAMES, AppConfig
from core.readonly_http import LIVE_ENVIRONMENT
from core.results import Colour, ResultRow, summary, to_csv, to_frame, worst_first
from core.snapshots import Snapshot, SnapshotError, latest_id, list_snapshots, load_snapshot
from ui.discovery import ReportError, ReportSpec, by_category

TILES_PER_ROW = 3

# --- Live / UAT ------------------------------------------------------------------

_UAT_BANNER = (
    '<div style="background-color:#ff8c00;color:#1a1a1a;padding:0.6rem 1rem;'
    'border-radius:0.4rem;font-weight:700;font-size:1.1rem;text-align:center;'
    'margin-bottom:0.8rem">UAT MODE - TT test environment ({environment}). '
    "This is not production data.</div>"
)
_LIVE_LABEL = (
    '<div style="border:1px solid #2e7d32;color:#2e7d32;padding:0.2rem 0.8rem;'
    'border-radius:0.4rem;font-weight:700;display:inline-block;margin-bottom:0.6rem">'
    "LIVE - TT production ({environment})</div>"
)


def page_title(settings: AppConfig) -> str:
    """The browser tab title: "Lookout" in Live, "Lookout UAT" in UAT."""
    return "Lookout UAT" if settings.tt.is_uat else "Lookout"


def mode_banner(settings: AppConfig) -> None:
    """Says on every page, and in the sidebar, which TT environment this session uses."""
    template = _UAT_BANNER if settings.tt.is_uat else _LIVE_LABEL
    html = template.format(environment=settings.tt.environment)
    st.markdown(html, unsafe_allow_html=True)
    st.sidebar.markdown(html, unsafe_allow_html=True)



def report_tiles(specs: list[ReportSpec], pages: Mapping[str, Any]) -> None:
    """The landing page grid: one bordered tile per report, grouped by category."""
    if not specs:
        st.info("No reports are installed yet. Add one to the reports folder.")
        return
    for category, group in by_category(specs).items():
        st.subheader(category)
        for start in range(0, len(group), TILES_PER_ROW):
            columns = st.columns(TILES_PER_ROW)
            for column, spec in zip(columns, group[start : start + TILES_PER_ROW], strict=False):
                with column, st.container(border=True):
                    st.markdown(f"### {spec.icon} {spec.title}")
                    st.write(spec.description)
                    st.page_link(pages[spec.slug], label="Open report", icon="➡️")


def report_errors(errors: list[ReportError]) -> None:
    """Shown on the landing page when a report file could not be loaded."""
    if not errors:
        return
    with st.expander(f"{len(errors)} report(s) could not be loaded", icon="⚠️"):
        for error in errors:
            st.write(f"**{error.file}**: {error.problem}")


# --- snapshots -------------------------------------------------------------------

_STATUS_NOTE = {
    "complete": "Complete",
    "partial": "Partial: some data was not read, so some checks show amber",
    "failed": "Failed: this pull did not finish",
}


def snapshot_picker(
    base_dir: Path, client: str, *, key: str, environment: str | None = None
) -> Snapshot | None:
    """Choose a saved snapshot (latest first). Returns None when there is nothing to open.

    With ``environment``, a snapshot pulled from another TT environment is not opened."""
    available = list_snapshots(base_dir, client)
    if not available:
        st.warning(
            f"No snapshots found for {client} in {base_dir}. "
            "A snapshot is created by pulling the setup from TT."
        )
        return None
    latest = latest_id(base_dir, client)
    default = available.index(latest) if latest in available else 0
    chosen = st.selectbox(
        "Snapshot",
        available,
        index=default,
        format_func=lambda sid: f"{sid} (latest)" if sid == latest else sid,
        key=f"{key}_snapshot",
    )
    try:
        snapshot = load_snapshot(base_dir, client, chosen, environment=environment)
    except SnapshotError as exc:
        st.error(f"Snapshot {chosen} could not be opened: {exc}")
        return None
    snapshot_status(snapshot)
    return snapshot


def snapshot_status(snapshot: Snapshot) -> None:
    manifest = snapshot.manifest
    pulled_from = manifest.get("environment") or LIVE_ENVIRONMENT
    st.caption(
        f"Snapshot {snapshot.id} · taken {manifest.get('created_at', '?')} · "
        f"from TT {MODE_NAMES.get(pulled_from, pulled_from)} · "
        f"{manifest.get('calls', 0)} TT calls · tool v{manifest.get('tool_version', '?')}"
    )
    note = _STATUS_NOTE.get(snapshot.status, snapshot.status)
    if snapshot.status == "complete":
        st.success(note, icon="✅")
    elif snapshot.status == "partial":
        st.warning(note, icon="⚠️")
    else:
        st.error(note, icon="⛔")


# --- results ---------------------------------------------------------------------

# Light backgrounds with dark text, readable in both light and dark themes.
_ROW_STYLE = {
    Colour.RED.value: "background-color: #fde2e2; color: #5f1414",
    Colour.AMBER.value: "background-color: #fff3cd; color: #5c4400",
    Colour.GREEN.value: "background-color: #e3f5e6; color: #14401d",
    Colour.GREY.value: "background-color: #ececec; color: #3a3a3a",
}
_COLOUR_NAMES = {
    Colour.RED: "Red",
    Colour.AMBER: "Amber",
    Colour.GREEN: "Green",
    Colour.GREY: "Not checked",
}


def _style_rows(frame: pd.DataFrame) -> Any:
    def paint(row: pd.Series) -> list[str]:
        return [_ROW_STYLE.get(row["colour"], "")] * len(row)

    return frame.style.apply(paint, axis=1)


def results_table(rows: Sequence[ResultRow], *, key: str, file_name: str) -> None:
    """Counts, filters, the colour-coded table and a CSV download of what is shown."""
    counts = summary(rows)
    # Grey ("not checked") appears only when a rule skipped something on purpose.
    offered = [Colour.RED, Colour.AMBER, Colour.GREEN]
    if counts[Colour.GREY]:
        offered.append(Colour.GREY)
    for column, colour in zip(st.columns(len(offered)), offered, strict=True):
        column.metric(_COLOUR_NAMES[colour], counts[colour])

    left, right = st.columns(2)
    colours = left.multiselect(
        "Show",
        [c.value for c in offered],
        default=[Colour.RED.value, Colour.AMBER.value],
        format_func=lambda value: _COLOUR_NAMES[Colour(value)],
        key=f"{key}_colours",
    )
    rules = right.multiselect(
        "Rules (empty = all)",
        sorted({row.rule_id for row in rows}),
        key=f"{key}_rules",
    )
    shown = [
        row
        for row in worst_first(rows)
        if row.colour.value in colours and (not rules or row.rule_id in rules)
    ]
    if not shown:
        st.info("Nothing to show with these filters.")
    else:
        st.dataframe(_style_rows(to_frame(shown)), hide_index=True, width="stretch")
    st.download_button(
        f"Download {len(shown)} rows as CSV",
        data=to_csv(shown),
        file_name=file_name,
        mime="text/csv",
        key=f"{key}_download",
    )


def plain_table(records: Sequence[Mapping[str, str]], *, key: str, file_name: str) -> None:
    """A display-only table with a CSV download. All values are shown as text."""
    if not records:
        st.info("Nothing to show.")
        return
    frame = pd.DataFrame(list(records), dtype=str)
    st.dataframe(frame, hide_index=True, width="stretch")
    st.download_button(
        "Download as CSV",
        data=frame.to_csv(index=False, lineterminator="\r\n").encode("utf-8-sig"),
        file_name=file_name,
        mime="text/csv",
        key=f"{key}_download",
    )
