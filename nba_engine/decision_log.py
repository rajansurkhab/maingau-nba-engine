"""Decision log (slide 8 'Explain', slide 12 'Explainability') and one-line reasons.

Every decision, including "no action", is logged with inputs, rule trace, scores and the
chosen action. The log is append-only: approval or rejection by marketing (human in the
loop, slide 12) appends a status entry instead of editing the decision.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from nba_engine.arbitration import Option
from nba_engine.config import SALES_ACTION_TYPES
from nba_engine.eligibility import EligibilityResult
from nba_engine.scoring import CustomerValue, Score
from nba_engine.state import CustomerState

# proposed -> approved / rejected by marketing; held_out = control group, never sent
DECISION_STATUSES = ("proposed", "held_out", "no_action")
REVIEW_STATUSES = ("approved", "rejected")

DRIVER_LABELS = {
    "price_guarantee_ends_90d": "price guarantee ends <90d",
    "open_complaint": "open complaint",
    "tickets_12m_ge2": "repeated tickets",
    "csat_le2": "low CSAT",
    "failed_charges_30d_ge3": "failed charging sessions",
    "dunning_ge2": "dunning",
}
SERVICE_DRIVER_KEYS = ("open_complaint", "tickets_12m_ge2", "csat_le2", "failed_charges_30d_ge3")
CHANNEL_LABELS = {
    "newsletter_email": "newsletter",
    "email": "e-mail",
    "sms": "SMS",
    "outbound_call": "call",
    "portal_popup": "portal pop-up",
    "app_push": "app push",
}


@dataclass
class Decision:
    decision_id: str
    customer_id: str
    as_of: str
    created_at: str
    status: str
    reason: str
    offer_id: str | None = None
    offer_name: str | None = None
    action_type: str | None = None
    channel: str | None = None
    tone: str | None = None
    p_accept: float | None = None
    dclv: float | None = None
    clv_now: float | None = None
    clv_accept: float | None = None
    clv_reject: float | None = None
    contact_cost: float | None = None
    control_group: bool = False
    alternatives: list[dict] = field(default_factory=list)
    rule_trace: list[dict] = field(default_factory=list)
    blocked: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)
    config_version: str = ""

    @property
    def has_action(self) -> bool:
        return self.offer_id is not None


class DecisionLog:
    """Append-only log. With a path, every entry is also appended to a JSONL file."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.entries: list[dict] = []
        self._decisions: dict[str, Decision] = {}
        self._status: dict[str, str] = {}

    def _append(self, entry: dict) -> None:
        self.entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def record(self, decision: Decision) -> None:
        if decision.status not in DECISION_STATUSES:
            raise ValueError(f"invalid decision status {decision.status}")
        self._decisions[decision.decision_id] = decision
        self._status[decision.decision_id] = decision.status
        self._append({"kind": "decision", **asdict(decision)})

    def set_status(self, decision_id: str, status: str, actor: str, ts: str, note: str = "") -> None:
        """Marketing approves or rejects a proposed decision."""
        if status not in REVIEW_STATUSES:
            raise ValueError(f"status must be one of {REVIEW_STATUSES}")
        current = self._status.get(decision_id)
        if current != "proposed":
            raise ValueError(f"decision {decision_id} is {current}, only proposed decisions can be reviewed")
        self._status[decision_id] = status
        self._append({"kind": "status", "decision_id": decision_id, "status": status,
                      "actor": actor, "ts": ts, "note": note})

    def status(self, decision_id: str) -> str:
        return self._status[decision_id]

    def get(self, decision_id: str) -> Decision:
        return self._decisions[decision_id]

    def decisions(self) -> list[Decision]:
        return list(self._decisions.values())

    def to_frame(self) -> pd.DataFrame:
        rows = [
            {**{k: v for k, v in asdict(d).items() if k not in ("alternatives", "rule_trace", "inputs")},
             "status": self._status[d.decision_id]}
            for d in self._decisions.values()
        ]
        return pd.DataFrame(rows)


# --- reason text --------------------------------------------------------------------------

def _eur(x: float) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}€{abs(x):,.0f}"


def _first_exclusion(scored: list[tuple[EligibilityResult, Score]]) -> str | None:
    return next(
        (r.blocked_by.detail for r, _ in scored if r.blocked_by and r.blocked_by.rule_id.startswith("R-EXCL")),
        None,
    )


def _sales_context(scored: list[tuple[EligibilityResult, Score]]) -> str:
    """Why no sales offer: blocked by a hard rule, or economically negative."""
    sales = [(r, s) for r, s in scored if r.candidate.offer.action_type in SALES_ACTION_TYPES]
    if not sales:
        return ""
    exclusion = _first_exclusion(sales)
    if exclusion:
        return f"; sales blocked ({exclusion})"
    eligible = [(r, s) for r, s in sales if r.eligible]
    if eligible:
        r, s = max(eligible, key=lambda rs: rs[1].dclv)
        if s.dclv <= 0:
            why = " during open service case" if s.reject_penalty else ""
            return f"; best sales offer {r.candidate.offer.name} scores {_eur(s.dclv)}{why}"
    return ""


def reason_for(
    chosen: Option | None,
    state: CustomerState,
    cv: CustomerValue,
    scored: list[tuple[EligibilityResult, Score]],
    notes: list[str],
) -> str:
    """One line a marketer can read (slide 8). Deterministic, built from drivers and scores."""
    suffix = f" [{'; '.join(notes)}]" if notes else ""
    if chosen is None:
        eligible = [(r, s) for r, s in scored if r.eligible]
        if not eligible:
            channel_blocks = all(
                r.blocked_by.rule_id in ("R-CONSENT-CHANNEL", "R-CHANNEL-REACHABLE") for r, _ in scored
            )
            if channel_blocks:
                return "No action: no channel with consent" + suffix
            return f"No action: {_first_exclusion(scored) or 'no eligible offer'}" + suffix
        r, s = max(eligible, key=lambda rs: rs[1].dclv)
        if s.dclv <= 0:
            return (f"No action: best eligible option {r.candidate.offer.name} via "
                    f"{CHANNEL_LABELS[s.channel]} scores {_eur(s.dclv)} expected CLV") + suffix
        return "No action: positive options disabled or over capacity" + suffix

    s, offer = chosen.score, chosen.offer
    channel = CHANNEL_LABELS[s.channel]
    if offer.action_type == "service":
        drivers = [DRIVER_LABELS[d] for d in cv.drivers if d in SERVICE_DRIVER_KEYS] or ["open service case"]
        text = (f"Service first: {', '.join(drivers)} raise churn; proactive fix via {channel} "
                f"{_eur(s.dclv)} expected CLV") + _sales_context(scored)
    elif offer.action_type == "retention":
        text = (f"Price guarantee ends <90d: renewal via {channel} avoids churn, "
                f"{_eur(s.dclv)} expected CLV")
    else:
        text = (f"{offer.name} via {channel}: {s.p_accept:.0%} acceptance × "
                f"{_eur(s.gain_if_accept)} CLV → {_eur(s.dclv)} expected")
    return text + suffix
