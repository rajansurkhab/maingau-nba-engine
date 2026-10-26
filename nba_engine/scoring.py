"""Scoring: expected CLV change of an action, exactly as in clv_method.txt (slide 7).

    CLV  = sum_{t=1..H} (margin - cost_to_serve) * (1 - churn)^(t-1) / (1 + r)^t
    dCLV = p * (CLV_accept - CLV_now) + (1 - p) * (CLV_reject - CLV_now) - contact_cost

The CLV is the expected discounted reward of a two-state absorbing Markov chain
(active -> churned with probability `churn` per year). An action changes the chain's
parameters; scoring evaluates the chain under accept and reject.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from nba_engine.config import SALES_ACTION_TYPES, EngineConfig
from nba_engine.state import CustomerState

CONTACT_COST_PARAM = {
    "newsletter_email": "contact_cost_email_eur",
    "email": "contact_cost_email_eur",
    "sms": "contact_cost_sms_eur",
    "outbound_call": "contact_cost_call_eur",
    "portal_popup": "contact_cost_popup_eur",
    "app_push": "contact_cost_popup_eur",  # assumption: in-app message costs like a pop-up
}

# churn_drivers text in customer_value -> additive churn parameter in value_assumptions
_DRIVER_PATTERNS = (
    (re.compile(r"price guarantee ends <90d"), "price_guarantee_ends_90d"),
    (re.compile(r"open complaint"), "open_complaint"),
    (re.compile(r"\d+ tickets in 12m"), "tickets_12m_ge2"),
    (re.compile(r"low CSAT"), "csat_le2"),
    (re.compile(r"dunning level \d+"), "dunning_ge2"),
    (re.compile(r"\d+ failed charges in 30d"), "failed_charges_30d_ge3"),
    (re.compile(r"HEMS user"), "hems_user"),
    (re.compile(r"cancellation pending"), "cancellation_pending"),
    (re.compile(r"multi-product \(\d+ lines\)"), "multi_line"),  # multiplicative, already in base
)

SERVICE_DRIVERS = ("open_complaint", "tickets_12m_ge2", "csat_le2", "failed_charges_30d_ge3", "dunning_ge2")
RETENTION_DRIVERS = ("price_guarantee_ends_90d",)


def parse_drivers(text: str | float | None) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    keys = []
    for part in text.split(";"):
        part = part.strip()
        key = next((k for pattern, k in _DRIVER_PATTERNS if pattern.fullmatch(part)), None)
        if key is None:
            raise ValueError(f"unknown churn driver: {part!r}")
        keys.append(key)
    return keys


@dataclass(frozen=True)
class ValueParams:
    discount_rate: float
    horizon_years: int
    assumptions: dict[str, float]

    @classmethod
    def from_assumptions(cls, va: dict[str, float]) -> ValueParams:
        return cls(va["discount_rate"], int(va["horizon_years"]), dict(va))

    def __getitem__(self, key: str) -> float:
        return self.assumptions[key]

    def contact_cost(self, channel: str) -> float:
        return self.assumptions[CONTACT_COST_PARAM[channel]]


def clv(net_margin: float, churn: float, params: ValueParams) -> float:
    """Present value over the horizon; churn is clipped to [0, 1]."""
    q = 1.0 - min(max(churn, 0.0), 1.0)
    d = 1.0 + params.discount_rate
    return sum(net_margin * q ** (t - 1) / d**t for t in range(1, params.horizon_years + 1))


@dataclass
class CustomerValue:
    customer_id: str
    annual_margin: float
    cost_to_serve: float
    churn: float
    drivers: list[str] = field(default_factory=list)
    clv_reported: float = 0.0  # clv_eur from the BI model, for display

    @property
    def net_margin(self) -> float:
        return self.annual_margin - self.cost_to_serve

    @property
    def cancellation_pending(self) -> bool:
        return "cancellation_pending" in self.drivers

    def churn_without(self, removed: tuple[str, ...], params: ValueParams) -> float:
        """Churn after removing additive drivers. A pending cancellation fixes churn at 0.95."""
        if self.cancellation_pending:
            return self.churn
        delta = sum(params[d] for d in self.drivers if d in removed)
        return max(self.churn - delta, 0.0)


def customer_values(customer_value: pd.DataFrame) -> dict[str, CustomerValue]:
    return {
        r.customer_id: CustomerValue(
            customer_id=r.customer_id,
            annual_margin=float(r.annual_margin_eur),
            cost_to_serve=float(r.cost_to_serve_12m_eur),
            churn=float(r.churn_prob_12m),
            drivers=parse_drivers(r.churn_drivers),
            clv_reported=float(r.clv_eur),
        )
        for r in customer_value.itertuples(index=False)
    }


@dataclass(frozen=True)
class ProductEconomics:
    offer_id: str
    action_type: str
    annual_margin_if_bought: float
    churn_effect_if_bought: float

    @classmethod
    def from_catalog(cls, catalog: pd.DataFrame) -> dict[str, ProductEconomics]:
        return {
            r.product_id: cls(
                r.product_id,
                r.action_type,
                float(r.annual_margin_if_bought_eur) if pd.notna(r.annual_margin_if_bought_eur) else 0.0,
                float(r.churn_effect_if_bought) if pd.notna(r.churn_effect_if_bought) else 0.0,
            )
            for r in catalog.itertuples(index=False)
        }


@dataclass(frozen=True)
class Score:
    customer_id: str
    offer_id: str
    channel: str
    tone: str
    p_accept: float
    clv_now: float
    clv_accept: float
    clv_reject: float
    contact_cost: float
    dclv: float
    reject_penalty: bool  # sales contact during an open service case

    @property
    def gain_if_accept(self) -> float:
        return self.clv_accept - self.clv_now

    @property
    def loss_if_reject(self) -> float:
        return self.clv_reject - self.clv_now


def clv_after_accept(cv: CustomerValue, product: ProductEconomics, params: ValueParams, cfg: EngineConfig) -> float:
    if product.action_type == "retention":
        return clv(cv.net_margin, cv.churn_without(RETENTION_DRIVERS, params), params)
    if product.action_type == "service":
        removed = SERVICE_DRIVERS if cfg.service_fix_removes_dunning else tuple(
            d for d in SERVICE_DRIVERS if d != "dunning_ge2"
        )
        churn = cv.churn_without(removed, params)
        return clv(cv.net_margin, churn, params) - params["service_resolution_cost_eur"]
    churn = cv.churn + product.churn_effect_if_bought
    if product.action_type == "one_off":
        # one-off shop margin counted once, not as an annuity
        return clv(cv.net_margin, churn, params) + product.annual_margin_if_bought
    return clv(cv.net_margin + product.annual_margin_if_bought, churn, params)


def has_reject_penalty(state: CustomerState, product: ProductEconomics, cfg: EngineConfig) -> bool:
    if product.action_type not in SALES_ACTION_TYPES:
        return False
    in_case = state.open_complaint or state.dunning_level >= 1
    if cfg.reject_penalty_on_any_open_ticket:
        in_case = in_case or state.open_service_case
    return in_case


def score(
    state: CustomerState,
    cv: CustomerValue,
    product: ProductEconomics,
    channel: str,
    tone: str,
    p_accept: float,
    params: ValueParams,
    cfg: EngineConfig | None = None,
) -> Score:
    cfg = cfg or EngineConfig()
    now = clv(cv.net_margin, cv.churn, params)
    accept = clv_after_accept(cv, product, params, cfg)
    penalty = has_reject_penalty(state, product, cfg)
    reject = clv(cv.net_margin, cv.churn + params["contact_during_service_case"], params) if penalty else now
    cost = params.contact_cost(channel)
    dclv = p_accept * (accept - now) + (1 - p_accept) * (reject - now) - cost
    return Score(state.customer_id, product.offer_id, channel, tone, p_accept, now, accept, reject, cost, dclv, penalty)
