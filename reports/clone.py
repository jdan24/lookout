"""Clone: copy an account, or a group with the accounts below it, into new accounts in TT.

UAT only. This is the only page that changes anything in TT, and only through the
UAT-only write guard in ``core/write_http.py``. The logic is in ``core/clone.py`` and
``core/clone_run.py``.

Flow: enter the source and press Get details. Lookout reads it from TT UAT and shows
the source and the accounts below it, each with its description and Clearing Account
per exchange. Then choose what the new accounts get for the Clearing Account: the
source's values, nothing, or values from a table. Enter the new accounts (with the
table choice: new account, exchange, clearing account override, one row per account
and exchange) and press Next. Lookout shows every account and clearing value it would
create; both tables can be edited (rename, change a value) or trimmed (delete rows)
there, and the plan is checked again after every edit. Nothing is written until the
list is confirmed and Create is pressed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

import core.config as app_config
from core import credentials
from core.api_log import ApiLog
from core.clone import (
    COLUMNS,
    ClearingMode,
    ClonePlan,
    NewAccounts,
    PlanEdits,
    build_plan,
    find_source,
    parse_new_accounts,
)
from core.clone_run import SourceData, read_source, run_plan, write_log
from core.config import AppConfig
from core.tt_writer import TTWriter
from core.write_http import WRITE_ENVIRONMENT, WriteClient
from ui import components
from ui.pull import HOW_TO_SET_REQUEST_ID, make_tt_client

TITLE = "Clone"
DESCRIPTION = (
    "Copies an account, or a group and the accounts below it, into new accounts in TT UAT."
)
ICON = "🧬"
CATEGORY = "Setup"

CLEARING_CHOICES = {
    ClearingMode.SOURCE: "Clone the clearing account override: each new account gets the "
    "value of the account it is copied from",
    ClearingMode.OFF: "Leave the clearing account override blank",
    ClearingMode.TABLE: "Use the values from a table (below): exchanges a row doesn't list "
    "stay blank",
}
SOURCE_WIDTH = 360  # pixels: room for a 40-character group name


def make_tt_writer(settings: AppConfig) -> tuple[TTWriter, Callable[[], None]]:
    """A UAT write client built from the app settings, and a function that closes it."""
    if not settings.tt.request_id_is_set:
        raise ValueError(HOW_TO_SET_REQUEST_ID)
    http = WriteClient(settings.tt.host, environment=settings.tt.environment)
    writer = TTWriter(
        http,
        lambda: credentials.load_credentials(WRITE_ENVIRONMENT),
        requests_per_second=settings.pacing.requests_per_second,
        max_calls=settings.pacing.max_calls_per_pull,
        request_id_prefix=settings.tt.request_id_prefix,
        api_log=ApiLog(settings.api_log_dir),
    )
    return writer, http.close


def _decode(data: bytes) -> str:
    """A CSV file's text: UTF-8 (with or without Excel's mark), else Windows' own encoding."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _source() -> str:
    source = st.text_input(
        "Account or group to clone (name or id)", key="cl_source", width=SOURCE_WIDTH
    )
    return source.strip()


def _get_details(settings: AppConfig, source: str) -> None:
    """Read the source from TT UAT (GET only) and keep it for the rest of the page."""
    try:
        tt, close = make_tt_client(settings)
    except Exception as exc:  # noqa: BLE001 - shown to the user; nothing has been read yet
        st.error(f"Could not prepare the connection to TT: {exc}")
        return
    try:
        with st.spinner("Reading the source from TT UAT..."):
            data = read_source(tt, source, max_pages=settings.pacing.max_order_tag_default_pages)
    except Exception as exc:  # noqa: BLE001 - the read stopped; say why instead of a traceback
        st.error(f"Could not read the source from TT UAT ({type(exc).__name__}: {exc}).")
        return
    finally:
        close()
    st.session_state["cl_details"] = {"source": source, "data": data}


