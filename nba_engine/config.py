"""Static settings and marketing-editable defaults."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import sha1
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

    # --- personalisation (only with profiling_analytics consent, slide 12) ---
    # A21: a trigger signal counts as evidence of intent, like a click. Strength 1.0 moves
    # p_accept all the way to the learned "conversion after click" rate of the offer.
    signal_strength: dict[str, float] = field(default_factory=lambda: {
        "gas_meter_deregistered": 1.0,
        "heat_pump_interest": 1.0,
        "heat_pump_device": 0.5,
        "wallbox_purchase": 1.0,
    })
    heat_pump_clicks_min: int = 3
    signal_lookback_days: int = 365
    # adjusted p_accept is capped; the history never shows more than ~31 % after a click
    max_p_accept: float = 0.5
    # channel affinity multiplies acceptance odds within these bounds
    affinity_bounds: tuple[float, float] = (0.1, 3.0)
    # A22: without profiling consent the engine addresses customers with "Sie"
    tone_requires_profiling: bool = True

    # --- arbitration (slide 10: set offers, priorities and contact limits) ---
    disabled_offers: set[str] = field(default_factory=set)
    # slide 10: marketing approves channels and tone
    disabled_channels: set[str] = field(default_factory=set)
    allow_du_tone: bool = True
    # ranking weight per offer; dCLV > 0 is still required, the weight only reorders
    offer_priority: dict[str, float] = field(default_factory=dict)
    # A19: max actions per channel and run (None = unlimited); call centre capacity
    channel_capacity: dict[str, int] = field(default_factory=lambda: {"outbound_call": 25})
    top_k: int = 3
    # slide 9/12: hold out 10 % of sales decisions as control group; service is never held out
    control_share: float = 0.10

    def version(self) -> str:
        """Short hash of all settings, stored with every decision."""
        payload = json.dumps(asdict(self), sort_keys=True, default=sorted)
        return sha1(payload.encode()).hexdigest()[:8]
