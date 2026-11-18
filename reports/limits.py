"""Limits: the Limits app's two tools (Account limit upload, Risk and periodic audit), one per tab.

The page shows the Limits app (``jdan24/limit_compare``) exactly as it is built there:
a compiled, self-contained copy lives in ``vendor/limits_app/index.html`` and is never
edited here (see that folder's README to refresh it). Each tab loads the app straight
on its own tool. The app runs in the browser only: no TT calls, nothing sent anywhere.

Team lists: when ``[limits] shared_folder`` is set in ``config.toml``, the page reads
``tt_symbol_mappings.csv`` and ``unavailable_products.csv`` from it (read only) and hands
them to the app, which uses them under each user's own saved mappings and unavailable
products. They are read once per session; "Reload team lists" reads them again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

import core.config as app_config

TITLE = "Limits"
DESCRIPTION = (
    "Account limit upload: a client limit table matched to TT products (unavailable "
    "products greyed out), checked against the Risk System Export, with the risk-tool "
    "upload CSVs. Risk and periodic audit: a client's product universe against the "
    "risk system's limits, with the risk review and upload."
)
ICON = "📏"
CATEGORY = "Limits"

APP_FILE = Path(__file__).resolve().parent.parent / "vendor" / "limits_app" / "index.html"
TOOLS: dict[str, str] = {
    "Account limit upload": "/account-upload",
    "Risk and periodic audit": "/risk-audit",
}
FRAME_HEIGHT = 1600
# Hides the app's "← Risk Tools" home link: each tab holds one tool.
HIDE_HOME_LINK = '<style>header a[href="#/"] { display: none; }</style>'


MAPPINGS_FILE = "tt_symbol_mappings.csv"
UNAVAILABLE_FILE = "unavailable_products.csv"
MAX_SHARED_BYTES = 5_000_000  # a team list is a few KB; refuse anything absurd
SHARED_KEY = "lim_shared"


@dataclass(frozen=True)
class SharedLists:
    """The team's lists as CSV text (None when a file is absent), and any problems."""

    folder: str = ""
    mappings_csv: str | None = None
    unavailable_csv: str | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)


def read_shared(folder: Path | None) -> SharedLists:
    """Read the two team files from ``folder``. A missing file is fine; never raises."""
    if folder is None:
        return SharedLists()
    if not folder.is_dir():
        return SharedLists(
            str(folder), problems=(f"The team lists folder {folder} can't be reached.",)
        )
    texts: dict[str, str | None] = {}
    problems: list[str] = []
    for name in (MAPPINGS_FILE, UNAVAILABLE_FILE):
        path = folder / name
        texts[name] = None
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_SHARED_BYTES:
                problems.append(f"{path} is too large to be a team list; it was skipped.")
                continue
            texts[name] = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError as exc:
            problems.append(f"Could not read {path}: {exc}")
    return SharedLists(str(folder), texts[MAPPINGS_FILE], texts[UNAVAILABLE_FILE], tuple(problems))


def shared_script(shared: SharedLists) -> str:
    """The script that hands the team lists to the app (``window.__LIMITS_SHARED__``).

    JSON is written so that no text in the files can close the script element.
    """
    payload = json.dumps(
        {
            "mappingsCsv": shared.mappings_csv,
            "unavailableCsv": shared.unavailable_csv,
            "source": shared.folder,
        }
    ).replace("</", "<\\/")
    return f"<script>window.__LIMITS_SHARED__ = {payload};</script>"


@st.cache_resource(show_spinner=False)
def load_app(path: Path = APP_FILE) -> str:
    return path.read_text(encoding="utf-8")


def app_for(html: str, route: str, shared: SharedLists | None = None) -> str:
    """The app opening on ``route`` (and given the team lists, if any): both are set
    before the app's own script runs."""
    snippet = f"<script>window.location.hash = {json.dumps(route)};</script>{HIDE_HOME_LINK}"
    if shared is not None and shared.folder:
        snippet = shared_script(shared) + snippet
    head = html.find("<head>")
    if head < 0:
        raise ValueError("The Limits app file has no <head>; restore it from the Limits repo.")
    at = head + len("<head>")
    return html[:at] + snippet + html[at:]


def render() -> None:
    st.title(f"{ICON} {TITLE}")
    st.caption(DESCRIPTION + " Everything runs in this browser; nothing is sent to TT.")
    try:
        html = load_app(APP_FILE)
    except OSError:
        st.error(
            f"The Limits app is missing ({APP_FILE}). Copy spa/dist/index.html from the "
            "Limits project there (see vendor/limits_app/README.md)."
        )
        return
    shared = _team_lists()
    for tab, route in zip(st.tabs(list(TOOLS)), TOOLS.values(), strict=True):
        with tab:
            st.iframe(app_for(html, route, shared), height=FRAME_HEIGHT)


def _team_lists() -> SharedLists:
    """The team lists for this session (read once; the button reads them again)."""
    folder = app_config.load_config().limits.shared_path
    if folder is None:
        return SharedLists()
    cached: SharedLists | None = st.session_state.get(SHARED_KEY)
    left, right = st.columns([5, 1])
    if (
        right.button("Reload team lists", key="lim_reload")
        or cached is None
        or cached.folder != str(folder)
    ):
        cached = read_shared(folder)
        st.session_state[SHARED_KEY] = cached
    found = [
        n
        for n, t in (
            (MAPPINGS_FILE, cached.mappings_csv),
            (UNAVAILABLE_FILE, cached.unavailable_csv),
        )
        if t
    ]
    left.caption(
        f"Team lists from {cached.folder}: "
        + (", ".join(found) if found else "no list files there yet")
        + "."
    )
    for problem in cached.problems:
        st.warning(problem)
    return cached