def _show_details(details: dict[str, Any]) -> bool:
    """The source's accounts, descriptions and Clearing Account values. False: not found."""
    data: SourceData = details["data"]
    source, problem = find_source(data.tree, details["source"])
    if source is None:
        st.error(problem)
        return False
    below = len(data.tree.descendants(source.id))
    st.subheader("Source details")
    st.write(
        f"**{source.display}**"
        + (f", with {below} account(s) below it" if below else "")
        + ". Clearing account override per exchange:"
    )
    st.dataframe(pd.DataFrame(data.details(details["source"]), dtype=str), hide_index=True)
    if not data.clearing_entries:
        st.caption("No Clearing Account is set on any of these accounts.")
    return True


def _mode() -> ClearingMode:
    st.subheader("What to clone")
    st.caption("TT copies none of the source's other settings: every clone switch is off.")
    chosen = st.radio(
        "Clearing account override on the new accounts",
        list(CLEARING_CHOICES),
        format_func=CLEARING_CHOICES.__getitem__,
        key="cl_clearing_mode",
    )
    return ClearingMode(chosen)


def _new_accounts(mode: ClearingMode, has_below: bool) -> NewAccounts:
    if mode is ClearingMode.TABLE:
        label = (
            "New accounts: paste from Excel or type one row per account and exchange. "
            "Columns: " + ", ".join(COLUMNS) + " (use TT's exchange names, e.g. CME, "
            "ICE_L, Eurex; a row with only the new account sets no clearing value)"
        )
        placeholder = "NEW10001AL,CME,CLR123\nNEW10001AL,Eurex,CLR456\nNEW20001AL,CME,CLR789"
    else:
        label = "New accounts: one per line, or paste a column from Excel"
        placeholder = "NEW10001AL\nNEW20001AL"
    pasted = st.text_area(label, height=160, key="cl_names", placeholder=placeholder)
    upload = st.file_uploader("...or choose a CSV file", type=["csv"], key="cl_file")
    if upload is not None:
        st.caption(f"Using {upload.name}; the box above is ignored while a file is chosen.")
        parsed = parse_new_accounts(_decode(upload.getvalue()))
    else:
        parsed = parse_new_accounts(pasted)
    if parsed.accounts:
        st.caption(
            f"{len(parsed.accounts)} new account(s): "
            + ", ".join(
                a.name + (f" ({len(a.overrides)} exchange value(s))" if a.overrides else "")
                for a in parsed.accounts
            )
            + (" (header row skipped)" if parsed.header_skipped else "")
        )
    for error in parsed.errors:
        st.error(error)
    if mode is not ClearingMode.TABLE and any(a.overrides for a in parsed.accounts):
        st.error(
            "This list has Exchange and clearing columns, which are only used with "
            "'Use the values from a table'. Choose that above, or list the new accounts only."
        )
    if mode is ClearingMode.TABLE and has_below:
        st.caption("The accounts below the group get no Clearing Account with this choice.")
    return parsed


def _review(details: dict[str, Any], inputs: tuple[Any, ...]) -> None:
    """The plan for ``inputs``, from the details already read (no TT call)."""
    review = {"inputs": inputs, "data": details["data"], "edits": PlanEdits(), "version": 0}
    review["plan"] = _plan(review)
    st.session_state["cl_review"] = review


def _plan(review: dict[str, Any]) -> ClonePlan:
    """The plan from the review's source data, inputs and edits (no TT read)."""
    source, parsed, mode = review["inputs"]
    data = review["data"]
    return build_plan(
        data.tree,
        source,
        parsed.accounts,
        clearing_mode=mode,
        clearing_entries=data.clearing_entries,
        market_names=data.market_names,
        edits=review["edits"],
    )


