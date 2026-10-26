"""End-to-end pipeline: Eligibility -> Scoring -> Arbitration -> Channel & Tone -> Delivery & Log.

`Engine.recommend()` is the API entry point that later maps 1:1 to an MCP tool.
Channel & Tone works inside scoring: tone is detected per customer, channel affinity and
trigger signals adjust p_accept per candidate, and arbitration then picks the channel with
the best dCLV. Content templates live in `content.py` and are attached by the API.

Optional ML upgrades (config): `solver="lp"` (V3), `exploration="thompson"` (V1). Every
decision logs its policy and propensity (V6-prep) for later off-policy evaluation.
"""
from __future__ import annotations

import random
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
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
        base = self.model.p(offer_id, channel, ctx.tone.tone)
        ctx.p_base[(offer_id, channel)] = base
        return self._personalise(ctx, offer_id, channel, base)

    def _personalise(self, ctx: CustomerContext, offer_id: str, channel: str, base: float) -> float:
        p = base
        signal = strongest_for(ctx.signals, offer_id)
        if signal:
            p = apply_signal(p, self.intent_model.p(offer_id, channel, ctx.tone.tone), signal.strength)
        factor = ctx.affinity.factor(channel)
        if factor != 1.0:
            p = apply_odds(p, factor)
        return min(p, max(self.cfg.max_p_accept, base))

    # --- ML-Upgrade V1: Thompson sampling -------------------------------------------------

    def _explorable(self, option: Option) -> bool:
        """Positive sales options that arbitration may pick. Service is never sampled."""
        return (option.offer.action_type != "service" and option.score.dclv > 0
                and option.offer.offer_id not in self.cfg.disabled_offers
                and option.channel not in self.cfg.disabled_channels)

    def _sampler(self, ctx: CustomerContext, option: Option):
        """Returns rng -> sampled dCLV. Everything except the Beta draw is precomputed.

        dCLV is linear in p: dCLV = L − c + p·(G − L), so only p is drawn; the draw then
        goes through the same personalisation (signal, affinity, cap) as the expected value.
        """
        s = option.score
        alpha, beta = self.model.posterior(s.offer_id, s.channel, s.tone)
        k = self.cfg.exploration_concentration
        alpha, beta = alpha * k, beta * k
        signal = strongest_for(ctx.signals, s.offer_id)
        p_intent = self.intent_model.p(s.offer_id, s.channel, s.tone) if signal else None
        factor = ctx.affinity.factor(s.channel)
        base_value, slope = s.loss_if_reject - s.contact_cost, s.gain_if_accept - s.loss_if_reject
        cap = self.cfg.max_p_accept

        def draw(rng: random.Random) -> float:
            base = rng.betavariate(alpha, beta)
            p = apply_signal(base, p_intent, signal.strength) if signal else base
            if factor != 1.0:
                p = apply_odds(p, factor)
            return base_value + min(p, max(cap, base)) * slope

        return draw

    def _options(self, ctx: CustomerContext) -> list[Option]:
        options = [make_option(r.candidate.offer, s, self.cfg) for r, s in ctx.scored if r.eligible]
        if self.cfg.exploration != "thompson":
            return options
        rng = random.Random(f"{self.cfg.exploration_seed}:{ctx.state.customer_id}")
        return [replace(o, sample_dclv=self._sampler(ctx, o)(rng)) if self._explorable(o) else o for o in options]

    def _propensity(self, ctx: CustomerContext, options: list[Option], chosen: Option) -> tuple[float, str]:
        """ML-Upgrade V6-prep: probability that this policy picks `chosen` for the customer.

        Deterministic policies log 1.0. Under exploration it is estimated by Monte Carlo over
        fresh posterior draws (per-customer argmax, before channel capacity). The draw that
        was actually used counts as one observation, so the estimate is never 0:
        (hits + 1) / (draws + 1).
        """
        if self.cfg.exploration != "thompson":
            return 1.0, "deterministic"
        rng = random.Random(f"{self.cfg.exploration_seed}:{ctx.state.customer_id}:propensity")
        candidates = []
        for o in options:
            if o.score.dclv > 0 and o.offer.offer_id not in self.cfg.disabled_offers \
                    and o.channel not in self.cfg.disabled_channels:
                fixed = None if self._explorable(o) else o.score.dclv * o.priority
                candidates.append(((o.offer.offer_id, o.channel), o.priority, fixed,
                                   self._sampler(ctx, o) if fixed is None else None))
        key = (chosen.offer.offer_id, chosen.channel)
        hits = 0
        for _ in range(self.cfg.propensity_draws):
            best, best_value = None, float("-inf")
            for action, priority, fixed, draw in candidates:
                value = fixed if fixed is not None else max(draw(rng), 1e-6) * priority
                if value > best_value:
                    best, best_value = action, value
            hits += best == key
        return (hits + 1) / (self.cfg.propensity_draws + 1), f"monte_carlo_{self.cfg.propensity_draws}"

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
        options = {cid: self._options(ctx) for cid, ctx in contexts.items()}
        arb = arbitrate(options, self.cfg)
        self.last_options, self.last_arbitration = options, arb

        decisions = []
        for cid in ids:
            d = self._decision(contexts[cid], arb.chosen[cid], arb.ranked[cid], arb.notes[cid], as_of)
            self._log_policy(d, contexts[cid], options[cid], arb.chosen[cid], arb.shadow_prices)
            self.log.record(d)
            decisions.append(d)
        return decisions

    def _log_policy(self, d: Decision, ctx: CustomerContext, options: list[Option], chosen: Option | None,
                    shadow_prices: dict[str, float]) -> None:
        """ML-Upgrade V6-prep: everything off-policy evaluation will need later."""
        policy = {"solver": self.cfg.solver, "exploration": self.cfg.exploration,
                  "seed": self.cfg.exploration_seed if self.cfg.exploration != "off" else None}
        if chosen is not None:
            propensity, method = self._propensity(ctx, options, chosen)
            treat = 1 - self.cfg.control_share if chosen.offer.action_type in SALES_ACTION_TYPES else 1.0
            policy.update({"propensity": propensity, "propensity_method": method, "p_treat": treat})
            if chosen.sample_dclv is not None:
                policy["sampled_dclv"] = chosen.sample_dclv
                expected_best = max((o for o in options if o.score.dclv > 0 and o.offer.offer_id not in
                                     self.cfg.disabled_offers and o.channel not in self.cfg.disabled_channels),
                                    key=lambda o: o.score.dclv * o.priority)
                if (expected_best.offer.offer_id, expected_best.channel) != (chosen.offer.offer_id, chosen.channel):
                    note = f"exploration: expected best was {expected_best.offer.name} via {expected_best.channel}"
                    d.notes.append(note)
                    d.reason += f" [{note}]"
            if chosen.channel in shadow_prices:
                policy["channel_shadow_price"] = shadow_prices[chosen.channel]
        d.inputs["policy"] = policy

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
