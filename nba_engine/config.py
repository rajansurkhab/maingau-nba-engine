"""Static settings and marketing-editable defaults."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "Re__Präsentation_Hackathon" / "maingau_nba_mockdata_csv_jsonl"
CSV_DIR = DATA_DIR / "csv"
EVENT_FILE = DATA_DIR / "event_stream_2026-10-01.jsonl"

SNAPSHOT = pd.Timestamp("2026-09-30 23:59:59")

# Outbound channel -> consent_type that must be "granted" (slide 12: no channel without consent).
CHANNEL_CONSENT = {
    "newsletter_email": "newsletter_email",
    "email": "marketing_email",
    "sms": "marketing_sms",
    "outbound_call": "marketing_phone",
    "portal_popup": "portal_app_personalisation",
    "app_push": "portal_app_personalisation",
}
CHANNELS = tuple(CHANNEL_CONSENT)

# product_catalog.action_type values that count as sales content (blocked by exclusions).
SALES_ACTION_TYPES = frozenset({"sales", "upsell", "one_off", "retention"})


@dataclass
class EngineConfig:
    """Marketing-editable parameters. Defaults are working assumptions (PLANUNG §6.2)."""

    # A6: max delivered sales contacts per customer in the last 30 days.
    max_sales_contacts_30d: int = 4
    # Open question 5: retention (P-EN-02) after a received cancellation stays blocked until clarified.
    allow_retention_on_cancellation: bool = False

    # --- scoring (clv_method.txt) ---
    # clv_method applies the reject penalty for open complaint or dunning. Slide 7 says
    # "open service case", so by default any open ticket counts too (A15).
    reject_penalty_on_any_open_ticket: bool = True
    # A16: dunning is payment data. A service fix does not remove the dunning churn driver,
    # so payment data never adds value to an action (slide 12).
    service_fix_removes_dunning: bool = False
    # A17: no history for P-SV-01. Prior acceptance of a proactive service fix.
    service_p_accept_prior: float = 0.5
    # Beta-Binomial pseudo-count per hierarchy level (global -> type -> offer -> channel -> tone).
    shrinkage_strength: float = 30.0
