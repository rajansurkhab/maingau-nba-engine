"""End-to-end pipeline: Eligibility -> Scoring -> Arbitration -> Delivery & Log.

`Engine.recommend()` is the API entry point that later maps 1:1 to an MCP tool.
Channel & Tone is still a stub (tone defaults to "Sie"); Content is not built yet.
"""
from __future__ import annotations

import uuid
from collections import Counter
from hashlib import sha256
from pathlib import Path

import pandas as pd

from nba_engine.acceptance import AcceptanceModel
from nba_engine.arbitration import Option, arbitrate, make_option
from nba_engine.config import SALES_ACTION_TYPES, SNAPSHOT, EngineConfig
from nba_engine.decision_log import Decision, DecisionLog, reason_for
from nba_engine.eligibility import EligibilityResult, evaluate_all, offers_from_catalog
from nba_engine.loader import Dataset, default_dataset
from nba_engine.scoring import CustomerValue, ProductEconomics, Score, ValueParams, customer_values, score
from nba_engine.state import CustomerState, build_states


def in_control_group(customer_id: str, share: float) -> bool:
    """Stable hash split, so a customer stays in the same group across runs."""
    bucket = int(sha256(customer_id.encode()).hexdigest()[:8], 16) % 10_000
    return bucket < share * 10_000


class Engine:
    def __init__(self, ds: Dataset | None = None, cfg: EngineConfig | None = None, log_path: Path | None = None):
        self.ds = ds or default_dataset()
        self.cfg = cfg or EngineConfig()
        self.params = ValueParams.from_assumptions(self.ds.value_assumptions)
        self.values = customer_values(self.ds.customer_value)
        self.products = ProductEconomics.from_catalog(self.ds.product_catalog)
        self.offers = offers_from_catalog(self.ds.product_catalog)
        self.model = AcceptanceModel.fit(
            self.ds.nba_action_history,
            self.ds.product_catalog,
            strength=self.cfg.shrinkage_strength,
            type_priors={"service": self.cfg.service_p_accept_prior},
        )
        self.log = DecisionLog(log_path)
        self._states: dict[pd.Timestamp, dict[str, CustomerState]] = {}

    def states(self, as_of: pd.Timestamp) -> dict[str, CustomerState]:
        if as_of not in self._states:
            self._states[as_of] = build_states(self.ds, as_of)
        return self._states[as_of]

    def tone_of(self, state: CustomerState) -> str:
        return "sie"  # placeholder until the Channel & Tone stage derives Du/Sie from texts

    def evaluate_customer(self, state: CustomerState) -> list[tuple[EligibilityResult, Score]]:
        """All candidates with their rule trace and score. Blocked ones are scored too:
        their (often negative) dCLV is the economic reason shown next to the hard rule."""
        cv = self.values[state.customer_id]
        tone = self.tone_of(state)
        out = []
        for result in evaluate_all(state, self.offers, cfg=self.cfg):
            offer, channel = result.candidate.offer, result.candidate.channel
            p = self.model.p(offer.offer_id, channel, tone)
            s = score(state, cv, self.products[offer.offer_id], channel, tone, p, self.params, self.cfg)
            out.append((result, s))
        return out

    def recommend(
        self,
        customer_ids: list[str] | None = None,
        as_of: pd.Timestamp = SNAPSHOT,
    ) -> list[Decision]:
        states = self.states(as_of)
        ids = customer_ids if customer_ids is not None else list(states)
        evaluated = {cid: self.evaluate_customer(states[cid]) for cid in ids}
        options = {
            cid: [make_option(r.candidate.offer, s, self.cfg) for r, s in scored if r.eligible]
            for cid, scored in evaluated.items()
        }
        arb = arbitrate(options, self.cfg)

        decisions = []
        for cid in ids:
            d = self._decision(states[cid], self.values[cid], evaluated[cid], arb.chosen[cid],
                               arb.ranked[cid], arb.notes[cid], as_of)
            self.log.record(d)
            decisions.append(d)
        return decisions

    def _decision(
        self,
        state: CustomerState,
        cv: CustomerValue,
        scored: list[tuple[EligibilityResult, Score]],
        chosen: Option | None,
        ranked: list[Option],
        notes: list[str],
        as_of: pd.Timestamp,
    ) -> Decision:
        d = Decision(
            decision_id=uuid.uuid4().hex[:12],
            customer_id=state.customer_id,
            as_of=str(as_of),
            created_at=str(pd.Timestamp.now().floor("s")),
            status="no_action",
            reason=reason_for(chosen, state, cv, scored, notes),
            blocked=dict(Counter(r.blocked_by.rule_id for r, _ in scored if r.blocked_by)),
            notes=list(notes),
            inputs=_inputs(state, cv),
            config_version=self.cfg.version(),
            alternatives=[_option_summary(o) for o in ranked],
        )
        if chosen is None:
            return d

        s, offer = chosen.score, chosen.offer
        trace = next(r.trace for r, sc in scored if sc is s)
        held_out = offer.action_type in SALES_ACTION_TYPES and in_control_group(state.customer_id, self.cfg.control_share)
        d.status = "held_out" if held_out else "proposed"
        d.control_group = held_out
        d.offer_id, d.offer_name, d.action_type = offer.offer_id, offer.name, offer.action_type
        d.channel, d.tone = s.channel, s.tone
        d.p_accept, d.dclv, d.contact_cost = s.p_accept, s.dclv, s.contact_cost
        d.clv_now, d.clv_accept, d.clv_reject = s.clv_now, s.clv_accept, s.clv_reject
        d.rule_trace = [{"rule": r.rule_id, "passed": r.passed, "detail": r.detail} for r in trace]
        return d


def _option_summary(o: Option) -> dict:
    return {"offer_id": o.offer.offer_id, "offer_name": o.offer.name, "channel": o.channel,
            "dclv": round(o.score.dclv, 2), "p_accept": round(o.score.p_accept, 4), "priority": o.priority}


def _inputs(state: CustomerState, cv: CustomerValue) -> dict:
    """Snapshot of what the decision was based on. Payment data appears only as an exclusion flag."""
    return {
        "consents": dict(state.consents),
        "profiling_allowed": state.profiling_allowed,
        "excluded_dunning": state.dunning_level >= 1,
        "open_complaint": state.open_complaint,
        "open_service_case": state.open_service_case,
        "cancellation_received": state.cancellation_received,
        "sales_contacts_30d": state.sales_contacts_30d,
        "failed_charges_30d": state.failed_charges_30d,
        "churn": cv.churn,
        "churn_drivers": list(cv.drivers),
        "annual_margin": cv.annual_margin,
        "cost_to_serve": cv.cost_to_serve,
        "clv_reported": cv.clv_reported,
    }


def recommend(
    customer_ids: list[str] | None = None,
    as_of: pd.Timestamp = SNAPSHOT,
    cfg: EngineConfig | None = None,
) -> list[Decision]:
    """Convenience wrapper: one engine, one batch run."""
    return Engine(cfg=cfg).recommend(customer_ids, as_of)
