"""Clone: copy an account, or a group with the accounts below it, into new accounts in TT.

UAT only. This is the only page that changes anything in TT, and only through the
UAT-only write guard in ``core/write_http.py``. The logic is in ``core/clone.py`` and
``core/clone_run.py``.

Flow: enter the source and the new names, tick what to copy, press Next. Lookout reads
the source from TT UAT and shows every account it would create. Nothing is written
until the list is confirmed and Create is pressed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

import core.config as app_config
from core import credentials
from core.api_log import ApiLog
from core.clone import CLONE_FLAGS, ClearingMode, ClonePlan, build_plan, clean_names
from core.clone_run import read_source, run_plan, write_log
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
    ClearingMode.SOURCE: "Same value as the account it is copied from",
    ClearingMode.NEW_NAME: "The new account's name",
}


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


def _inputs() -> tuple[str, list[str], dict[str, bool], ClearingMode]:
    source = st.text_input("Account or group to clone (name or id)", key="cl_source")
    pasted = st.text_area(
        "New account names, one per line (spaces around each name are removed)",
        height=160,
        key="cl_names",
    )
    names = clean_names(pasted)
    if names:
        st.caption(f"{len(names)} new name(s): " + ", ".join(names))
    st.markdown("**Copy with each account**")
    columns = st.columns(2)
    flags = {
        flag: columns[i % 2].checkbox(label, key=f"cl_flag_{flag}")
        for i, (flag, label) in enumerate(CLONE_FLAGS.items())
    }
    st.caption("Limits and user assignments are not copied.")
    mode = ClearingMode.OFF
    if st.checkbox("Clone the clearing account override (Clearing Account)", key="cl_clearing"):
        chosen = st.radio(
            "Clearing Account on the new accounts",
            list(CLEARING_CHOICES),
            format_func=CLEARING_CHOICES.__getitem__,
            key="cl_clearing_mode",
        )
        mode = ClearingMode(chosen)
    return source.strip(), names, flags, mode


def _review(settings: AppConfig, inputs: tuple[Any, ...]) -> None:
    source, names, flags, mode = inputs
    try:
        tt, close = make_tt_client(settings)
    except Exception as exc:  # noqa: BLE001 - shown to the user; nothing has been read yet
        st.error(f"Could not prepare the connection to TT: {exc}")
        return
    try:
        with st.spinner("Reading the source account from TT UAT..."):
            data = read_source(
                tt,
                source,
                need_clearing=mode is not ClearingMode.OFF,
                max_pages=settings.pacing.max_order_tag_default_pages,
            )
    except Exception as exc:  # noqa: BLE001 - the read stopped; say why instead of a traceback
        st.error(f"Could not read the source from TT UAT ({type(exc).__name__}: {exc}).")
        return
    finally:
        close()
    plan = build_plan(
        data.tree,
        source,
        names,
        flags=flags,
        clearing_mode=mode,
        clearing_entries=data.clearing_entries,
        market_names=data.market_names,
    )
    st.session_state["cl_review"] = {
        "inputs": inputs,
        "plan": plan,
        "field_ids": data.clearing_field_ids,
    }


def _show_plan(plan: ClonePlan) -> None:
    st.subheader("Check before creating")
    if plan.source is not None:
        st.write(f"Copying **{plan.source.display}**.")
    for error in plan.errors:
        st.error(error)
    if plan.steps:
        components.plain_table(plan.rows(), key="cl_plan", file_name="clone_plan.csv")
    ticked = [CLONE_FLAGS[f] for f, on in plan.flags.items() if on]
    st.caption("Also copied: " + (", ".join(ticked) if ticked else "nothing else"))
    if plan.clearing_mode is not ClearingMode.OFF:
        st.markdown("**Clearing Account**")
        if plan.clearing:
            components.plain_table(
                plan.clearing_rows(), key="cl_clearing_table", file_name="clone_clearing.csv"
            )
        else:
            st.info("The source has no Clearing Account set on its own accounts; none is set.")


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
            result = run_plan(
                plan,
                writer,
                reader,
                clearing_field_ids=review["field_ids"],
                max_pages=settings.pacing.max_order_tag_default_pages,
            )
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

    inputs = _inputs()
    review = st.session_state.get("cl_review")
    if review is not None and review["inputs"] != inputs:
        st.session_state.pop("cl_review")
        review = None
    if st.button("Next", type="primary", disabled=not (inputs[0] and inputs[1]), key="cl_next"):
        st.session_state.pop("cl_result", None)
        _review(settings, inputs)
        review = st.session_state.get("cl_review")

    if review is not None:
        plan: ClonePlan = review["plan"]
        _show_plan(plan)
        round_key = "cl_round"
        done = st.session_state.get(round_key, 0)
        agreed = st.checkbox("I have checked this list", key=f"cl_ok_{done}", disabled=not plan.ok)
        left, right = st.columns([1, 4])
        if left.button("Back", key="cl_back"):
            st.session_state.pop("cl_review", None)
            st.rerun()
        label = f"Create {len(plan.steps)} account(s) in TT UAT"
        if right.button(label, type="primary", disabled=not (plan.ok and agreed), key="cl_create"):
            st.session_state[round_key] = done + 1
            _create(settings, review)
    _show_result()
