"""End-to-end pipeline: Eligibility -> Scoring -> Arbitration -> Channel & Tone -> Delivery & Log.

`Engine.recommend()` is the API entry point that later maps 1:1 to an MCP tool.
Channel & Tone works inside scoring: tone is detected per customer, channel affinity and
trigger signals adjust p_accept per candidate, and arbitration then picks the channel with
the best dCLV. Content (German templates) is not built yet.
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

import pandas as pd

from nba_engine.acceptance import AcceptanceModel
from nba_engine.arbitration import Option, arbitrate, make_option
from nba_engine.channel_tone import AffinityModel, ChannelAffinity, ToneDetector, ToneResult
from nba_engine.config import SALES_ACTION_TYPES, SNAPSHOT, EngineConfig
from nba_engine.decision_log import Decision, DecisionLog, reason_for
from nba_engine.eligibility import EligibilityResult, evaluate_all, offers_from_catalog
from nba_engine.loader import Dataset, default_dataset
from nba_engine.scoring import CustomerValue, ProductEconomics, Score, ValueParams, customer_values, score
from nba_engine.signals import Signal, SignalDetector, apply_odds, apply_signal, strongest_for
from nba_engine.state import CustomerState, build_states


def in_control_group(customer_id: str, share: float) -> bool:
    """Stable hash split, so a customer stays in the same group across runs."""
    bucket = int(sha256(customer_id.encode()).hexdigest()[:8], 16) % 10_000
    return bucket < share * 10_000


@dataclass
class CustomerContext:
    """Everything the engine derived for one customer before arbitration."""

    state: CustomerState
    value: CustomerValue
    tone: ToneResult
    signals: list[Signal]
    affinity: ChannelAffinity
    scored: list[tuple[EligibilityResult, Score]] = field(default_factory=list)
    p_base: dict[tuple[str, str], float] = field(default_factory=dict)  # (offer, channel) -> history rate


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
        self.intent_model = AcceptanceModel.fit(
            self.ds.nba_action_history,
            self.ds.product_catalog,
            strength=self.cfg.shrinkage_strength,
            clicked_only=True,
        )
        self.signal_detector = SignalDetector(self.ds, self.cfg)
        self.tone_detector = ToneDetector(self.ds, self.cfg)
        self.affinity_model = AffinityModel(self.ds, self.cfg)
        self.log = DecisionLog(log_path)
        self._states: dict[pd.Timestamp, dict[str, CustomerState]] = {}

    def states(self, as_of: pd.Timestamp) -> dict[str, CustomerState]:
        if as_of not in self._states:
            self._states[as_of] = build_states(self.ds, as_of)
        return self._states[as_of]

    def p_accept(self, ctx: CustomerContext, offer_id: str, channel: str) -> float:
        """History rate; with profiling consent, a trigger signal moves it toward the
        after-click rate and channel affinity multiplies the odds. Capped at max_p_accept."""
        tone = ctx.tone.tone
        base = self.model.p(offer_id, channel, tone)
        ctx.p_base[(offer_id, channel)] = base
        p = base
        signal = strongest_for(ctx.signals, offer_id)
        if signal:
            p = apply_signal(p, self.intent_model.p(offer_id, channel, tone), signal.strength)
        factor = ctx.affinity.factor(channel)
        if factor != 1.0:
            p = apply_odds(p, factor)
        return min(p, max(self.cfg.max_p_accept, base))

    def evaluate_customer(self, state: CustomerState, as_of: pd.Timestamp = SNAPSHOT) -> CustomerContext:
        """All candidates with their rule trace and score. Blocked ones are scored too:
        their (often negative) dCLV is the economic reason shown next to the hard rule."""
        ctx = CustomerContext(
            state=state,
            value=self.values[state.customer_id],
            tone=self.tone_detector.detect(state, as_of),
            signals=self.signal_detector.detect(state, as_of),
            affinity=self.affinity_model.affinity(state, as_of),
        )
        for result in evaluate_all(state, self.offers, cfg=self.cfg):
            offer, channel = result.candidate.offer, result.candidate.channel
            p = self.p_accept(ctx, offer.offer_id, channel)
            s = score(state, ctx.value, self.products[offer.offer_id], channel, ctx.tone.tone, p, self.params, self.cfg)
            ctx.scored.append((result, s))
        return ctx

    def recommend(
        self,
        customer_ids: list[str] | None = None,
        as_of: pd.Timestamp = SNAPSHOT,
    ) -> list[Decision]:
        states = self.states(as_of)
        ids = customer_ids if customer_ids is not None else list(states)
        contexts = {cid: self.evaluate_customer(states[cid], as_of) for cid in ids}
        options = {
            cid: [make_option(r.candidate.offer, s, self.cfg) for r, s in ctx.scored if r.eligible]
            for cid, ctx in contexts.items()
        }
        arb = arbitrate(options, self.cfg)

        decisions = []
        for cid in ids:
            d = self._decision(contexts[cid], arb.chosen[cid], arb.ranked[cid], arb.notes[cid], as_of)
            self.log.record(d)
            decisions.append(d)
        return decisions

    def _decision(
        self,
        ctx: CustomerContext,
        chosen: Option | None,
        ranked: list[Option],
        notes: list[str],
        as_of: pd.Timestamp,
    ) -> Decision:
        state = ctx.state
        notes = list(notes) + [
            f"{s.label} not used: no profiling consent" for s in ctx.signals if not s.used
        ]
        signal = strongest_for(ctx.signals, chosen.offer.offer_id) if chosen else None
        d = Decision(
            decision_id=uuid.uuid4().hex[:12],
            customer_id=state.customer_id,
            as_of=str(as_of),
            created_at=str(pd.Timestamp.now().floor("s")),
            status="no_action",
            reason=reason_for(chosen, state, ctx.value, ctx.scored, notes, signal=signal, tone=ctx.tone.tone,
                              channel_evidence=ctx.affinity.evidence.get(chosen.channel) if chosen and ctx.affinity.used else None),
            blocked=dict(Counter(r.blocked_by.rule_id for r, _ in ctx.scored if r.blocked_by)),
            notes=notes,
            inputs=_inputs(ctx),
            config_version=self.cfg.version(),
            alternatives=[_option_summary(o) for o in ranked],
        )
        if chosen is None:
            return d

        s, offer = chosen.score, chosen.offer
        trace = next(r.trace for r, sc in ctx.scored if sc is s)
        held_out = offer.action_type in SALES_ACTION_TYPES and in_control_group(state.customer_id, self.cfg.control_share)
        d.status = "held_out" if held_out else "proposed"
        d.control_group = held_out
        d.offer_id, d.offer_name, d.action_type = offer.offer_id, offer.name, offer.action_type
        d.channel, d.tone = s.channel, s.tone
        d.p_accept, d.dclv, d.contact_cost = s.p_accept, s.dclv, s.contact_cost
        d.clv_now, d.clv_accept, d.clv_reject = s.clv_now, s.clv_accept, s.clv_reject
        d.rule_trace = [{"rule": r.rule_id, "passed": r.passed, "detail": r.detail} for r in trace]
        d.inputs["p_accept_base"] = ctx.p_base[(offer.offer_id, s.channel)]
        d.inputs["signal_used"] = signal.signal_id if signal else None
        d.inputs["channel_factor"] = ctx.affinity.factor(s.channel)
        return d


def _option_summary(o: Option) -> dict:
    return {"offer_id": o.offer.offer_id, "offer_name": o.offer.name, "channel": o.channel,
            "dclv": round(o.score.dclv, 2), "p_accept": round(o.score.p_accept, 4), "priority": o.priority}


def _inputs(ctx: CustomerContext) -> dict:
    """Snapshot of what the decision was based on. Payment data appears only as an exclusion flag."""
    state, cv = ctx.state, ctx.value
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
        "tone": ctx.tone.tone,
        "tone_evidence": ctx.tone.evidence,
        "signals": [{"id": s.signal_id, "evidence": s.evidence, "used": s.used, "strength": s.strength}
                    for s in ctx.signals],
        "channel_affinity": {ch: round(f, 2) for ch, f in ctx.affinity.factors.items()} if ctx.affinity.used else {},
        "channel_evidence": dict(ctx.affinity.evidence) if ctx.affinity.used else {},
    }


def recommend(
    customer_ids: list[str] | None = None,
    as_of: pd.Timestamp = SNAPSHOT,
    cfg: EngineConfig | None = None,
) -> list[Decision]:
    """Convenience wrapper: one engine, one batch run."""
    return Engine(cfg=cfg).recommend(customer_ids, as_of)
