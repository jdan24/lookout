"""The "pull a new snapshot from TT" panel, shared by reports that work on a client profile.

A pull only reads (every request goes through the read-only guard) and is limited to
the client's trees. It uses part of the team's shared daily TT call allowance, so it
runs only after the user ticks a confirmation box and presses the button.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

import core.config as app_config
from checks.setup.profile import Profile
from checks.setup.pull import pull_client
from core import credentials
from core.config import AppConfig
from core.readonly_http import ReadOnlyClient
from core.snapshots import Snapshot
from core.tt_client import TTClient

DAILY_CALL_LIMIT = 15_000  # the TT plan's limit, shared by everyone using the same key
HOW_TO_SET_REQUEST_ID = (
    "TT needs your company name in every request. In config.toml, change "
    'request_id_prefix = "Lookout-CHANGEME" to "Lookout-YourCompany" '
    "(letters and digits only, no spaces), then start Lookout again."
)


def make_tt_client(settings: AppConfig) -> tuple[TTClient, Callable[[], None]]:
    """A TT client built from the app settings, and a function that closes it."""
    if not settings.tt.request_id_is_set:
        raise ValueError(HOW_TO_SET_REQUEST_ID)
    environment = settings.tt.environment
    http = ReadOnlyClient(settings.tt.host, environment=environment)
    client = TTClient(
        http,
        lambda: credentials.load_credentials(environment),
        requests_per_second=settings.pacing.requests_per_second,
        max_calls=settings.pacing.max_calls_per_pull,
        request_id_prefix=settings.tt.request_id_prefix,
    )
    return client, http.close


def _run_pull(profile: Profile, settings: AppConfig) -> Snapshot | None:
    try:
        tt, close = make_tt_client(settings)
    except Exception as exc:  # noqa: BLE001 - shown to the user; nothing has been read yet
        st.error(f"Could not prepare the connection to TT: {exc}")
        return None
    line = st.empty()
    try:
        with st.spinner(
            f"Reading {profile.title} from TT {settings.tt.mode}. This can take several minutes."
        ):
            return pull_client(
                tt,
                settings.snapshot_dir,
                profile,
                otd_max_pages=settings.pacing.max_order_tag_default_pages,
                progress=lambda message: line.caption(message),
            )
    except credentials.CredentialError as exc:
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001 - the pull stopped; say why instead of a traceback
        st.error(f"The pull stopped unexpectedly ({type(exc).__name__}: {exc}).")
    finally:
        line.empty()
        close()
    return None


def _report(snapshot: Snapshot) -> None:
    calls = snapshot.manifest.get("calls", 0)
    log = snapshot.pull_log
    problems = [*log.get("errors", []), *log.get("notes", [])]
    if snapshot.status == "complete":
        st.success(f"Snapshot {snapshot.id} saved ({calls} TT calls).")
    elif snapshot.status == "partial":
        st.warning(
            f"Snapshot {snapshot.id} saved, but some data was not read ({calls} TT calls). "
            "Checks on the missing parts show amber."
        )
    else:
        st.error(f"The pull failed ({calls} TT calls); no usable snapshot was saved.")
    if problems:
        with st.expander("Pull log"):
            for problem in problems:
                st.write(f"- {problem}")


def pull_panel(profile: Profile, *, key: str, select_key: str, expanded: bool = False) -> None:
    """The pull controls. ``select_key`` is the snapshot picker's selectbox key; a new
    usable snapshot is selected there. Call this before the picker is drawn."""
    settings = app_config.load_config()
    cap = settings.pacing.max_calls_per_pull
    mode, environment = settings.tt.mode, settings.tt.environment
    with st.expander(f"Pull a new snapshot from TT {mode}", expanded=expanded):
        st.caption(
            f"Reads {profile.title}'s accounts from TT {mode} ({environment}; read-only, "
            "nothing is changed). "
            f"A pull uses up to {cap:,} calls of the team's {DAILY_CALL_LIMIT:,} a day, so "
            "use the latest snapshot when it is recent enough."
        )
        if not credentials.has_credentials(environment):
            which = "TT UAT" if settings.tt.is_uat else "TT"
            st.warning(
                f"No {which} credentials are stored on this PC. "
                + credentials.how_to_set(environment)
            )
            return
        if not settings.tt.request_id_is_set:
            st.warning(HOW_TO_SET_REQUEST_ID)
            return
        # The confirmation box gets a new key after each pull, so it starts unticked again.
        round_key = f"{key}_pull_round"
        done = st.session_state.get(round_key, 0)
        agreed = st.checkbox(
            f"Pull {profile.title} from TT {mode} now, using up to {cap:,} calls",
            key=f"{key}_pull_ok_{done}",
        )
        if not st.button("Pull now", disabled=not agreed, key=f"{key}_pull"):
            return
        st.session_state[round_key] = done + 1
        snapshot = _run_pull(profile, settings)
        if snapshot is None:
            return
        _report(snapshot)
        if snapshot.status != "failed":
            st.session_state[select_key] = snapshot.id
