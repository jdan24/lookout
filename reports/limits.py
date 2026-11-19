"""Limits: the Limits app's two tools (Account limit upload, Risk and periodic audit), one per tab.

The page shows the Limits app (``jdan24/limit_compare``) exactly as it is built there:
a compiled, self-contained copy lives in ``vendor/limits_app/index.html`` and is never
edited here (see that folder's README to refresh it). Each tab loads the app straight
on its own tool. The app runs in the browser only: no TT calls, nothing sent anywhere.

Team lists: when ``[limits] shared_folder`` is set in ``config.toml``, the page reads
``tt_symbol_mappings.csv``, ``unavailable_products.csv`` and ``tt_exchange_products.csv``
(TT's product list, cut down here to futures) from it (read only) and hands them to the
Account limit upload tab. Each user's own saved mappings, unavailable products and
dropped TT file sit on top. They are read once per session; "Reload team lists" reads
them again.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import streamlit as st

import core.config as app_config
from core.team_lists import (
    MAPPINGS_FILE,
    TT_FILE,
    UNAVAILABLE_FILE,
    CommitError,
    SharedLists,
    commit_mappings,
    read_shared,
)

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
ACCOUNT_UPLOAD_ROUTE = "/account-upload"
TOOLS: dict[str, str] = {
    "Account limit upload": "/account-upload",
    "Risk and periodic audit": "/risk-audit",
}
FRAME_HEIGHT = 1600
# Hides the app's "← Risk Tools" home link: each tab holds one tool.
HIDE_HOME_LINK = '<style>header a[href="#/"] { display: none; }</style>'


SHARED_KEY = "lim_shared"


# Commit from the app to the team mapping file. The app (inside its frame) posts a
# message to this page; a small bridge component passes it to Python, which writes
# the file and hands an answer back to the frame. The token is this session's own, so
# only the frames this page made can ask for a commit.
COMMIT_MESSAGE = "lookout-limits/commit-mappings"
ACK_MESSAGE = "lookout-limits/ack"
TOKEN_KEY = "lim_commit_token"
ACK_KEY = "lim_commit_ack"
BRIDGE_JS = """
export default function (component) {
  const { data, setTriggerValue } = component;
  const w = window;
  w.__lookoutLimits = w.__lookoutLimits || {};
  const b = w.__lookoutLimits;
  b.token = data.token;
  b.send = setTriggerValue;
  if (!b.listening) {
    b.listening = true;
    w.addEventListener("message", (e) => {
      const m = e.data;
      if (e.origin !== w.location.origin || !m) return;
      if (m.type !== "lookout-limits/commit-mappings") return;
      if (!b.token || m.token !== b.token) return;
      b.send("commit", JSON.stringify({ id: m.id, token: m.token, mappings: m.mappings }));
    });
  }
  const ack = data.ack;
  if (ack && ack.id !== b.ackId) {
    b.ackId = ack.id;
    for (const f of document.querySelectorAll("iframe")) {
      const msg = { type: "lookout-limits/ack", ...ack };
      if (f.contentWindow) f.contentWindow.postMessage(msg, w.location.origin);
    }
  }
}
"""
assert COMMIT_MESSAGE in BRIDGE_JS and ACK_MESSAGE in BRIDGE_JS
_bridge = st.components.v2.component("lookout.limits_bridge", js=BRIDGE_JS)


def shared_script(shared: SharedLists, token: str | None = None) -> str:
    """The script that hands the team lists to the app (``window.__LIMITS_SHARED__``),
    and the commit token when committing to the team mapping file is possible.

    JSON is written so that no text in the files can close the script element.
    """
    payload = json.dumps(
        {
            "commitToken": token,
            "mappingsCsv": shared.mappings_csv,
            "unavailableCsv": shared.unavailable_csv,
            "ttCsv": shared.tt_csv,
            "ttFile": TT_FILE if shared.tt_csv else None,
            "ttModified": shared.tt_modified,
            "source": shared.folder,
        }
    ).replace("</", "<\\/")
    return f"<script>window.__LIMITS_SHARED__ = {payload};</script>"


@st.cache_resource(show_spinner=False)
def load_app(path: Path = APP_FILE) -> str:
    return path.read_text(encoding="utf-8")


def app_for(
    html: str, route: str, shared: SharedLists | None = None, token: str | None = None
) -> str:
    """The app opening on ``route`` (and given the team lists, if any): both are set
    before the app's own script runs."""
    snippet = f"<script>window.location.hash = {json.dumps(route)};</script>{HIDE_HOME_LINK}"
    if shared is not None and shared.folder:
        snippet = shared_script(shared, token) + snippet
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
    token = _commit_bridge(shared) if shared.folder else None
    for tab, route in zip(st.tabs(list(TOOLS)), TOOLS.values(), strict=True):
        with tab:
            # Only Account limit upload uses the team lists (and the TT list is large).
            team = shared if route == ACCOUNT_UPLOAD_ROUTE else None
            st.iframe(app_for(html, route, team, token if team else None), height=FRAME_HEIGHT)


def _commit_bridge(shared: SharedLists) -> str:
    """Mount the bridge, carry out a commit the app asked for, and return the token.

    The frames are not rebuilt after a commit (the team lists stay as read), so
    nothing loaded in the app is lost; the app updates itself from the answer.
    """
    token = st.session_state.setdefault(TOKEN_KEY, secrets.token_urlsafe(24))
    result = _bridge(
        key="lim_bridge",
        data={"token": token, "ack": st.session_state.get(ACK_KEY)},
        on_commit_change=lambda: None,
    )
    asked = getattr(result, "commit", None)
    if asked:
        st.session_state[ACK_KEY] = handle_commit(asked, token, Path(shared.folder))
        st.rerun()
    return token


def handle_commit(message: str, token: str, folder: Path) -> dict[str, object]:
    """Carry out one commit request from the app; the answer it gets back."""
    try:
        request = json.loads(message)
    except ValueError:
        return {"id": None, "ok": False, "message": "The commit request could not be read."}
    request_id = request.get("id") if isinstance(request, dict) else None
    if not isinstance(request, dict) or request.get("token") != token:
        return {"id": request_id, "ok": False, "message": "The commit request was refused."}
    try:
        done = commit_mappings(folder, request.get("mappings"))
    except CommitError as exc:
        return {"id": request_id, "ok": False, "message": str(exc)}
    if not done.added and not done.changed:
        text = "The team file already had these mappings; nothing changed."
    else:
        text = (
            f"Saved to the team file: {done.added} added, {done.changed} changed "
            f"({done.total} mappings in it now)."
        )
    return {"id": request_id, "ok": True, "message": text, "committed": request.get("mappings")}


def _team_lists() -> SharedLists:
    """The team lists for this session (read once; the button reads them again)."""
    folder = app_config.load_config().limits.shared_path
    if folder is None:
        return SharedLists()
    cached: SharedLists | None = st.session_state.get(SHARED_KEY)
    left, right = st.columns([3, 1])
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
    if cached.tt_csv:
        found.append(f"{TT_FILE} ({cached.tt_futures:,} futures)")
    left.caption(
        f"Team lists from {cached.folder}: "
        + (", ".join(found) if found else "no list files there yet")
        + "."
    )
    for problem in cached.problems:
        st.warning(problem)
    return cached