def _show_plan(review: dict[str, Any]) -> None:
    """The plan, as two editable tables: accounts and Clearing Account values."""
    plan: ClonePlan = review["plan"]
    st.subheader("Check before creating")
    if plan.source is not None:
        st.write(f"Copying **{plan.source.display}**.")
    for error in plan.errors:
        st.error(error)
    edited = review["edits"] != PlanEdits()
    st.caption(
        "Edit a name or value in place, or select rows and delete them. The plan is "
        "checked again after every change; nothing is sent to TT until you press Create."
    )
    if edited and st.button("Undo all edits", key="cl_undo"):
        _apply(review, PlanEdits())
    version = review["version"]
    if plan.steps:
        _accounts_editor(review, plan, version)
    else:
        st.info("No accounts left to create.")
    if plan.clearing_mode is not ClearingMode.OFF:
        st.markdown("**Clearing Account**")
        if plan.clearing:
            _clearing_editor(review, plan, version)
        else:
            st.info("No Clearing Account value will be set.")


def _accounts_editor(review: dict[str, Any], plan: ClonePlan, version: int) -> None:
    frame = pd.DataFrame(plan.rows(), index=[s.key for s in plan.steps], dtype=str)
    out = st.data_editor(
        frame,
        key=f"cl_steps_{version}",
        num_rows="delete",
        disabled=["Step", "Under", "Copied from"],
        hide_index=True,
        width="stretch",
    )
    changed = account_edits(frame, out, review["edits"])
    if changed is not None:
        _apply(review, changed)


def account_edits(frame: pd.DataFrame, out: pd.DataFrame, edits: PlanEdits) -> PlanEdits | None:
    """The edits after the accounts table went from ``frame`` to ``out`` (None: none)."""
    removed = set(frame.index) - set(out.index)
    names = dict(edits.names)
    for key in out.index:
        new = str(out.at[key, "New account"] or "").strip()
        if new != str(frame.at[key, "New account"]):
            names[key] = new
    if not removed and names == dict(edits.names):
        return None
    return PlanEdits(names, edits.removed | removed, edits.values, edits.dropped)


def _clearing_editor(review: dict[str, Any], plan: ClonePlan, version: int) -> None:
    frame = pd.DataFrame(plan.clearing_rows(), index=[c.key for c in plan.clearing], dtype=str)
    out = st.data_editor(
        frame,
        key=f"cl_clearing_{version}",
        num_rows="delete",
        disabled=["Step", "New account", "Exchange", "Source value"],
        hide_index=True,
        width="stretch",
    )
    changed = clearing_edits(frame, out, review["edits"])
    if changed is not None:
        _apply(review, changed)


def clearing_edits(frame: pd.DataFrame, out: pd.DataFrame, edits: PlanEdits) -> PlanEdits | None:
    """The edits after the clearing table went from ``frame`` to ``out`` (None: none)."""
    dropped = set(frame.index) - set(out.index)
    values = dict(edits.values)
    for key in out.index:
        new = str(out.at[key, "New value"] or "").strip()
        if new != str(frame.at[key, "New value"]):
            values[key] = new
    if not dropped and values == dict(edits.values):
        return None
    return PlanEdits(edits.names, edits.removed, values, edits.dropped | dropped)


def _apply(review: dict[str, Any], edits: PlanEdits) -> None:
    """Rebuild the plan with ``edits`` (no TT read) and show it afresh."""
    review["edits"] = edits
    review["plan"] = _plan(review)
    review["version"] += 1  # fresh editors, so an edit is not applied twice
    st.session_state["cl_review"] = review
    st.rerun()


def _create(settings: AppConfig, review: dict[str, Any]) -> None:
    plan: ClonePlan = review["plan"]
    try:
        reader, close_reader = make_tt_client(settings)
        writer, close_writer = make_tt_writer(settings)
    except Exception as exc:  # noqa: BLE001 - shown to the user; nothing has been sent
        st.error(f"Could not prepare the connection to TT: {exc}")
        return
    try:
        with st.spinner(f"Creating {len(plan.steps)} account(s) in TT UAT..."):
            result = run_plan(plan, writer, reader)
    finally:
        close_reader()
        close_writer()
    log = write_log(settings.clone_log_dir, plan, result, [*reader.calls, *writer.calls])
    st.session_state["cl_result"] = {
        "result": result,
        "log": str(log),
        "api_log": str(settings.api_log_dir),
    }
    st.session_state.pop("cl_review", None)
    st.session_state.pop("cl_details", None)  # TT has changed: read the source again
    st.rerun()


