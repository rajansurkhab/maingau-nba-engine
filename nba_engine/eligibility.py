"""Eligibility: hard rules applied BEFORE scoring (slide 9 step 2, slide 12).

Every rule is a predicate object. Evaluating a candidate writes one `RuleResult` per rule
into a trace, which later feeds the decision log and the one-line reason.
Evaluation stops at the first failing rule; the trace shows which one blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from nba_engine.config import CHANNEL_CONSENT, CHANNELS, SALES_ACTION_TYPES, EngineConfig
from nba_engine.state import SHOP_PRODUCT_NAMES, CustomerState


@dataclass(frozen=True)
class Offer:
    offer_id: str
    name: str
    action_type: str  # sales / upsell / retention / one_off / service

    @property
    def is_sales(self) -> bool:
        return self.action_type in SALES_ACTION_TYPES


@dataclass(frozen=True)
class Candidate:
    offer: Offer
    channel: str


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    detail: str


@dataclass
class EligibilityResult:
    customer_id: str
    candidate: Candidate
    trace: list[RuleResult] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return all(r.passed for r in self.trace)

    @property
    def blocked_by(self) -> RuleResult | None:
        return next((r for r in self.trace if not r.passed), None)


Check = Callable[[CustomerState, Candidate, EngineConfig], tuple[bool, str]]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    description: str
    check: Check

    def __call__(self, s: CustomerState, c: Candidate, cfg: EngineConfig) -> RuleResult:
        passed, detail = self.check(s, c, cfg)
        return RuleResult(self.rule_id, passed, detail)


# --- rule checks ---------------------------------------------------------------------------

def _channel_consent(s: CustomerState, c: Candidate, cfg: EngineConfig) -> tuple[bool, str]:
    consent_type = CHANNEL_CONSENT.get(c.channel)
    if consent_type is None:
        return False, f"unknown channel {c.channel}"
    status = s.consent(consent_type)
    return status == "granted", f"{consent_type}={status}"


def _channel_reachable(s: CustomerState, c: Candidate, cfg: EngineConfig) -> tuple[bool, str]:
    if c.channel == "portal_popup" and not s.portal_account:
        return False, "no portal account"
    if c.channel == "app_push" and not s.app_installed:
        return False, "app not installed"
    return True, "reachable"


def _no_dunning(s: CustomerState, c: Candidate, cfg: EngineConfig) -> tuple[bool, str]:
    if c.offer.is_sales and s.dunning_level >= 1:
        return False, f"dunning level {s.dunning_level} blocks sales content"
    return True, f"dunning level {s.dunning_level}"


def _no_open_complaint(s: CustomerState, c: Candidate, cfg: EngineConfig) -> tuple[bool, str]:
    if c.offer.is_sales and s.open_complaint:
        return False, "open complaint blocks sales content"
    return True, "no open complaint" if not s.open_complaint else "open complaint, service action allowed"


def _no_cancellation(s: CustomerState, c: Candidate, cfg: EngineConfig) -> tuple[bool, str]:
    if not (c.offer.is_sales and s.cancellation_received):
        return True, "no cancellation" if not s.cancellation_received else "cancellation, service action allowed"
    if c.offer.action_type == "retention" and cfg.allow_retention_on_cancellation:
        return True, "cancellation received, retention allowed by config"
    return False, "cancellation received blocks sales content"


def _product_fit(s: CustomerState, c: Candidate, cfg: EngineConfig) -> tuple[bool, str]:
    return PRODUCT_PREREQUISITES.get(c.offer.offer_id, lambda _s: (True, "no prerequisite"))(s)


def _contact_limit(s: CustomerState, c: Candidate, cfg: EngineConfig) -> tuple[bool, str]:
    if c.offer.is_sales and s.sales_contacts_30d >= cfg.max_sales_contacts_30d:
        return False, f"{s.sales_contacts_30d} contacts in 30d >= limit {cfg.max_sales_contacts_30d}"
    return True, f"{s.sales_contacts_30d}/{cfg.max_sales_contacts_30d} contacts in 30d"


def _held(flag: bool, what: str) -> tuple[bool, str]:
    return (False, f"already holds {what}") if flag else (True, f"does not hold {what}")


def _requires(flag: bool, what: str) -> tuple[bool, str]:
    return (True, f"has {what}") if flag else (False, f"requires {what}")


def _shop_item(offer_id: str) -> Callable[[CustomerState], tuple[bool, str]]:
    name = SHOP_PRODUCT_NAMES[offer_id]
    return lambda s: _held(name in s.shop_products_owned, name)


def _hems_upsell(device: str) -> Callable[[CustomerState], tuple[bool, str]]:
    def check(s: CustomerState) -> tuple[bool, str]:
        if not s.has_hems:
            return False, "requires HEMS"
        if not s.hems_gateway_online:
            return False, "HEMS gateway offline"
        return _requires(device in s.hems_device_types, device)
    return check


def _wallbox(s: CustomerState) -> tuple[bool, str]:
    if "wallbox" in s.hems_device_types:
        return False, "already has a wallbox (HEMS device)"
    return _shop_item("P-SH-02")(s)


def _gas(s: CustomerState) -> tuple[bool, str]:
    if s.gas_meter_deregistered:
        return False, "gas meter deregistered"
    return _held(s.has_gas, "gas contract")


PRODUCT_PREREQUISITES: dict[str, Callable[[CustomerState], tuple[bool, str]]] = {
    "P-EN-01": lambda s: _held(s.has_electricity, "electricity contract"),
    "P-EN-02": lambda s: _requires(s.has_electricity, "electricity contract"),
    "P-EN-03": lambda s: _held("StromWärme" in s.electricity_tariffs, "StromWärme"),
    "P-EN-04": _gas,
    "P-EN-05": lambda s: _requires(s.smart_meter, "smart meter"),
    "P-MO-01": lambda s: _held(s.has_mobile, "mobile contract"),
    "P-MO-02": lambda s: _requires(s.has_mobile, "mobile contract"),
    "P-MO-03": lambda s: _requires(s.has_mobile, "mobile contract"),
    "P-SL-01": lambda s: _held(s.has_hems, "HEMS"),
    "P-SL-02": _hems_upsell("home_battery"),
    "P-SL-03": _hems_upsell("wallbox"),
    "P-SH-01": _shop_item("P-SH-01"),
    "P-SH-02": _wallbox,
    "P-SH-03": _shop_item("P-SH-03"),
    "P-SH-04": _shop_item("P-SH-04"),
    "P-AS-01": lambda s: _held(s.has_autostrom, "Autostrom"),
}

RULES: tuple[Rule, ...] = (
    Rule("R-CONSENT-CHANNEL", "Channel consent must be granted", _channel_consent),
    Rule("R-CHANNEL-REACHABLE", "Customer must be reachable on the channel", _channel_reachable),
    Rule("R-EXCL-DUNNING", "Dunning level >= 1 blocks sales content", _no_dunning),
    Rule("R-EXCL-COMPLAINT", "Open complaint blocks sales content", _no_open_complaint),
    Rule("R-EXCL-CANCELLATION", "Received cancellation blocks sales content", _no_cancellation),
    Rule("R-PRODUCT-FIT", "Product not held and prerequisites met", _product_fit),
    Rule("R-CONTACT-LIMIT", "Sales contact limit per 30 days", _contact_limit),
)


def offers_from_catalog(catalog: pd.DataFrame) -> list[Offer]:
    return [Offer(r.product_id, r.product_name, r.action_type) for r in catalog.itertuples(index=False)]


def evaluate(s: CustomerState, c: Candidate, cfg: EngineConfig | None = None) -> EligibilityResult:
    cfg = cfg or EngineConfig()
    result = EligibilityResult(s.customer_id, c)
    for rule in RULES:
        outcome = rule(s, c, cfg)
        result.trace.append(outcome)
        if not outcome.passed:
            break
    return result


def evaluate_all(
    s: CustomerState,
    offers: list[Offer],
    channels: tuple[str, ...] = CHANNELS,
    cfg: EngineConfig | None = None,
) -> list[EligibilityResult]:
    """Every offer x channel candidate for one customer, eligible or not (for the log)."""
    return [evaluate(s, Candidate(o, ch), cfg) for o in offers for ch in channels]
