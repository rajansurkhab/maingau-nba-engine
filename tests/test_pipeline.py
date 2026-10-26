"""End-to-end recommend(): zero violations, one logged decision per customer, explainable."""
import json

import pandas as pd
import pytest

from nba_engine.config import CHANNEL_CONSENT, SALES_ACTION_TYPES, EngineConfig
from nba_engine.eligibility import Candidate, evaluate
from nba_engine.pipeline import Engine, in_control_group


@pytest.fixture(scope="module")
def engine(ds):
    return Engine(ds)


@pytest.fixture(scope="module")
def decisions(engine):
    return engine.recommend()


def test_one_logged_decision_per_customer(decisions, engine):
    assert len(decisions) == 750
    assert len({d.customer_id for d in decisions}) == 750
    assert len(engine.log.entries) >= 750
    assert {d.status for d in decisions} <= {"proposed", "held_out", "no_action"}


def test_every_action_has_positive_dclv_and_passing_trace(decisions):
    for d in decisions:
        if d.has_action:
            assert d.dclv > 0
            assert d.rule_trace and all(step["passed"] for step in d.rule_trace)
        else:
            assert d.status == "no_action"


def test_every_decision_has_a_one_line_reason(decisions):
    for d in decisions:
        assert d.reason and "\n" not in d.reason
        assert len(d.reason) < 250


def test_zero_consent_and_exclusion_violations(ds, decisions):
    """Independent recomputation from the raw tables (jury check, slide 11)."""
    cs = ds.consent_status
    granted = set(zip(cs.loc[cs["status"] == "granted", "customer_id"], cs.loc[cs["status"] == "granted", "consent_type"]))
    nl = ds.newsletter_performance
    unsubscribed = set(nl.loc[nl["unsubscribed"].fillna(False), "customer_id"])
    en, si, au = ds.energy_contracts, ds.service_interactions, ds.autostrom_contracts
    excluded = (
        set(en.loc[en["dunning_level"] >= 1, "customer_id"])
        | set(en.loc[en["cancellation_received_at"].notna() | (en["status"] == "cancellation_pending"), "customer_id"])
        | set(si.loc[(si["category"] == "complaint") & (si["status"] == "open"), "customer_id"])
        | set(au.loc[au["status"] == "cancelled", "customer_id"])
    )
    for d in decisions:
        if not d.has_action:
            continue
        consent_type = CHANNEL_CONSENT[d.channel]
        assert (d.customer_id, consent_type) in granted, d.decision_id
        if consent_type == "newsletter_email":
            assert d.customer_id not in unsubscribed
        if d.action_type in SALES_ACTION_TYPES:
            assert d.customer_id not in excluded, d.decision_id


def test_chosen_action_is_eligible_when_reevaluated(engine, decisions):
    states = engine.states(pd.Timestamp("2026-09-30 23:59:59"))
    offers = {o.offer_id: o for o in engine.offers}
    for d in decisions:
        if d.has_action:
            assert evaluate(states[d.customer_id], Candidate(offers[d.offer_id], d.channel), engine.cfg).eligible


def test_control_group_only_sales_and_stable(decisions):
    held = [d for d in decisions if d.status == "held_out"]
    sales = [d for d in decisions if d.action_type in SALES_ACTION_TYPES]
    assert all(d.action_type in SALES_ACTION_TYPES for d in held)
    assert all(not d.control_group for d in decisions if d.action_type == "service")
    assert 0.04 < len(held) / len(sales) < 0.16
    assert all(in_control_group(d.customer_id, 0.10) for d in held)


def test_no_action_when_nothing_is_positive(decisions):
    no_action = [d for d in decisions if not d.has_action]
    assert no_action
    assert all(d.reason.startswith("No action") for d in no_action)


def test_marketing_controls_change_the_outcome(ds):
    cfg = EngineConfig(disabled_offers={"P-MO-01"}, channel_capacity={"outbound_call": 0})
    out = Engine(ds, cfg).recommend()
    assert all(d.offer_id != "P-MO-01" for d in out)
    assert all(d.channel != "outbound_call" for d in out)


