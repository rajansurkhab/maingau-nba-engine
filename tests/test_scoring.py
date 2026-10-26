"""dCLV scoring must follow clv_method.txt exactly."""
import pandas as pd
import pytest

from nba_engine.acceptance import AcceptanceModel
from nba_engine.config import EngineConfig
from nba_engine.scoring import (
    CustomerValue, ProductEconomics, ValueParams, clv, customer_values, parse_drivers, score,
)
from nba_engine.state import CustomerState, build_states


@pytest.fixture(scope="module")
def params(ds):
    return ValueParams.from_assumptions(ds.value_assumptions)


@pytest.fixture(scope="module")
def cvs(ds):
    return customer_values(ds.customer_value)


@pytest.fixture(scope="module")
def products(ds):
    return ProductEconomics.from_catalog(ds.product_catalog)


def reference_clv(net_margin, churn):
    """Literal transcription of clv_method.txt."""
    return sum(net_margin * (1 - churn) ** (t - 1) / (1 + 0.06) ** t for t in range(1, 11))


def plain_state(**kw) -> CustomerState:
    return CustomerState(customer_id="MG-TEST", **kw)


def test_clv_matches_method(params):
    assert clv(100.0, 0.2, params) == pytest.approx(reference_clv(100.0, 0.2))
    assert clv(-10.0, 0.0, params) == pytest.approx(reference_clv(-10.0, 0.0))


def test_clv_reproduces_precomputed_values(cvs, params):
    # clv_eur is rounded to whole euros and churn to 2 decimals in the source table
    for cv in cvs.values():
        assert clv(cv.net_margin, cv.churn, params) == pytest.approx(cv.clv_reported, abs=3.5), cv.customer_id


def test_all_churn_drivers_parse(ds):
    for text in ds.customer_value["churn_drivers"]:
        parse_drivers(text)  # raises on unknown driver
    assert parse_drivers("open complaint; 3 tickets in 12m") == ["open_complaint", "tickets_12m_ge2"]


def test_churn_drivers_are_additive(cvs, params):
    """Removing every additive driver must leave a plausible base churn (base x multi-line factor)."""
    additive = {"price_guarantee_ends_90d", "open_complaint", "tickets_12m_ge2", "csat_le2",
                "dunning_ge2", "failed_charges_30d_ge3", "hems_user"}
    for cv in cvs.values():
        if cv.cancellation_pending:
            assert cv.churn == 0.95
            continue
        base = cv.churn_without(tuple(additive), params)
        assert 0.08 <= base <= 0.25, cv.customer_id


def test_sales_accept_adds_margin_and_churn_effect(params):
    cv = CustomerValue("MG-TEST", annual_margin=150.0, cost_to_serve=20.0, churn=0.2)
    product = ProductEconomics("P-MO-01", "sales", 54.0, -0.02)
    s = score(plain_state(), cv, product, "email", "sie", 0.1, params)
    expected_accept = reference_clv(130.0 + 54.0, 0.18)
    expected = 0.1 * (expected_accept - reference_clv(130.0, 0.2)) - 0.01
    assert s.clv_accept == pytest.approx(expected_accept)
    assert s.clv_reject == pytest.approx(s.clv_now)
    assert s.dclv == pytest.approx(expected)


def test_one_off_margin_counted_once(params):
    cv = CustomerValue("MG-TEST", annual_margin=150.0, cost_to_serve=20.0, churn=0.2)
    product = ProductEconomics("P-SH-02", "one_off", 126.0, -0.01)
    s = score(plain_state(), cv, product, "portal_popup", "sie", 1.0, params)
    assert s.clv_accept == pytest.approx(reference_clv(130.0, 0.19) + 126.0)


