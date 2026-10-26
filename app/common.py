"""Shared state for the Streamlit app: one cached engine run per (config, decision time)."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from nba_engine.config import SNAPSHOT, EngineConfig  # noqa: E402
from nba_engine.decision_log import CHANNEL_LABELS, Decision  # noqa: E402
from nba_engine.loader import Dataset, default_dataset  # noqa: E402
from nba_engine.pipeline import Engine  # noqa: E402
from nba_engine.segments import customer_segments  # noqa: E402

DECISION_TIMES = {
    "Snapshot 30 Sep, 23:59": SNAPSHOT,
    "Replay 1 Oct, 12:00": pd.Timestamp("2026-10-01 12:00"),
    "Replay 1 Oct, 18:00": pd.Timestamp("2026-10-01 18:00"),
    "Replay 2 Oct, 06:00": pd.Timestamp("2026-10-02 06:00"),
}

DEMO_CASES = {
    "MG-100009": "Slide 7: Autostrom driver, failed charging (use 1 Oct, 18:00)",
    "MG-100001": "Slide 9: formal writer, weekly portal user",
    "MG-100060": "Slide 6: gas meter deregistered",
    "MG-100266": "Slide 6: wallbox bought in shop",
    "MG-100125": "Slide 6: three heat-pump clicks",
    "MG-100004": "Exclusion: dunning + open complaint",
}

STATUS_LABELS = {
    "proposed": "Proposed",
    "approved": "Approved",
    "rejected": "Rejected",
    "held_out": "Control group (not sent)",
    "no_action": "No action",
}


@dataclass
class Run:
    engine: Engine
    decisions: list[Decision]
    as_of: pd.Timestamp

    @property
    def by_customer(self) -> dict[str, Decision]:
        return {d.customer_id: d for d in self.decisions}


@st.cache_resource(show_spinner=False)
def dataset() -> Dataset:
    return default_dataset()


@st.cache_resource(show_spinner="Running the engine for all customers…", max_entries=8)
def _run(config_version: str, as_of: str, _cfg: EngineConfig) -> Run:
    engine = Engine(dataset(), _cfg)
    return Run(engine, engine.recommend(as_of=pd.Timestamp(as_of)), pd.Timestamp(as_of))


def config() -> EngineConfig:
    if "cfg" not in st.session_state:
        st.session_state.cfg = EngineConfig()
    return st.session_state.cfg


def decision_time() -> pd.Timestamp:
    return DECISION_TIMES[st.session_state.get("decision_time", next(iter(DECISION_TIMES)))]


def current_run() -> Run:
    cfg = config()
    return _run(cfg.version(), str(decision_time()), cfg)


@st.cache_data(show_spinner=False)
def segments() -> pd.DataFrame:
    ds = dataset()
    return pd.DataFrame({
        "segment": customer_segments(ds),
        "clv_eur": ds.customer_value.set_index("customer_id")["clv_eur"],
    })


def decisions_frame(run: Run) -> pd.DataFrame:
    seg = segments()
    rows = []
    for d in run.decisions:
        rows.append({
            "customer_id": d.customer_id,
            "segment": seg.at[d.customer_id, "segment"],
            "status": STATUS_LABELS[run.engine.log.status(d.decision_id)],
            "offer": d.offer_name or "—",
            "offer_id": d.offer_id,
            "type": d.action_type or "—",
            "channel": CHANNEL_LABELS.get(d.channel, "—"),
            "tone": (d.tone or "—").capitalize(),
            "p_accept": d.p_accept,
            "expected_dclv": d.dclv,
            "clv_now": d.inputs["clv_reported"],
            "signal": d.inputs.get("signal_used") or "",
            "profiling": d.inputs["profiling_allowed"],
            "reason": d.reason,
            "decision_id": d.decision_id,
        })
    return pd.DataFrame(rows)


def eur(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"€{x:,.0f}"
