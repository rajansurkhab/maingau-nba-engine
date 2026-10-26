"""Compliance proof and impact vs. the legacy rule engine (slides 11 and 12).

`audit_decisions()` re-checks every decision against the hard rules straight from the raw
tables and the event stream. It deliberately does not use `state.py` or
`eligibility.py`, so it is an independent second implementation: if the engine had a
bug, the audit would disagree.

`legacy_audit()` applies the same rules to the legacy rule engine's actions at the time
they were decided, and `legacy_comparison()` values each customer's last legacy action
with the same dCLV formula as ours (same customers, same moment, same economics).
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from nba_engine.config import CHANNEL_CONSENT, SALES_ACTION_TYPES, SNAPSHOT
from nba_engine.decision_log import Decision
from nba_engine.eligibility import Candidate, evaluate
from nba_engine.loader import Dataset

MAX_EXAMPLES = 5


def _consent_as_of(ds: Dataset, as_of: pd.Timestamp) -> dict[tuple[str, str], str]:
    status = {(r.customer_id, r.consent_type): r.status for r in ds.consent_status.itertuples(index=False)}
    nl = ds.newsletter_performance.merge(ds.newsletter_campaigns[["campaign_id", "sent_at"]], on="campaign_id")
    for cid in nl.loc[nl["unsubscribed"].fillna(False) & (nl["sent_at"] <= as_of), "customer_id"]:
        status[(cid, "newsletter_email")] = "withdrawn"
    ev = ds.events[(ds.events["event_type"] == "consent_changed") & (ds.events["ingested_at"] <= as_of)]
    for r in ev.itertuples(index=False):
        status[(r.customer_id, r.payload["consent_type"])] = r.payload["new_status"]
    return status


def _excluded_as_of(ds: Dataset, as_of: pd.Timestamp) -> dict[str, list[str]]:
    """customer -> reasons that block sales content (dunning, open complaint, cancellation)."""
    out: dict[str, list[str]] = defaultdict(list)
    en = ds.energy_contracts
    for cid in en.loc[en["dunning_level"] >= 1, "customer_id"]:
        out[cid].append("dunning")
    for cid in en.loc[en["cancellation_received_at"].notna() | (en["status"] == "cancellation_pending"), "customer_id"]:
        out[cid].append("cancellation")
    for cid in ds.autostrom_contracts.loc[ds.autostrom_contracts["status"] == "cancelled", "customer_id"]:
        out[cid].append("cancellation")
    si = ds.service_interactions
    for cid in si.loc[(si["category"] == "complaint") & (si["status"] == "open") & (si["created_at"] <= as_of), "customer_id"]:
        out[cid].append("open complaint")
    ev = ds.events[(ds.events["ingested_at"] <= as_of) & (ds.events["occurred_at"] > SNAPSHOT)]
    for r in ev.itertuples(index=False):
        if r.event_type == "dunning_level_changed" and int(r.payload["new_level"]) >= 1:
            out[r.customer_id].append("dunning")
        elif r.event_type == "contract_cancellation_received":
            out[r.customer_id].append("cancellation")
        elif r.event_type == "ticket_created" and r.payload.get("category") == "complaint":
            out[r.customer_id].append("open complaint")
    return out


def audit_decisions(ds: Dataset, decisions: list[Decision], as_of: pd.Timestamp,
                    statuses: dict[str, str] | None = None, reviewed: set[str] | None = None) -> dict:
    """Independent re-check of every decision. `statuses`/`reviewed` (from the log) add the
    human-in-the-loop check: nothing is approved without a logged review."""
    consent = _consent_as_of(ds, as_of)
    excluded = _excluded_as_of(ds, as_of)
    checks = {
        "consent": ("Every action uses a channel with granted consent (incl. unsubscribes and live withdrawals)", []),
        "exclusions": ("No sales content to customers with dunning, open complaint or cancellation", []),
        "profiling": ("Personalisation (signals, channel affinity, Du) only with profiling consent", []),
        "value": ("No action with negative expected CLV gain", []),
        "reason": ("Every decision, including 'no action', has a one-line reason", []),
        "payment_data": ("Payment data appears only as an exclusion flag, never as a model input", []),
        "human_in_the_loop": ("Nothing is approved without a logged human review", []),
    }
    for d in decisions:
        if not d.reason:
            checks["reason"][1].append(d.customer_id)
        if any(k in d.inputs for k in ("payment_method", "dunning_level")):
            checks["payment_data"][1].append(d.customer_id)
        if statuses is not None and statuses.get(d.decision_id) == "approved" and d.decision_id not in (reviewed or set()):
            checks["human_in_the_loop"][1].append(d.customer_id)
        if not d.has_action:
            continue
        if consent.get((d.customer_id, CHANNEL_CONSENT[d.channel])) != "granted":
            checks["consent"][1].append(d.customer_id)
        if d.action_type in SALES_ACTION_TYPES and excluded.get(d.customer_id):
            checks["exclusions"][1].append(d.customer_id)
        personalised = (d.inputs.get("signal_used") or d.inputs.get("channel_factor", 1.0) != 1.0 or d.tone == "du")
        if personalised and consent.get((d.customer_id, "profiling_analytics")) != "granted":
            checks["profiling"][1].append(d.customer_id)
        if d.dclv is None or d.dclv <= 0:
            checks["value"][1].append(d.customer_id)
    results = [
        {"id": key, "label": label, "violations": len(bad), "examples": bad[:MAX_EXAMPLES]}
        for key, (label, bad) in checks.items()
    ]
    return {
        "as_of": str(as_of),
        "decisions": len(decisions),
        "actions": sum(1 for d in decisions if d.has_action),
        "violations": sum(r["violations"] for r in results),
        "checks": results,
        "method": "recomputed from raw tables and the event stream, independent of the engine code",
    }


def legacy_audit(ds: Dataset) -> dict:
    """The legacy rule engine's delivered actions, checked against the rules at decision time."""
    h = ds.nba_action_history
    sent = h[~h["control_group"]].copy()
    cs = ds.consent_status.set_index(["customer_id", "consent_type"])
    nl = ds.newsletter_performance.merge(ds.newsletter_campaigns[["campaign_id", "sent_at"]], on="campaign_id")
    unsub_at = nl[nl["unsubscribed"].fillna(False)].groupby("customer_id")["sent_at"].min().to_dict()
    si = ds.service_interactions
    complaints = si[si["category"] == "complaint"]
    cancelled_at = ds.energy_contracts.dropna(subset=["cancellation_received_at"]).groupby("customer_id")[
        "cancellation_received_at"].min().to_dict()
    for r in ds.autostrom_contracts.dropna(subset=["cancelled_at"]).itertuples():
        cancelled_at[r.customer_id] = min(cancelled_at.get(r.customer_id, r.cancelled_at), r.cancelled_at)
    action_type = dict(zip(ds.product_catalog["product_id"], ds.product_catalog["action_type"]))

    consent_violations = unverifiable = complaint_hits = cancel_hits = 0
    examples = []
    for r in sent.itertuples(index=False):
        ctype = CHANNEL_CONSENT[r.channel]
        row = cs.loc[(r.customer_id, ctype)] if (r.customer_id, ctype) in cs.index else None
        violated = False
        if ctype == "newsletter_email" and r.customer_id in unsub_at and unsub_at[r.customer_id] <= r.decided_at:
            violated = True
        elif row is None or row["updated_at"] > r.decided_at:
            unverifiable += 1  # status at decision time unknown
        elif row["status"] != "granted":
            violated = True
        if violated:
            consent_violations += 1
            if len(examples) < MAX_EXAMPLES:
                examples.append({"customer_id": r.customer_id, "channel": r.channel, "decided_at": str(r.decided_at),
                                 "consent_checked": bool(r.consent_checked)})
        if action_type.get(r.offer_id) in SALES_ACTION_TYPES:
            c = complaints[(complaints["customer_id"] == r.customer_id) & (complaints["created_at"] <= r.decided_at)
                           & (complaints["resolved_at"].isna() | (complaints["resolved_at"] > r.decided_at))]
            complaint_hits += len(c) > 0
            cancel_hits += r.customer_id in cancelled_at and cancelled_at[r.customer_id] <= r.decided_at
    return {
        "actions_sent": len(sent),
        "without_consent_check": int((~sent["consent_checked"]).sum()),
        "consent_not_granted": consent_violations,
        "consent_unverifiable": unverifiable,
        "sales_during_open_complaint": complaint_hits,
        "sales_after_cancellation": int(cancel_hits),
        "examples": examples,
        "notes": [
            "consent_not_granted compares each action with the consent table at decision time. The synthetic "
            "legacy log and consent table are not fully consistent (also actions with consent_checked=True hit "
            "'not_given'), so the robust figure is without_consent_check.",
            "Dunning has no timestamp in the data, so dunning exclusions of legacy actions cannot be dated.",
        ],
    }


