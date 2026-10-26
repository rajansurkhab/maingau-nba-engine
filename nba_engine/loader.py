"""Load the mock data and neutralise the intentional data traps.

Traps handled here (README + slide 12 notes):
- duplicate events            -> dedupe on event_id
- Apple MPP machine opens     -> `human_open` ignores apple_mpp_prefetch
- guest shop orders           -> split off into `guest_orders`
- legacy actions w/o consent  -> `usable_for_learning` excludes them (and the control group)
- late roaming CDRs           -> helpers count by `started_at`, never by `cdr_received_at`
Consent withdrawn by unsubscribe and offline HEMS gateways are resolved in `state.py`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from nba_engine.config import CSV_DIR, EVENT_FILE

DATE_COLUMNS = {
    "customer_master": ["customer_since", "record_updated_at"],
    "energy_contracts": [
        "start_date", "price_guarantee_until", "cancellation_received_at",
        "last_meter_reading_at", "record_updated_at", "meter_deregistered_at",
    ],
    "mobile_contracts": ["start_date", "min_term_end", "record_updated_at"],
    "shop_orders": ["order_created_at", "record_updated_at"],
    "hems_devices": ["activated_at", "last_seen_at"],
    "hems_telemetry_daily": ["date"],
    "consent_status": ["updated_at", "double_opt_in_confirmed_at"],
    "newsletter_campaigns": ["sent_at"],
    "newsletter_performance": ["opened_at", "clicked_at"],
    "portal_sessions": ["session_start"],
    "service_interactions": ["created_at", "resolved_at"],
    "autostrom_contracts": ["start_date", "cancelled_at", "record_updated_at"],
    "charge_sessions": ["started_at", "ended_at", "cdr_received_at"],
    "nba_action_history": ["decided_at", "response_at"],
    "call_transcripts": ["call_started_at"],
    "service_nba_feedback": ["shown_at"],
    "customer_value": ["clv_calc_date"],
}

BOOL_COLUMNS = {
    "customer_master": ["portal_account", "app_installed"],
    "energy_contracts": ["feed_in_meter"],
    "mobile_contracts": ["esim"],
    "newsletter_performance": ["delivered", "bounced", "converted_within_7d", "unsubscribed"],
    "portal_sessions": ["offer_banner_shown"],
    "service_interactions": ["first_contact_resolution"],
    "autostrom_contracts": ["app_user"],
    "nba_action_history": ["control_group", "converted_within_14d", "consent_checked"],
    "service_nba_feedback": ["offer_mentioned_to_customer", "converted_within_14d"],
}


@dataclass
class Dataset:
    customer_master: pd.DataFrame
    energy_contracts: pd.DataFrame
    mobile_contracts: pd.DataFrame
    autostrom_contracts: pd.DataFrame
    charge_sessions: pd.DataFrame
    shop_orders: pd.DataFrame
    guest_orders: pd.DataFrame
    hems_devices: pd.DataFrame
    hems_telemetry_daily: pd.DataFrame
    consent_status: pd.DataFrame
    newsletter_campaigns: pd.DataFrame
    newsletter_performance: pd.DataFrame
    portal_sessions: pd.DataFrame
    service_interactions: pd.DataFrame
    call_transcripts: pd.DataFrame
    service_nba_feedback: pd.DataFrame
    nba_action_history: pd.DataFrame
    product_catalog: pd.DataFrame
    customer_value: pd.DataFrame
    value_assumptions: dict[str, float]
    events: pd.DataFrame
    raw_event_count: int


def _to_bool(series: pd.Series) -> pd.Series:
    return series.map({True: True, False: False, "True": True, "False": False}).astype("boolean")


def _read_csv(csv_dir: Path, name: str) -> pd.DataFrame:
    df = pd.read_csv(csv_dir / f"{name}.csv", encoding="utf-8")
    for col in DATE_COLUMNS.get(name, []):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in BOOL_COLUMNS.get(name, []):
        df[col] = _to_bool(df[col])
    return df


def load_events(path: Path = EVENT_FILE) -> tuple[pd.DataFrame, int]:
    """Event stream, deduplicated on event_id and sorted by occurrence."""
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    events = pd.DataFrame(rows)
    events = events.drop_duplicates(subset="event_id", keep="first")
    for col in ("occurred_at", "ingested_at"):
        events[col] = pd.to_datetime(events[col])
    return events.sort_values("occurred_at").reset_index(drop=True), len(rows)


def load_dataset(csv_dir: Path = CSV_DIR, event_file: Path = EVENT_FILE) -> Dataset:
    t = {name: _read_csv(csv_dir, name) for name in DATE_COLUMNS}
    t["product_catalog"] = _read_csv(csv_dir, "product_catalog")

    shop = t["shop_orders"]
    is_guest = shop["customer_id"].isna()
    t["guest_orders"] = shop[is_guest].reset_index(drop=True)
    t["shop_orders"] = shop[~is_guest].reset_index(drop=True)

    nl = t["newsletter_performance"]
    nl["human_open"] = nl["opened_at"].notna() & (nl["open_client_class"] == "human")

    hist = t["nba_action_history"]
    hist["usable_for_learning"] = ~hist["control_group"] & hist["consent_checked"]

    va = pd.read_csv(csv_dir / "value_assumptions.csv")
    events, raw_event_count = load_events(event_file)

    return Dataset(
        **t,
        value_assumptions=dict(zip(va["parameter"], va["value"].astype(float))),
        events=events,
        raw_event_count=raw_event_count,
    )


@lru_cache(maxsize=1)
def default_dataset() -> Dataset:
    """Cached dataset for UI and tests. Treat as read-only."""
    return load_dataset()


def failed_charges(ds: Dataset, customer_id: str, as_of: pd.Timestamp, days: int = 30) -> int:
    """Failed charging sessions in the window before `as_of`, by session start (late CDR trap)."""
    cs = ds.charge_sessions
    window = (cs["started_at"] > as_of - pd.Timedelta(days=days)) & (cs["started_at"] <= as_of)
    mask = (cs["customer_id"] == customer_id) & (cs["session_status"] == "failed") & window
    return int(mask.sum())