def test_channel_and_tone_approval(ds):
    cfg = EngineConfig(disabled_channels={"newsletter_email", "app_push"}, allow_du_tone=False)
    out = Engine(ds, cfg).recommend()
    assert all(d.channel not in cfg.disabled_channels for d in out)
    assert all(d.tone in (None, "sie") for d in out)
    assert any("Du detected but not approved" in d.inputs["tone_evidence"] for d in out)


def test_config_version_logged(decisions, engine):
    assert all(d.config_version == engine.cfg.version() for d in decisions)
    assert EngineConfig(top_k=5).version() != EngineConfig().version()


def test_slide7_case_end_to_end(engine):
    d = engine.recommend(["MG-100009"], pd.Timestamp("2026-10-01 18:00"))[0]
    assert d.offer_id == "P-SV-01"
    assert d.status == "proposed"
    assert "failed charging sessions" in d.reason
    assert "during open service case" in d.reason


def by_customer(decisions):
    return {d.customer_id: d for d in decisions}


def test_slide9_formal_portal_customer(decisions):
    """MG-100001 writes formally, opened 0/6 newsletters, visits the portal weekly."""
    d = by_customer(decisions)["MG-100001"]
    assert (d.channel, d.tone) == ("portal_popup", "sie")
    assert "portal visits/week" in d.reason
    assert d.inputs["channel_evidence"]["newsletter_email"] == "0/6 newsletters opened"


@pytest.mark.parametrize(
    "customer_id, offer_id, signal",
    [
        ("MG-100060", "P-EN-03", "gas_meter_deregistered"),  # gas meter -> heat-pump electricity
        ("MG-100266", "P-AS-01", "wallbox_purchase"),  # wallbox bought in shop -> Autostrom
        ("MG-100125", "P-EN-03", "heat_pump_interest"),  # 3 heat-pump clicks -> follow-up
    ],
)
def test_slide6_signal_cases(decisions, customer_id, offer_id, signal):
    d = by_customer(decisions)[customer_id]
    assert d.offer_id == offer_id
    assert d.inputs["signal_used"] == signal
    assert d.p_accept > d.inputs["p_accept_base"]
    assert "→" in d.reason


def test_no_personalisation_without_profiling(decisions):
    for d in decisions:
        if d.has_action and not d.inputs["profiling_allowed"]:
            assert d.inputs["signal_used"] is None
            assert d.inputs["channel_factor"] == 1.0
            assert d.p_accept == pytest.approx(d.inputs["p_accept_base"])
            assert d.tone == "sie"


def test_p_accept_cap(decisions, engine):
    for d in decisions:
        if d.has_action:
            assert d.p_accept <= max(engine.cfg.max_p_accept, d.inputs["p_accept_base"]) + 1e-12


def test_approval_is_append_only(ds, tmp_path):
    path = tmp_path / "decision_log.jsonl"
    eng = Engine(ds, log_path=path)
    d = next(x for x in eng.recommend(["MG-100001", "MG-100002", "MG-100004"]) if x.status == "proposed")
    eng.log.set_status(d.decision_id, "approved", actor="marketing@test", ts="2026-10-26 12:00")
    assert eng.log.status(d.decision_id) == "approved"
    with pytest.raises(ValueError):
        eng.log.set_status(d.decision_id, "rejected", actor="marketing@test", ts="2026-10-26 12:01")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [e["kind"] for e in lines] == ["decision"] * 3 + ["status"]
    assert lines[-1]["decision_id"] == d.decision_id
    assert eng.log.to_frame().set_index("decision_id").loc[d.decision_id, "status"] == "approved"


def test_held_out_and_no_action_cannot_be_approved(engine, decisions):
    other = next(d for d in decisions if d.status in ("held_out", "no_action"))
    with pytest.raises(ValueError):
        engine.log.set_status(other.decision_id, "approved", actor="x", ts="2026-10-26")
