"""MAINGAU Next Best Action – prototype UI.

Run from the repository root:
    .venv/Scripts/python -m streamlit run app/streamlit_app.py
"""
import streamlit as st

from common import DECISION_TIMES, config, current_run

st.set_page_config(page_title="MAINGAU Next Best Action", layout="wide")

pages = st.navigation([
    st.Page("customer_360.py", title="Customer 360°", icon=":material/person_search:", default=True),
    st.Page("marketing.py", title="Marketing dashboard", icon=":material/tune:"),
])

with st.sidebar:
    st.markdown("### MAINGAU Next Best Action")
    st.selectbox(
        "Decision time",
        list(DECISION_TIMES),
        key="decision_time",
        help="Snapshot = master view of 30 Sep. Replay applies the 1 Oct event stream "
             "up to that time (only events already ingested).",
    )
    run = current_run()
    st.caption(f"Config version `{config().version()}` · {len(run.decisions)} customers scored")
    st.caption("The engine proposes, marketing approves. Nothing is sent automatically.")

pages.run()
