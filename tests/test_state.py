"""Customer state: consent resolution, exclusions and event replay."""
import dataclasses

import pandas as pd

from nba_engine.config import SNAPSHOT
from nba_engine.state import build_states, effective_consents


def test_every_unsubscribe_means_newsletter_withdrawn(ds, states):
    unsubscribed = ds.newsletter_performance.loc[
        ds.newsletter_performance["unsubscribed"].fillna(False), "customer_id"
    ].unique()
    assert len(unsubscribed) > 0
    for cid in unsubscribed:
        assert states[cid].consent("newsletter_email") == "withdrawn", cid


def test_unsubscribe_overrides_stale_consent_table(ds):
    """If the consent platform still says 'granted', the unsubscribe must win."""
    nl = ds.newsletter_performance
    cid = nl.loc[nl["unsubscribed"].fillna(False), "customer_id"].iloc[0]
    cs = ds.consent_status.copy()
    row = (cs["customer_id"] == cid) & (cs["consent_type"] == "newsletter_email")
    cs.loc[row, "status"] = "granted"
    cs.loc[row, "updated_at"] = pd.Timestamp("2020-01-01")
    stale = dataclasses.replace(ds, consent_status=cs)
    assert effective_consents(stale)[(cid, "newsletter_email")] == "withdrawn"


def test_regrant_after_unsubscribe_is_respected(ds):
    """A later explicit opt-in (newer updated_at) beats an old unsubscribe."""
    nl = ds.newsletter_performance
    cid = nl.loc[nl["unsubscribed"].fillna(False), "customer_id"].iloc[0]
    cs = ds.consent_status.copy()
    row = (cs["customer_id"] == cid) & (cs["consent_type"] == "newsletter_email")
    cs.loc[row, "status"] = "granted"
    cs.loc[row, "updated_at"] = SNAPSHOT
    regranted = dataclasses.replace(ds, consent_status=cs)
    assert effective_consents(regranted)[(cid, "newsletter_email")] == "granted"


def test_exclusion_flags_match_raw_tables(ds, states):
    en = ds.energy_contracts
    for cid in en.loc[en["dunning_level"] >= 1, "customer_id"]:
        assert states[cid].dunning_level >= 1
    for cid in en.loc[en["cancellation_received_at"].notna(), "customer_id"]:
        assert states[cid].cancellation_received
    si = ds.service_interactions
    for cid in si.loc[(si["category"] == "complaint") & (si["status"] == "open"), "customer_id"]:
        assert states[cid].open_complaint


def test_gas_meter_deregistration_detected(ds, states):
    dereg = ds.energy_contracts.loc[ds.energy_contracts["meter_deregistered_at"].notna(), "customer_id"]
    assert len(dereg) > 0
    assert all(states[cid].gas_meter_deregistered for cid in dereg)


def test_offline_hems_gateway_detected(ds, states):
    hd = ds.hems_devices
    offline = hd.loc[(hd["device_type"] == "hems_gateway") & (hd["connection_status"] == "offline"), "customer_id"]
    assert len(offline) == 2
    assert not any(states[cid].hems_gateway_online for cid in offline)


def test_consent_withdrawal_event_applies_once_ingested(ds):
    before = build_states(ds, pd.Timestamp("2026-10-01 10:14:00"))["MG-100003"]
    after = build_states(ds, pd.Timestamp("2026-10-01 10:15:00"))["MG-100003"]
    assert after.consent("profiling_analytics") == "withdrawn"
    assert after.consent("newsletter_email") == "withdrawn"
    assert before.consent("profiling_analytics") != "withdrawn" or before.consent("newsletter_email") != "withdrawn"


def test_cancellation_event_uses_ingestion_time(ds, states):
    cid = "MG-100465"  # occurred 2026-10-01 09:17, ingested 2026-10-02 02:11
    assert not states[cid].cancellation_received
    assert not build_states(ds, pd.Timestamp("2026-10-01 23:59"))[cid].cancellation_received
    assert build_states(ds, pd.Timestamp("2026-10-02 03:00"))[cid].cancellation_received


def test_dunning_event_raises_level(ds):
    assert build_states(ds, pd.Timestamp("2026-10-03"))["MG-100004"].dunning_level == 3
