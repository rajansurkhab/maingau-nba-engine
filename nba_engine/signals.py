"""Trigger signals (slide 6 examples) that raise p_accept for matching offers.

Signals are personalisation: they are only applied with profiling_analytics consent
(slide 12). Without consent they are still detected and logged as "not used", so the
reason can show the compliance decision.

Strength: a signal is treated as evidence of intent, like a click (A21). With strength s,

    p = p_base + s * (p_intent - p_base)

where p_intent is the offer's learned conversion rate after a click (the `intent` model in
the engine, same Beta-Binomial shrinkage, fitted on clicked rows only). Only the strongest
signal per offer counts, so correlated signals (gas deregistration + heat-pump clicks) are
not double counted.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from nba_engine.config import SNAPSHOT, EngineConfig
from nba_engine.loader import Dataset
from nba_engine.state import CustomerState

SIGNAL_OFFERS = {
    "gas_meter_deregistered": ("P-EN-03", "P-SL-01"),
    "heat_pump_interest": ("P-EN-03", "P-SL-01"),
    "heat_pump_device": ("P-EN-03",),
    "wallbox_purchase": ("P-AS-01", "P-SL-03"),
}
SIGNAL_LABELS = {
    "gas_meter_deregistered": "gas meter deregistered",
    "heat_pump_interest": "heat-pump story clicked",
    "heat_pump_device": "heat pump in household",
    "wallbox_purchase": "wallbox bought",
}


@dataclass(frozen=True)
class Signal:
    signal_id: str
    evidence: str
    observed_at: pd.Timestamp | None
    strength: float
    used: bool  # False without profiling consent

    @property
    def offers(self) -> tuple[str, ...]:
        return SIGNAL_OFFERS[self.signal_id]

    @property
    def label(self) -> str:
        return SIGNAL_LABELS[self.signal_id]


def _odds(p: float) -> float:
    return p / (1 - p)


def apply_odds(p: float, multiplier: float) -> float:
    o = _odds(min(max(p, 1e-6), 1 - 1e-6)) * multiplier
    return o / (1 + o)


def apply_signal(p_base: float, p_intent: float, strength: float) -> float:
    """Move p toward the after-click rate. A signal never lowers p."""
    return max(p_base, p_base + strength * (p_intent - p_base))


class SignalDetector:
    """Indexes the raw tables once; `detect()` is then cheap per customer."""

    def __init__(self, ds: Dataset, cfg: EngineConfig):
        self.cfg = cfg

        nl = ds.newsletter_performance
        clicks = nl[(nl["click_target"] == "heat_pump_article") & nl["clicked_at"].notna()]
        self.heat_pump_clicks = {cid: sorted(g["clicked_at"]) for cid, g in clicks.groupby("customer_id")}

        en = ds.energy_contracts
        dereg = en[en["meter_deregistered_at"].notna()]
        self.dereg_at = dereg.groupby("customer_id")["meter_deregistered_at"].max().to_dict()
        self.dereg_events = self._event_times(ds, lambda e: e.event_type == "gas_meter_deregistration_received")

        so = ds.shop_orders
        wb = so[(so["product_name"] == "Wallbox 11 kW") & (so["order_status"] == "delivered")]
        self.wallbox_orders = wb.groupby("customer_id")["order_created_at"].max().to_dict()
        self.wallbox_events = self._event_times(
            ds, lambda e: e.event_type == "order_placed" and e.payload.get("product_name") == "Wallbox 11 kW"
        )

    @staticmethod
    def _event_times(ds: Dataset, pred) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
        out: dict[str, list] = defaultdict(list)
        for e in ds.events.itertuples(index=False):
            if pred(e):
                out[e.customer_id].append((e.occurred_at, e.ingested_at))
        return out

    def detect(self, state: CustomerState, as_of: pd.Timestamp = SNAPSHOT) -> list[Signal]:
        cid = state.customer_id
        since = as_of - pd.Timedelta(days=self.cfg.signal_lookback_days)
        found: list[tuple[str, str, pd.Timestamp | None]] = []

        dereg = [t for t in [self.dereg_at.get(cid)] if t is not None and t <= as_of]
        dereg += [occ for occ, ing in self.dereg_events.get(cid, []) if ing <= as_of]
        if dereg:
            t = max(dereg)
            found.append(("gas_meter_deregistered", f"gas meter deregistered {t:%d.%m.}", t))

        clicks = [t for t in self.heat_pump_clicks.get(cid, []) if since < t <= as_of]
        if len(clicks) >= self.cfg.heat_pump_clicks_min:
            found.append(("heat_pump_interest", f"{len(clicks)} heat-pump story clicks", clicks[-1]))

        if "heat_pump" in state.hems_device_types:
            found.append(("heat_pump_device", "heat pump connected to HEMS", None))

        wallbox = [t for t in [self.wallbox_orders.get(cid)] if t is not None and since < t <= as_of]
        wallbox += [occ for occ, ing in self.wallbox_events.get(cid, []) if ing <= as_of]
        if wallbox:
            t = max(wallbox)
            found.append(("wallbox_purchase", f"wallbox bought {t:%d.%m.}", t))
        elif "wallbox" in state.hems_device_types:
            found.append(("wallbox_purchase", "wallbox connected to HEMS", None))

        used = state.profiling_allowed
        return [Signal(sid, ev, t, self.cfg.signal_strength.get(sid, 0.0), used) for sid, ev, t in found]


def strongest_for(signals: list[Signal], offer_id: str) -> Signal | None:
    """The used signal with the largest strength for this offer."""
    relevant = [s for s in signals if s.used and offer_id in s.offers and s.strength > 0]
    return max(relevant, key=lambda s: s.strength, default=None)
