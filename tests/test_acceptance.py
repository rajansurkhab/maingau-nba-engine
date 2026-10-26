"""p_accept via hierarchical Beta-Binomial shrinkage."""
import pandas as pd
import pytest

from nba_engine.acceptance import AcceptanceModel, uplift_by_offer

TYPES = {"P-A": "sales", "P-B": "sales", "P-SV-01": "service"}


def make_model(**kw) -> AcceptanceModel:
    return AcceptanceModel(offer_types=TYPES, strength=10.0, **kw)


def test_empty_model_is_laplace_prior():
    assert make_model().p("P-A", "sms", "sie") == pytest.approx(0.5)


def test_estimate_lies_between_leaf_rate_and_parent():
    m = make_model()
    for i in range(100):
        m.update("P-B", "email", "sie", accepted=i < 5)  # 5 %
    for i in range(20):
        m.update("P-A", "email", "sie", accepted=i < 10)  # 50 %
    p = m.p("P-A", "email", "sie")
    sales_rate = 15 / 120
    assert sales_rate < p < 0.5


def test_unseen_offer_gets_action_type_rate():
    m = make_model()
    for i in range(200):
        m.update("P-B", "email", "sie", accepted=i < 20)
    unseen = m.p("P-A", "email", "sie")
    assert unseen == pytest.approx(m.p("P-A", "sms", "du"))
    assert m.trials("P-A", "email", "sie") == 0
    assert 0.09 < unseen < 0.13


def test_update_is_bayesian_learning():
    m = make_model()
    before = m.p("P-A", "sms", "du")
    m.update("P-A", "sms", "du", accepted=True)
    assert m.p("P-A", "sms", "du") > before
    m2 = make_model()
    m2.update("P-A", "sms", "du", accepted=False)
    assert m2.p("P-A", "sms", "du") < before


def test_type_prior_for_service_without_history():
    m = make_model(type_priors={"service": 0.5})
    for _ in range(100):
        m.update("P-A", "email", "sie", accepted=False)
    assert m.p("P-SV-01", "portal_popup", "sie") == pytest.approx(0.5)


def test_fit_ignores_control_group_and_legacy_rows(ds):
    hist = ds.nba_action_history
    base = AcceptanceModel.fit(hist, ds.product_catalog)
    tampered = hist.copy()
    excluded = ~tampered["usable_for_learning"]
    tampered.loc[excluded, "converted_within_14d"] = True
    again = AcceptanceModel.fit(tampered, ds.product_catalog)
    for key in [("P-EN-02", "newsletter_email", "sie"), ("P-MO-01", "portal_popup", "du")]:
        assert again.p(*key) == pytest.approx(base.p(*key))
    assert base.counts[("global",)][1] == int(hist["usable_for_learning"].sum())


def test_probabilities_are_valid_for_every_catalog_cell(ds):
    m = AcceptanceModel.fit(ds.nba_action_history, ds.product_catalog, type_priors={"service": 0.5})
    for offer in ds.product_catalog["product_id"]:
        for channel in ["newsletter_email", "email", "sms", "outbound_call", "portal_popup", "app_push"]:
            for tone in ["du", "sie"]:
                assert 0.0 < m.p(offer, channel, tone) < 1.0


def test_uplift_table(ds):
    up = uplift_by_offer(ds.nba_action_history)
    row = up.loc["P-EN-02"]
    assert row["control_n"] > 0
    assert row["uplift"] == pytest.approx(row["treated_rate"] - row["control_rate"])
