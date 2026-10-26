"""ML-Upgrade V2-light: uplift statistics, A/B plan and the learning curve."""
import pytest

from nba_engine.acceptance import AcceptanceModel
from nba_engine.uplift import ab_plan, learning_curve, pooled_uplift, sample_size, two_proportion, uplift_by_offer_ci


def test_pooled_uplift_matches_raw_counts(ds):
    h = ds.nba_action_history[ds.nba_action_history["consent_checked"]]
    est = pooled_uplift(ds.nba_action_history)
    assert est.control_n == int(h["control_group"].sum())
    assert est.treated_conv + est.control_conv == int(h["converted_within_14d"].sum())
    assert est.ci_low < est.uplift < est.ci_high
    assert 0 <= est.prob_positive <= 1


def test_mock_data_cannot_prove_uplift(ds):
    """The honest finding: 5 control conversions are not enough evidence."""
    est = pooled_uplift(ds.nba_action_history)
    assert not est.significant
    assert est.ci_low < 0 < est.ci_high


def test_two_proportion_detects_a_clear_effect():
    est = two_proportion(300, 1000, 100, 1000)
    assert est.significant and est.p_value < 1e-6
    assert est.uplift == pytest.approx(0.2)
    assert est.prob_positive > 0.999


def test_small_control_arms_are_flagged(ds):
    rows = uplift_by_offer_ci(ds.nba_action_history)
    assert rows and all(r["verdict"] == "insufficient data" for r in rows if r["control_n"] < 30)


def test_sample_size_textbook_case():
    # equal arms, 10 % vs 15 %: the textbook answer is about 686 per arm
    n = sample_size(0.10, 0.05, control_share=0.5)
    assert 670 <= n["n_control"] <= 700
    assert n["n_treated"] == n["n_control"]


def test_ab_plan_needs_more_with_smaller_effects_and_skewed_split(ds):
    plan = ab_plan(ds.nba_action_history)
    by = {(r["uplift"], r["control_share"]): r["total"] for r in plan["rows"]}
    assert by[(0.01, 0.1)] > by[(0.02, 0.1)] > by[(0.05, 0.1)]
    assert by[(0.02, 0.1)] > by[(0.02, 0.5)]


def test_learning_curve_narrows_and_moves_to_truth(ds):
    m = AcceptanceModel.fit(ds.nba_action_history, ds.product_catalog)
    curve = learning_curve(m, "P-EN-03", "email", "sie", true_p=0.25, steps=400, every=100)
    widths = [c["high"] - c["low"] for c in curve]
    assert widths[-1] < widths[0]
    assert abs(curve[-1]["mean"] - 0.25) < abs(curve[0]["mean"] - 0.25)
    assert m.counts.get(("offer", "P-EN-03")) is None  # the simulation never touches the real model
