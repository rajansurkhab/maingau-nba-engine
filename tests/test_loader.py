"""Loader must neutralise every intentional data trap."""
import pandas as pd

from nba_engine.config import SNAPSHOT
from nba_engine.loader import failed_charges


def test_all_customers_loaded(ds):
    assert len(ds.customer_master) == 750
    assert ds.customer_master["customer_id"].str.fullmatch(r"MG-1\d{5}").all()


def test_duplicate_events_removed(ds):
    assert ds.raw_event_count == 647
    assert len(ds.events) == 635
    assert ds.events["event_id"].is_unique


def test_guest_orders_split_off(ds):
    assert ds.shop_orders["customer_id"].notna().all()
    assert len(ds.guest_orders) == 25
    assert ds.guest_orders["customer_id"].isna().all()


def test_apple_mpp_opens_are_not_human(ds):
    nl = ds.newsletter_performance
    mpp = nl["open_client_class"] == "apple_mpp_prefetch"
    assert mpp.any()
    assert not nl.loc[mpp, "human_open"].any()
    assert nl.loc[nl["open_client_class"] == "human", "human_open"].all()


def test_learning_rows_exclude_control_and_legacy(ds):
    hist = ds.nba_action_history
    usable = hist[hist["usable_for_learning"]]
    assert not usable["control_group"].any()
    assert usable["consent_checked"].all()
    assert hist["control_group"].sum() == 248
    assert (~hist["consent_checked"]).sum() > 0


def test_late_roaming_cdrs_counted_by_start_time(ds):
    cs = ds.charge_sessions
    late = cs[(cs["cdr_received_at"] > SNAPSHOT) & (cs["started_at"] <= SNAPSHOT)]
    assert len(late) > 0, "dataset should contain CDRs that arrived after the snapshot"
    failed_late = late[late["session_status"] == "failed"].iloc[0]
    as_of = failed_late["started_at"] + pd.Timedelta(minutes=1)
    # counted although the CDR arrived later than as_of
    assert failed_charges(ds, failed_late["customer_id"], as_of) >= 1


def test_value_assumptions_loaded(ds):
    va = ds.value_assumptions
    assert va["discount_rate"] == 0.06
    assert va["contact_during_service_case"] == 0.05
    assert va["service_resolution_cost_eur"] == 15.0