def _show_result() -> None:
    done = st.session_state.get("cl_result")
    if not done:
        return
    result = done["result"]
    if result.ok:
        st.success(f"Created {len(result.created)} account(s) in TT UAT.")
    else:
        st.error(result.stopped)
        st.warning("Accounts already created stay in TT UAT; check them in TT Setup.")
    components.plain_table(result.rows, key="cl_result", file_name="clone_result.csv")
    st.caption(f"Saved a record of this run: {done['log']}")
    st.caption(f"Every TT call, with TT's replies, is logged in {done['api_log']}")


def render() -> None:
    st.title(f"{ICON} {TITLE}")
    settings = app_config.load_config()
    if settings.tt.environment != WRITE_ENVIRONMENT:
        st.info(
            "Clone works in UAT only. Start Lookout with Start-Lookout-UAT.bat to use it. "
            "Lookout never changes anything in TT Live."
        )
        return
    st.caption(
        DESCRIPTION + " This page changes TT UAT. Nothing is sent until you check the list "
        "and press Create."
    )
    if not credentials.has_credentials(WRITE_ENVIRONMENT):
        st.warning(
            "No TT UAT credentials are stored on this PC. "
            + credentials.how_to_set(WRITE_ENVIRONMENT)
        )
        return
    if not settings.tt.request_id_is_set:
        st.warning(HOW_TO_SET_REQUEST_ID)
        return

    source = _source()
    details = st.session_state.get("cl_details")
    if details is not None and details["source"] != source:
        st.session_state.pop("cl_details")
        st.session_state.pop("cl_review", None)
        details = None
    if st.button("Get details", disabled=not source, key="cl_details_button"):
        st.session_state.pop("cl_result", None)
        st.session_state.pop("cl_review", None)
        _get_details(settings, source)
        details = st.session_state.get("cl_details")
    if details is not None and _show_details(details):
        _plan_and_create(settings, details)
    _show_result()


def _plan_and_create(settings: AppConfig, details: dict[str, Any]) -> None:
    data: SourceData = details["data"]
    mode = _mode()
    found, _ = find_source(data.tree, details["source"])
    has_below = found is not None and bool(data.tree.descendants(found.id))
    parsed = _new_accounts(mode, has_below)
    inputs = (details["source"], parsed, mode)
    review = st.session_state.get("cl_review")
    if review is not None and review["inputs"] != inputs:
        st.session_state.pop("cl_review")
        review = None
    misplaced = mode is not ClearingMode.TABLE and any(a.overrides for a in parsed.accounts)
    ready = bool(parsed.accounts and not parsed.errors and not misplaced)
    if st.button("Next", type="primary", disabled=not ready, key="cl_next"):
        st.session_state.pop("cl_result", None)
        _review(details, inputs)
        review = st.session_state.get("cl_review")
    if review is None:
        return
    _show_plan(review)
    plan: ClonePlan = review["plan"]
    round_key = "cl_round"
    done = st.session_state.get(round_key, 0)
    agreed = st.checkbox(
        "I have checked this list",
        key=f"cl_ok_{done}_{review['version']}",
        disabled=not plan.ok,
    )
    left, right = st.columns([1, 4])
    if left.button("Back", key="cl_back"):
        st.session_state.pop("cl_review", None)
        st.rerun()
    label = f"Create {len(plan.steps)} account(s) in TT UAT"
    if right.button(label, type="primary", disabled=not (plan.ok and agreed), key="cl_create"):
        st.session_state[round_key] = done + 1
        _create(settings, review)
