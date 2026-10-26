"""Per-customer facts that eligibility (and later scoring) reads.

`build_states` turns the cleaned tables into one `CustomerState` per customer as of a
point in time. Events from the stream are applied once they were *ingested* by `as_of`,
because a decision can only use what the system already knows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from nba_engine.config import SNAPSHOT
from nba_engine.loader import Dataset

SHOP_PRODUCT_NAMES = {
    "P-SH-01": "Balkonkraftwerk 800 W",
    "P-SH-02": "Wallbox 11 kW",
    "P-SH-03": "Smarte Thermostate 3er",
    "P-SH-04": "Smart Plug 4er-Set",
}


@dataclass
class CustomerState:
    customer_id: str
    consents: dict[str, str] = field(default_factory=dict)
    portal_account: bool = False
    app_installed: bool = False
    # holdings
    electricity_tariffs: set[str] = field(default_factory=set)
    has_gas: bool = False
    gas_meter_deregistered: bool = False
    smart_meter: bool = False
    has_mobile: bool = False
    has_autostrom: bool = False
    hems_device_types: set[str] = field(default_factory=set)
    hems_gateway_online: bool = False
    shop_products_owned: set[str] = field(default_factory=set)
    # exclusions (payment data used ONLY here, never as a model feature)
    dunning_level: int = 0
    open_complaint: bool = False
    cancellation_received: bool = False
    # any open ticket (complaint or not); sales contacts then raise churn on reject (slide 7)
    open_service_case: bool = False
    # contact pressure and service signals
    sales_contacts_30d: int = 0
    failed_charges_30d: int = 0

    def consent(self, consent_type: str) -> str:
        return self.consents.get(consent_type, "not_given")

    @property
    def profiling_allowed(self) -> bool:
        return self.consent("profiling_analytics") == "granted"

    @property
    def has_electricity(self) -> bool:
        return bool(self.electricity_tariffs)

    @property
    def has_hems(self) -> bool:
        return "hems_gateway" in self.hems_device_types


def effective_consents(ds: Dataset, as_of: pd.Timestamp = SNAPSHOT) -> dict[tuple[str, str], str]:
    """Latest consent per (customer, type). An unsubscribe after the last status change
    counts as withdrawal of newsletter_email, even if the consent table lags behind."""
    cs = ds.consent_status
    result = {(r.customer_id, r.consent_type): r.status for r in cs.itertuples(index=False)}
    updated = {(r.customer_id, r.consent_type): r.updated_at for r in cs.itertuples(index=False)}

    nl = ds.newsletter_performance.merge(
        ds.newsletter_campaigns[["campaign_id", "sent_at"]], on="campaign_id", how="left"
    )
    unsubs = nl[nl["unsubscribed"].fillna(False) & (nl["sent_at"] <= as_of)]
    for r in unsubs.itertuples(index=False):
        key = (r.customer_id, "newsletter_email")
        last_change = updated.get(key)
        if last_change is None or pd.isna(last_change) or r.sent_at >= last_change:
            result[key] = "withdrawn"
    return result


def build_states(ds: Dataset, as_of: pd.Timestamp = SNAPSHOT) -> dict[str, CustomerState]:
    states = {
        r.customer_id: CustomerState(
            customer_id=r.customer_id,
            portal_account=bool(r.portal_account),
            app_installed=bool(r.app_installed),
        )
        for r in ds.customer_master.itertuples(index=False)
    }

    for (cid, ctype), status in effective_consents(ds, as_of).items():
        if cid in states:
            states[cid].consents[ctype] = status

    for r in ds.energy_contracts.itertuples(index=False):
        s = states[r.customer_id]
        if r.status == "terminated_meter_deregistration" or pd.notna(r.meter_deregistered_at):
            s.gas_meter_deregistered = True
        if r.status in ("active", "cancellation_pending"):
            if r.commodity == "electricity":
                s.electricity_tariffs.add(r.tariff)
                s.smart_meter |= r.meter_type == "smart_meter_imsys"
            elif r.commodity == "gas":
                s.has_gas = True
        s.dunning_level = max(s.dunning_level, int(r.dunning_level))
        if r.status == "cancellation_pending" or pd.notna(r.cancellation_received_at):
            s.cancellation_received = True

    for r in ds.mobile_contracts.itertuples(index=False):
        if r.status == "active":
            states[r.customer_id].has_mobile = True

    for r in ds.autostrom_contracts.itertuples(index=False):
        s = states[r.customer_id]
        if r.status == "active":
            s.has_autostrom = True
        else:
            s.cancellation_received = True

    for r in ds.hems_devices.itertuples(index=False):
        s = states[r.customer_id]
        s.hems_device_types.add(r.device_type)
        if r.device_type == "hems_gateway" and r.connection_status == "online":
            s.hems_gateway_online = True

    owned = ds.shop_orders[ds.shop_orders["order_status"] == "delivered"]
    for r in owned.itertuples(index=False):
        states[r.customer_id].shop_products_owned.add(r.product_name)

    si = ds.service_interactions
    open_tickets = si[(si["status"] == "open") & (si["created_at"] <= as_of)]
    for r in open_tickets.itertuples(index=False):
        states[r.customer_id].open_service_case = True
        if r.category == "complaint":
            states[r.customer_id].open_complaint = True

    hist = ds.nba_action_history
    recent = hist[
        (hist["delivery_status"] == "delivered")
        & (hist["decided_at"] > as_of - pd.Timedelta(days=30))
        & (hist["decided_at"] <= as_of)
    ]
    for cid, n in recent["customer_id"].value_counts().items():
        states[cid].sales_contacts_30d = int(n)

    cs = ds.charge_sessions
    failed = cs[
        (cs["session_status"] == "failed")
        & (cs["started_at"] > as_of - pd.Timedelta(days=30))
        & (cs["started_at"] <= as_of)
    ]
    for cid, n in failed["customer_id"].value_counts().items():
        states[cid].failed_charges_30d = int(n)

    if as_of > SNAPSHOT:
        known = ds.events[(ds.events["ingested_at"] <= as_of) & (ds.events["occurred_at"] > SNAPSHOT)]
        for ev in known.itertuples(index=False):
            if ev.customer_id in states:
                apply_event(states[ev.customer_id], ev.event_type, ev.payload)

    return states


def apply_event(s: CustomerState, event_type: str, payload: dict) -> None:
    """Update a state with one stream event. Only events that change eligibility are handled."""
    if event_type == "consent_changed":
        s.consents[payload["consent_type"]] = payload["new_status"]
    elif event_type == "dunning_level_changed":
        # max over contracts: a lowered level on one contract must not clear another one
        s.dunning_level = max(s.dunning_level, int(payload["new_level"]))
    elif event_type == "contract_cancellation_received":
        s.cancellation_received = True
    elif event_type == "ticket_created":
        s.open_service_case = True
        if payload.get("category") == "complaint":
            s.open_complaint = True
    elif event_type == "gas_meter_deregistration_received":
        s.gas_meter_deregistered = True
    elif event_type == "charge_session_failed":
        s.failed_charges_30d += 1
    elif event_type == "hems_device_offline" and payload.get("device_type") == "hems_gateway":
        s.hems_gateway_online = False
