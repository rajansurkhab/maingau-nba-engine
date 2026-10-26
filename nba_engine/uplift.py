"""Learn (slide 8, deep dive A): did an action cause the result, and how do we find out?

ML-Upgrade V2-light. The legacy history holds out ~10 % of actions at random within each
rule (the control group, slide 12). That randomisation makes "treated vs. control" a
causal comparison, but only for the population the legacy rules targeted, and with 5
control conversions it is far too small for per-customer uplift models (X-learner,
causal forest). So this module does what the data supports:

- pooled uplift with a 95 % confidence interval, a z-test and the Bayesian probability
  that the uplift is positive,
- per-offer uplift, flagged when the data is insufficient,
- an A/B sample-size plan for proving uplift in production,
- a simulated learning curve of the Beta-Binomial model (how the posterior narrows as
  responses arrive), which is the feedback loop of the engine.

Dependency-free except `statistics.NormalDist` from the standard library.
"""
from __future__ import annotations

import copy
import math
import random
from dataclasses import asdict, dataclass
from statistics import NormalDist

import pandas as pd

from nba_engine.acceptance import AcceptanceModel

_Z = NormalDist()


@dataclass(frozen=True)
class UpliftEstimate:
    treated_n: int
    treated_conv: int
    control_n: int
    control_conv: int
    treated_rate: float
    control_rate: float
    uplift: float  # absolute, treated − control
    ci_low: float
    ci_high: float
    p_value: float  # two-sided pooled z-test
    prob_positive: float  # Bayesian P(uplift > 0) with Beta(1, 1) priors
    significant: bool

    def to_dict(self) -> dict:
        return asdict(self)


def wilson(successes: int, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval; stays sensible with 0 or n successes, unlike the Wald interval."""
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def two_proportion(treated_conv: int, treated_n: int, control_conv: int, control_n: int,
                   alpha: float = 0.05, draws: int = 20_000, seed: int = 0) -> UpliftEstimate:
    """Difference of two rates with Newcombe's hybrid score interval (method 10)."""
    pt, pc = treated_conv / treated_n, control_conv / control_n
    diff = pt - pc
    z = _Z.inv_cdf(1 - alpha / 2)
    lt, ut = wilson(treated_conv, treated_n, z)
    lc, uc = wilson(control_conv, control_n, z)
    ci_low = diff - math.sqrt((pt - lt) ** 2 + (uc - pc) ** 2)
    ci_high = diff + math.sqrt((ut - pt) ** 2 + (pc - lc) ** 2)
    pooled = (treated_conv + control_conv) / (treated_n + control_n)
    se0 = math.sqrt(pooled * (1 - pooled) * (1 / treated_n + 1 / control_n))
    p_value = 2 * (1 - _Z.cdf(abs(diff) / se0)) if se0 > 0 else 1.0
    rng = random.Random(seed)
    wins = sum(
        rng.betavariate(1 + treated_conv, 1 + treated_n - treated_conv)
        > rng.betavariate(1 + control_conv, 1 + control_n - control_conv)
        for _ in range(draws)
    )
    return UpliftEstimate(treated_n, treated_conv, control_n, control_conv, pt, pc, diff,
                          ci_low, ci_high, p_value, wins / draws, p_value < alpha)


def _split(history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Treated = delivered legacy actions with consent check; control = held-out ones."""
    h = history[history["consent_checked"]]
    return h[~h["control_group"]], h[h["control_group"]]


def pooled_uplift(history: pd.DataFrame) -> UpliftEstimate:
    treated, control = _split(history)
    return two_proportion(int(treated["converted_within_14d"].sum()), len(treated),
                          int(control["converted_within_14d"].sum()), len(control))


def uplift_by_offer_ci(history: pd.DataFrame, min_control: int = 30) -> list[dict]:
    """Per offer; 'insufficient' when the control arm is too small to say anything."""
    treated, control = _split(history)
    rows = []
    for offer_id in sorted(set(treated["offer_id"]) | set(control["offer_id"])):
        t, c = treated[treated["offer_id"] == offer_id], control[control["offer_id"] == offer_id]
        if len(t) == 0 or len(c) == 0:
            continue
        est = two_proportion(int(t["converted_within_14d"].sum()), len(t),
                             int(c["converted_within_14d"].sum()), len(c), draws=4000)
        verdict = ("insufficient data" if len(c) < min_control
                   else "significant" if est.significant else "not significant")
        rows.append({"offer_id": offer_id, **est.to_dict(), "verdict": verdict})
    return rows


def sample_size(p_control: float, uplift: float, alpha: float = 0.05, power: float = 0.8,
                control_share: float = 0.1) -> dict:
    """Customers per arm to detect an absolute uplift with a two-sided two-proportion test.

    Unequal allocation with k = treated / control = (1 − share) / share.
    """
    p_treated = p_control + uplift
    k = (1 - control_share) / control_share
    p_bar = (p_control + k * p_treated) / (1 + k)
    z_a, z_b = _Z.inv_cdf(1 - alpha / 2), _Z.inv_cdf(power)
    n_control = ((z_a * math.sqrt(p_bar * (1 - p_bar) * (1 + 1 / k))
                  + z_b * math.sqrt(p_control * (1 - p_control) + p_treated * (1 - p_treated) / k)) ** 2) / uplift ** 2
    n_control = math.ceil(n_control)
    n_treated = math.ceil(n_control * k)
    return {"control_share": control_share, "uplift": uplift, "n_control": n_control,
            "n_treated": n_treated, "total": n_control + n_treated}


def ab_plan(history: pd.DataFrame, uplifts=(0.01, 0.02, 0.03, 0.05), shares=(0.1, 0.2, 0.5)) -> dict:
    """Total actions needed to prove a given uplift, for several control-group shares."""
    _, control = _split(history)
    baseline = float(control["converted_within_14d"].mean())
    return {
        "baseline": baseline,
        "available_control": len(control),
        "rows": [sample_size(baseline, u, control_share=s) for u in uplifts for s in shares],
    }


def learning_curve(model: AcceptanceModel, offer_id: str, channel: str, tone: str, true_p: float,
                   steps: int = 400, every: int = 20, seed: int = 0) -> list[dict]:
    """Simulate responses with conversion rate `true_p` and record the posterior after each
    batch. Shows the feedback loop: every response is one `update()`, O(levels)."""
    from scipy.stats import beta as beta_dist

    sim = copy.deepcopy(model)
    rng = random.Random(seed)
    out = []
    for step in range(steps + 1):
        if step % every == 0:
            a, b = sim.posterior(offer_id, channel, tone)
            out.append({"responses": step, "mean": a / (a + b),
                        "low": float(beta_dist.ppf(0.05, a, b)), "high": float(beta_dist.ppf(0.95, a, b))})
        if step < steps:
            sim.update(offer_id, channel, tone, rng.random() < true_p)
    return out