def test_retention_only_pays_with_price_guarantee_driver(params):
    product = ProductEconomics("P-EN-02", "retention", 0.0, -0.1)
    without = CustomerValue("MG-TEST", 150.0, 20.0, 0.14)
    with_driver = CustomerValue("MG-TEST", 150.0, 20.0, 0.24, drivers=["price_guarantee_ends_90d"])
    assert score(plain_state(), without, product, "email", "sie", 0.5, params).dclv == pytest.approx(-0.01)
    s = score(plain_state(), with_driver, product, "email", "sie", 0.5, params)
    assert s.clv_accept == pytest.approx(reference_clv(130.0, 0.14))
    assert s.dclv > 0


def test_service_fix_removes_service_drivers_and_costs(params):
    product = ProductEconomics("P-SV-01", "service", 0.0, 0.0)
    cv = CustomerValue("MG-TEST", 200.0, 30.0, 0.34, drivers=["tickets_12m_ge2", "failed_charges_30d_ge3"])
    s = score(plain_state(), cv, product, "portal_popup", "sie", 0.5, params)
    assert s.clv_accept == pytest.approx(reference_clv(170.0, 0.20) - 15.0)
    no_issue = CustomerValue("MG-TEST", 200.0, 30.0, 0.20)
    assert score(plain_state(), no_issue, product, "portal_popup", "sie", 0.5, params).dclv < 0


def test_service_fix_never_removes_dunning_driver(params):
    """Payment data must not create value (slide 12), so dunning churn stays."""
    product = ProductEconomics("P-SV-01", "service", 0.0, 0.0)
    cv = CustomerValue("MG-TEST", 200.0, 30.0, 0.30, drivers=["dunning_ge2"])
    s = score(plain_state(dunning_level=2), cv, product, "portal_popup", "sie", 1.0, params)
    assert s.clv_accept == pytest.approx(reference_clv(170.0, 0.30) - 15.0)


@pytest.mark.parametrize("state_kw", [{"open_complaint": True}, {"dunning_level": 1}, {"open_service_case": True}])
def test_sales_reject_during_service_case_raises_churn(params, state_kw):
    cv = CustomerValue("MG-TEST", 200.0, 30.0, 0.2)
    sales = ProductEconomics("P-EN-01", "sales", 114.0, -0.02)
    s = score(plain_state(**state_kw), cv, sales, "email", "sie", 0.03, params)
    assert s.reject_penalty
    assert s.clv_reject == pytest.approx(reference_clv(170.0, 0.25))
    assert s.dclv < 0


def test_reject_penalty_on_plain_ticket_is_configurable(params):
    cv = CustomerValue("MG-TEST", 200.0, 30.0, 0.2)
    sales = ProductEconomics("P-EN-01", "sales", 114.0, -0.02)
    strict = EngineConfig(reject_penalty_on_any_open_ticket=False)
    assert not score(plain_state(open_service_case=True), cv, sales, "email", "sie", 0.03, params, strict).reject_penalty


def test_contact_costs_from_assumptions(params):
    assert params.contact_cost("outbound_call") == 6.0
    assert params.contact_cost("sms") == 0.08
    assert params.contact_cost("portal_popup") == 0.0


def test_slide7_autostrom_case(ds, cvs, params, products):
    """MG-100009: 3+ failed charges, opens a ticket on 2026-10-01 17:25.
    Service fix scores clearly positive, the home electricity offer negative (slide 7)."""
    cfg = EngineConfig()
    model = AcceptanceModel.fit(ds.nba_action_history, ds.product_catalog, cfg.shrinkage_strength,
                                {"service": cfg.service_p_accept_prior})
    state = build_states(ds, pd.Timestamp("2026-10-01 18:00"))["MG-100009"]
    assert state.open_service_case

    def dclv(offer_id):
        p = model.p(offer_id, "portal_popup", "sie")
        return score(state, cvs["MG-100009"], products[offer_id], "portal_popup", "sie", p, params, cfg).dclv

    assert dclv("P-SV-01") > 50
    assert dclv("P-EN-01") < 0
