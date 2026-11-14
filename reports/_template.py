"""Template for a new report. Copy this file, rename it, and fill it in.

How to add a report:

1. Copy this file to ``reports/<your_report>.py`` (lowercase, no leading underscore).
   The file name becomes the page address, e.g. ``/your_report``.
2. Set the four constants below and write ``render()``.
3. Keep this file to UI only. Put the checking logic in ``checks/`` (generic) or
   ``clients/<client>/`` (client-specific) as plain functions with no Streamlit,
   and return rows using the result model in ``core/results.py``.
4. Read TT data from snapshots (``core/snapshots.py``), never from live responses.

Do not edit ``app.py``; it finds the new file by itself.
"""

from __future__ import annotations

import streamlit as st

TITLE = "Report name"
DESCRIPTION = "One sentence saying what this report checks."
ICON = "📋"
CATEGORY = "General"


def render() -> None:
    st.title(f"{ICON} {TITLE}")
    st.write(DESCRIPTION)
    st.info("This is the template. Replace this with the report.")
