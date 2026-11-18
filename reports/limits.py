"""Limits: the Limits app's two tools (Account limit upload, Risk and periodic audit), one per tab.

The page shows the Limits app (``jdan24/limit_compare``) exactly as it is built there:
a compiled, self-contained copy lives in ``vendor/limits_app/index.html`` and is never
edited here (see that folder's README to refresh it). Each tab loads the app straight
on its own tool. The app runs in the browser only: no TT calls, nothing sent anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

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


@st.cache_resource(show_spinner=False)
def load_app(path: Path = APP_FILE) -> str:
    return path.read_text(encoding="utf-8")


def app_for(html: str, route: str) -> str:
    """The app opening on ``route``: the hash is set before the app's own script runs."""
    snippet = f"<script>window.location.hash = {json.dumps(route)};</script>{HIDE_HOME_LINK}"
    head = html.find("<head>")
    if head < 0:
        raise ValueError("The Limits app file has no <head>; restore it from the Limits repo.")
    at = head + len("<head>")
    return html[:at] + snippet + html[at:]


def render() -> None:
    st.title(f"{ICON} {TITLE}")
    st.caption(DESCRIPTION + " Everything runs in this browser; nothing is sent to TT.")
    try:
        html = load_app()
    except OSError:
        st.error(
            f"The Limits app is missing ({APP_FILE}). Copy spa/dist/index.html from the "
            "Limits project there (see vendor/limits_app/README.md)."
        )
        return
    for tab, route in zip(st.tabs(list(TOOLS)), TOOLS.values(), strict=True):
        with tab:
            st.iframe(app_for(html, route), height=FRAME_HEIGHT)
