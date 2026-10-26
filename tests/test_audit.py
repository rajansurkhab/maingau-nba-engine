"""Independent compliance audit and the legacy comparison."""
import dataclasses

import pandas as pd
import pytest

from nba_engine.audit import audit_decisions, legacy_audit, legacy_comparison
from nba_engine.config import SNAPSHOT
from nba_engine.pipeline import Engine


@pytest.fixture(scope="module")
def run(ds):
    engine = Engine(ds)
    return engine, engine.recommend()


def by_check(report):
    return {c["id"]: c["violations"] for c in report["checks"]}


def test_engine_passes_the_independent_audit(ds, run):
    _, decisions = run
    report = audit_decisions(ds, decisions, SNAPSHOT)
    assert report["violations"] == 0
    assert report["actions"] > 500


def test_audit_passes_in_event_replay(ds):
    as_of = pd.Timestamp("2026-10-02 06:00")
    decisions = Engine(ds).recommend(as_of=as_of)
    assert audit_decisions(ds, decisions, as_of)["violations"] == 0


def test_audit_catches_injected_violations(ds, run, states):
    _, decisions = run
    action = next(d for d in decisions if d.has_action and d.action_type != "service")
    no_sms = next(cid for cid, s in states.items() if s.consent("marketing_sms") != "granted")
    complaint = next(cid for cid, s in states.items() if s.open_complaint)
    no_profiling = next(cid for cid, s in states.items() if not s.profiling_allowed and s.consent("marketing_email") == "granted")
    bad = [
        dataclasses.replace(action, customer_id=no_sms, channel="sms"),
        dataclasses.replace(action, customer_id=complaint, channel="email", action_type="sales"),
        dataclasses.replace(action, customer_id=no_profiling, channel="email", tone="du"),
        dataclasses.replace(action, dclv=-3.0),
        dataclasses.replace(action, reason=""),
    ]
    counts = by_check(audit_decisions(ds, bad, SNAPSHOT))
    assert counts["consent"] >= 1
    assert counts["exclusions"] >= 1
    assert counts["profiling"] >= 1
    assert counts["value"] >= 1
    assert counts["reason"] >= 1


def test_audit_flags_approval_without_review(ds, run):
    _, decisions = run
    d = next(x for x in decisions if x.has_action)
    report = audit_decisions(ds, [d], SNAPSHOT, statuses={d.decision_id: "approved"}, reviewed=set())
    assert by_check(report)["human_in_the_loop"] == 1


def test_legacy_audit_counts_the_documented_trap(ds):
    legacy = legacy_audit(ds)
    h = ds.nba_action_history
    assert legacy["actions_sent"] == int((~h["control_group"]).sum())
    assert legacy["without_consent_check"] == int((~h["control_group"] & ~h["consent_checked"]).sum()) == 297


def test_legacy_comparison_same_formula(run):
    engine, decisions = run
    cmp = legacy_comparison(engine, decisions)
    assert cmp["customers"] > 700
    assert cmp["engine_expected_gain"] > cmp["legacy_expected_gain"]
    assert cmp["legacy_rule_violations_today"] > 0
    assert sum(cmp["legacy_violations_by_rule"].values()) == cmp["legacy_rule_violations_today"]