def legacy_comparison(engine, decisions: list[Decision], as_of: pd.Timestamp = SNAPSHOT) -> dict:
    """Each customer's last legacy action vs. our decision, valued with the same dCLV formula.

    Legacy actions get the population acceptance rate for their offer × channel × tone
    (the legacy engine did not personalise) and are checked against today's hard rules.
    """
    from nba_engine.scoring import score

    h = engine.ds.nba_action_history
    last = h[~h["control_group"]].sort_values("decided_at").groupby("customer_id").tail(1)
    states = engine.states(as_of)
    offers = {o.offer_id: o for o in engine.offers}
    ours = {d.customer_id: d for d in decisions}
    legacy_total = ours_total = 0.0
    negative = blocked = better = 0
    blocked_by: dict[str, int] = defaultdict(int)
    for r in last.itertuples(index=False):
        state, cv = states[r.customer_id], engine.values[r.customer_id]
        p = engine.model.p(r.offer_id, r.channel, r.tonality)
        s = score(state, cv, engine.products[r.offer_id], r.channel, r.tonality, p, engine.params, engine.cfg)
        result = evaluate(state, Candidate(offers[r.offer_id], r.channel), engine.cfg)
        legacy_total += s.dclv
        negative += s.dclv < 0
        if not result.eligible:
            blocked += 1
            blocked_by[result.blocked_by.rule_id] += 1
        mine = ours.get(r.customer_id)
        mine_value = mine.dclv if mine and mine.has_action else 0.0
        ours_total += mine_value
        better += mine_value > s.dclv
    n = len(last)
    return {
        "customers": n,
        "legacy_expected_gain": legacy_total,
        "engine_expected_gain": ours_total,
        "uplift_factor": ours_total / legacy_total if legacy_total > 0 else None,
        "legacy_negative_actions": int(negative),
        "legacy_rule_violations_today": blocked,
        "legacy_violations_by_rule": dict(blocked_by),
        "engine_better_or_equal_share": better / n if n else 0.0,
    }
