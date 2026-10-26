"""Trigger signals: detection, consent gating and effect on p_accept."""
import pandas as pd
import pytest

from nba_engine.acceptance import AcceptanceModel
from nba_engine.config import SNAPSHOT, EngineConfig
from nba_engine.signals import SignalDetector, apply_signal, strongest_for
from nba_engine.state import build_states


@pytest.fixture(scope="module")
def detector(ds):
    return SignalDetector(ds, EngineConfig())


def ids(signals):
    return {s.signal_id for s in signals}


def test_apply_signal_moves_toward_intent_rate_and_never_lowers():
    assert apply_signal(0.02, 0.30, 1.0) == pytest.approx(0.30)
    assert apply_signal(0.02, 0.30, 0.5) == pytest.approx(0.16)
    assert apply_signal(0.02, 0.30, 0.0) == pytest.approx(0.02)
    assert apply_signal(0.40, 0.30, 1.0) == pytest.approx(0.40)


def test_intent_model_learns_from_clicked_rows_only(ds):
    hist = ds.nba_action_history
    intent = AcceptanceModel.fit(hist, ds.product_catalog, clicked_only=True)
    base = AcceptanceModel.fit(hist, ds.product_catalog)
    clicked = hist["usable_for_learning"] & (hist["response"] == "clicked")
    assert intent.counts[("global",)][1] == int(clicked.sum())
    for offer in ("P-AS-01", "P-EN-03", "P-SL-01"):
        assert intent.p(offer, "newsletter_email", "sie") > 3 * base.p(offer, "newsletter_email", "sie")


def test_gas_meter_deregistration_detected(ds, states, detector):
    dereg = ds.energy_contracts.loc[ds.energy_contracts["meter_deregistered_at"].notna(), "customer_id"]
    for cid in dereg:
        assert "gas_meter_deregistered" in ids(detector.detect(states[cid]))


def test_gas_deregistration_event_counts_once_ingested(ds, detector):
    cid = "MG-100125"  # ticket on 01.10 11:31, ERP event ingested 02.10 02:14
    before = build_states(ds, pd.Timestamp("2026-10-01 23:00"))[cid]
    after = build_states(ds, pd.Timestamp("2026-10-02 03:00"))[cid]
    assert "gas_meter_deregistered" not in ids(detector.detect(before, pd.Timestamp("2026-10-01 23:00")))
    assert "gas_meter_deregistered" in ids(detector.detect(after, pd.Timestamp("2026-10-02 03:00")))


def test_three_heat_pump_clicks(states, detector):
    signals = detector.detect(states["MG-100125"])
    assert "heat_pump_interest" in ids(signals)
    assert next(s for s in signals if s.signal_id == "heat_pump_interest").evidence == "3 heat-pump story clicks"


def test_two_clicks_are_not_enough(ds, detector):
    nl = ds.newsletter_performance
    hp = nl[nl["click_target"] == "heat_pump_article"].groupby("customer_id").size()
    states = build_states(ds)
    for cid, n in hp.items():
        assert ("heat_pump_interest" in ids(detector.detect(states[cid]))) == (n >= 3)


def test_wallbox_purchase_from_shop_within_lookback(ds, states, detector):
    so = ds.shop_orders
    wb = so[(so["product_name"] == "Wallbox 11 kW") & (so["order_status"] == "delivered")]
    recent = wb["order_created_at"] > SNAPSHOT - pd.Timedelta(days=EngineConfig().signal_lookback_days)
    assert recent.any() and (~recent).any()
    for cid in wb.loc[recent, "customer_id"]:
        assert "wallbox_purchase" in ids(detector.detect(states[cid]))
    for cid in set(wb.loc[~recent, "customer_id"]) - set(wb.loc[recent, "customer_id"]):
        if "wallbox" not in states[cid].hems_device_types:
            assert "wallbox_purchase" not in ids(detector.detect(states[cid]))


def test_signals_unused_without_profiling_consent(states, detector):
    found = 0
    for s in states.values():
        for sig in detector.detect(s):
            assert sig.used == s.profiling_allowed
            found += not sig.used
        if not s.profiling_allowed:
            assert all(strongest_for(detector.detect(s), o) is None for o in ("P-EN-03", "P-AS-01"))
    assert found > 0


def test_strongest_signal_wins(ds):
    """MG-100125 on 02.10 has both heat-pump clicks and a gas deregistration."""
    as_of = pd.Timestamp("2026-10-02 03:00")
    state = build_states(ds, as_of)["MG-100125"]
    cfg = EngineConfig(signal_strength={"gas_meter_deregistered": 0.3, "heat_pump_interest": 0.9,
                                        "heat_pump_device": 0.5, "wallbox_purchase": 1.0})
    signals = SignalDetector(ds, cfg).detect(state, as_of)
    assert {"gas_meter_deregistered", "heat_pump_interest"} <= ids(signals)
    assert strongest_for(signals, "P-EN-03").signal_id == "heat_pump_interest"
