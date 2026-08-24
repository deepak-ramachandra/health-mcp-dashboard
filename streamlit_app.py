"""Entrypoint / page router — run via `streamlit run streamlit_app.py`.

Uses Streamlit's st.navigation()/st.Page() API so each page's sidebar label
is set explicitly right here, independent of its filename. (Streamlit's
classic pages/ auto-discovery infers a page's label from its filename -
that's why the health dashboard used to show up as "streamlit app" in the
sidebar, since that's this file's name. st.navigation() supersedes that
auto-discovery entirely once it's called.)

This file carries no page content of its own - the dashboard and the
Research page both moved into pages/, same as before, just no longer
relying on filename-derived labels.
"""

from pathlib import Path

import streamlit as st

from ui_components import HIDE_MENU_STYLE

st.set_page_config(
    page_title="Health dashboard",
    page_icon=str(Path(__file__).parent / "icons8-dumbbell-100.png"),
    layout="wide",
)
st.markdown(HIDE_MENU_STYLE, unsafe_allow_html=True)

pg = st.navigation(
    [
        st.Page("pages/health_dashboard.py", title="Health dashboard", icon="🏋️", default=True),
        st.Page("pages/research.py", title="Research", icon="📚"),
    ]
)
pg.run()
