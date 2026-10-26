"""Eligibility: zero consent or exclusion violations (slide 11/12)."""
import pytest

from nba_engine.config import CHANNEL_CONSENT, EngineConfig
from nba_engine.eligibility import Candidate, Offer, evaluate, evaluate_all
from nba_engine.state import CustomerState

SALES = Offer("P-AS-01", "MAINGAU Autostrom", "sales")
RETENTION = Offer("P-EN-02", "StromGarant", "retention")
SERVICE = Offer("P-SV-01", "Service-Only / No offer", "service")


def happy_customer(**overrides) -> CustomerState:
    s = CustomerState(
        customer_id="MG-TEST",
        consents={ct: "granted" for ct in set(CHANNEL_CONSENT.values()) | {"profiling_analytics"}},
        portal_account=True,
        app_installed=True,
        electricity_tariffs={"StromAktiv"},
    )
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


# --- unit tests on synthetic states --------------------------------------------------------

def test_happy_path_is_eligible():
    assert evaluate(happy_customer(), Candidate(SALES, "portal_popup")).eligible


@pytest.mark.parametrize("status", ["withdrawn", "not_given"])
@pytest.mark.parametrize("channel", list(CHANNEL_CONSENT))
def test_missing_channel_consent_blocks_everything(channel, status):
    s = happy_customer()
    s.consents[CHANNEL_CONSENT[channel]] = status
    for offer in (SALES, SERVICE):
        result = evaluate(s, Candidate(offer, channel))
        assert not result.eligible
        assert result.blocked_by.rule_id == "R-CONSENT-CHANNEL"


def test_unreachable_channels():
    assert evaluate(happy_customer(portal_account=False), Candidate(SALES, "portal_popup")).blocked_by.rule_id == "R-CHANNEL-REACHABLE"
    assert evaluate(happy_customer(app_installed=False), Candidate(SALES, "app_push")).blocked_by.rule_id == "R-CHANNEL-REACHABLE"


@pytest.mark.parametrize(
    "overrides, rule_id",
    [
        ({"dunning_level": 1}, "R-EXCL-DUNNING"),
        ({"open_complaint": True}, "R-EXCL-COMPLAINT"),
        ({"cancellation_received": True}, "R-EXCL-CANCELLATION"),
    ],
)
def test_exclusions_block_sales_but_allow_service(overrides, rule_id):
    s = happy_customer(**overrides)
    for offer in (SALES, RETENTION):
        result = evaluate(s, Candidate(offer, "portal_popup"))
        assert not result.eligible
        assert result.blocked_by.rule_id == rule_id
    assert evaluate(s, Candidate(SERVICE, "portal_popup")).eligible


def test_retention_on_cancellation_is_config_switch():
    s = happy_customer(cancellation_received=True)
    cand = Candidate(RETENTION, "portal_popup")
    assert not evaluate(s, cand).eligible
    assert evaluate(s, cand, EngineConfig(allow_retention_on_cancellation=True)).eligible


def test_contact_limit_blocks_sales_not_service():
    s = happy_customer(sales_contacts_30d=4)
    assert evaluate(s, Candidate(SALES, "portal_popup")).blocked_by.rule_id == "R-CONTACT-LIMIT"
    assert evaluate(s, Candidate(SERVICE, "portal_popup")).eligible


def test_product_already_held():
    s = happy_customer(has_autostrom=True)
    assert evaluate(s, Candidate(SALES, "portal_popup")).blocked_by.rule_id == "R-PRODUCT-FIT"


def test_no_gas_offer_after_meter_deregistration():
    gas = Offer("P-EN-04", "GasAktiv", "sales")
    s = happy_customer(gas_meter_deregistered=True)
    assert evaluate(s, Candidate(gas, "portal_popup")).blocked_by.detail == "gas meter deregistered"


def test_hems_upsell_blocked_when_gateway_offline():
    battery = Offer("P-SL-02", "HEMS Speicher-Optimierung", "upsell")
    online = happy_customer(hems_device_types={"hems_gateway", "home_battery"}, hems_gateway_online=True)
    offline = happy_customer(hems_device_types={"hems_gateway", "home_battery"}, hems_gateway_online=False)
    assert evaluate(online, Candidate(battery, "app_push")).eligible
    assert evaluate(offline, Candidate(battery, "app_push")).blocked_by.detail == "HEMS gateway offline"


def test_trace_records_rules_up_to_block():
    result = evaluate(happy_customer(open_complaint=True), Candidate(SALES, "portal_popup"))
    assert [r.rule_id for r in result.trace] == [
        "R-CONSENT-CHANNEL", "R-CHANNEL-REACHABLE", "R-EXCL-DUNNING", "R-EXCL-COMPLAINT",
    ]


# --- property test on the full dataset: recompute rules independently from raw tables -------

@pytest.fixture(scope="module")
def all_results(states, offers):
    return [r for s in states.values() for r in evaluate_all(s, offers)]


def test_no_consent_violation_on_real_data(ds, all_results):
    cs = ds.consent_status
    granted = set(zip(cs.loc[cs["status"] == "granted", "customer_id"], cs.loc[cs["status"] == "granted", "consent_type"]))
    nl = ds.newsletter_performance
    unsubscribed = set(nl.loc[nl["unsubscribed"].fillna(False), "customer_id"])

    eligible = [r for r in all_results if r.eligible]
    assert eligible, "engine should find at least some eligible candidates"
    for r in eligible:
        consent_type = CHANNEL_CONSENT[r.candidate.channel]
        assert (r.customer_id, consent_type) in granted, r
        if consent_type == "newsletter_email":
            assert r.customer_id not in unsubscribed, r


def test_no_exclusion_violation_on_real_data(ds, all_results):
    en, si, au = ds.energy_contracts, ds.service_interactions, ds.autostrom_contracts
    excluded = (
        set(en.loc[en["dunning_level"] >= 1, "customer_id"])
        | set(en.loc[en["cancellation_received_at"].notna() | (en["status"] == "cancellation_pending"), "customer_id"])
        | set(si.loc[(si["category"] == "complaint") & (si["status"] == "open"), "customer_id"])
        | set(au.loc[au["status"] == "cancelled", "customer_id"])
    )
    assert excluded
    for r in all_results:
        if r.eligible and r.candidate.offer.is_sales:
            assert r.customer_id not in excluded, r


def test_excluded_customers_still_get_service_candidates(states, all_results):
    excluded_ids = {s.customer_id for s in states.values() if s.open_complaint or s.dunning_level >= 1}
    service_ok = {r.customer_id for r in all_results if r.eligible and r.candidate.offer.action_type == "service"}
    assert excluded_ids & service_ok
