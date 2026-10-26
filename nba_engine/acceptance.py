"""p_accept: hierarchical Beta-Binomial shrinkage over the legacy action history.

Hierarchy (root -> leaf): global -> action_type -> offer -> offer x channel -> offer x channel x tone.
Each level is a Beta prior centred on its parent's estimate with `strength` pseudo-observations:

    p(node) = (successes + strength * p(parent)) / (trials + strength)

Sparse cells fall back smoothly to their parent; offers never seen in the history get
their action_type rate. "Accept" = converted_within_14d (assumption A2).
Learning rows exclude the control group (uplift baseline) and legacy rows with
consent_checked=False (assumptions A3, A4). The rates are population/segment rates,
so they are allowed for customers without profiling consent (slide 12).

`update()` is the Bayesian learning step for the feedback loop (deep dive A): every new
response is one more Bernoulli observation for all levels, O(levels).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

ROOT = ("global",)


@dataclass
class AcceptanceModel:
    offer_types: dict[str, str]
    strength: float = 30.0
    # Prior mean for action types with no history at all (e.g. service).
    type_priors: dict[str, float] = field(default_factory=dict)
    counts: dict[tuple, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))

    def _path(self, offer_id: str, channel: str, tone: str) -> list[tuple]:
        action_type = self.offer_types.get(offer_id, "unknown")
        return [
            ROOT,
            ("type", action_type),
            ("offer", offer_id),
            ("offer_channel", offer_id, channel),
            ("offer_channel_tone", offer_id, channel, tone),
        ]

    def update(self, offer_id: str, channel: str, tone: str, accepted: bool) -> None:
        for key in self._path(offer_id, channel, tone):
            cell = self.counts[key]
            cell[0] += int(accepted)
            cell[1] += 1

    def _cell(self, key: tuple) -> tuple[int, int]:
        s, n = self.counts.get(key, (0, 0))
        return s, n

    def p(self, offer_id: str, channel: str, tone: str) -> float:
        alpha, beta = self.posterior(offer_id, channel, tone)
        return alpha / (alpha + beta)

    def posterior(self, offer_id: str, channel: str, tone: str) -> tuple[float, float]:
        """Beta(alpha, beta) of the leaf cell: its counts on top of a prior centred on the parent.

        Empirical Bayes: the parent estimate is treated as known, so the spread is a lower
        bound of the true uncertainty.
        """
        path = self._path(offer_id, channel, tone)
        s, n = self._cell(ROOT)
        p = (s + 1) / (n + 2)  # Laplace at the root
        for key in path[1:-1]:
            if key[0] == "type" and key[1] in self.type_priors:
                p = self.type_priors[key[1]]
            s, n = self._cell(key)
            p = (s + self.strength * p) / (n + self.strength)
        s, n = self._cell(path[-1])
        return max(s + self.strength * p, 1e-9), max(n - s + self.strength * (1 - p), 1e-9)

    def p_sample(self, offer_id: str, channel: str, tone: str, rng, concentration: float = 1.0) -> float:
        """ML-Upgrade V1: one Thompson draw from the leaf posterior (rng = random.Random).

        `concentration` > 1 narrows the posterior around the same mean (less exploration).
        """
        alpha, beta = self.posterior(offer_id, channel, tone)
        return rng.betavariate(alpha * concentration, beta * concentration)

    def trials(self, offer_id: str, channel: str, tone: str) -> int:
        """Observations behind the leaf estimate (0 = pure prior), for the reason text."""
        return self._cell(self._path(offer_id, channel, tone)[-1])[1]

    @classmethod
    def fit(
        cls,
        history: pd.DataFrame,
        catalog: pd.DataFrame,
        strength: float = 30.0,
        type_priors: dict[str, float] | None = None,
        clicked_only: bool = False,
    ) -> AcceptanceModel:
        """`clicked_only=True` learns P(convert | intent shown), used for trigger signals."""
        model = cls(
            offer_types=dict(zip(catalog["product_id"], catalog["action_type"])),
            strength=strength,
            type_priors=dict(type_priors or {}),
        )
        rows = history[history["usable_for_learning"]]
        if clicked_only:
            rows = rows[rows["response"] == "clicked"]
        for r in rows.itertuples(index=False):
            model.update(r.offer_id, r.channel, r.tonality, bool(r.converted_within_14d))
        return model


def uplift_by_offer(history: pd.DataFrame) -> pd.DataFrame:
    """Treatment vs. control conversion per offer (causal baseline, slide 8 'Learn').

    Treatment = delivered with consent check; control = held-out rows with consent check.
    """
    h = history[history["consent_checked"]]
    grouped = h.groupby(["offer_id", "control_group"])["converted_within_14d"].agg(["sum", "count"]).unstack()
    out = pd.DataFrame({
        "treated_n": grouped[("count", False)],
        "treated_conv": grouped[("sum", False)],
        "control_n": grouped[("count", True)],
        "control_conv": grouped[("sum", True)],
    }).fillna(0)
    out["treated_rate"] = out["treated_conv"] / out["treated_n"]
    out["control_rate"] = out["control_conv"] / out["control_n"]
    out["uplift"] = out["treated_rate"] - out["control_rate"]
    return out
