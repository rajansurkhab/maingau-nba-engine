"""Arbitration (slide 9 step 4): rank by expected CLV gain, drop negative actions,
respect marketing switches, priorities and channel capacity.

Global assignment is a greedy pass over all positive options sorted by rank value
(dCLV x marketing priority). A customer gets at most one action per run; when a channel
is full, the customer falls back to their next best option. O(M log M) for M options.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from nba_engine.config import EngineConfig
from nba_engine.eligibility import Offer
from nba_engine.scoring import Score


@dataclass(frozen=True)
class Option:
    """An eligible, scored candidate."""

    offer: Offer
    score: Score
    priority: float = 1.0

    @property
    def customer_id(self) -> str:
        return self.score.customer_id

    @property
    def channel(self) -> str:
        return self.score.channel

    @property
    def rank_value(self) -> float:
        return self.score.dclv * self.priority


@dataclass
class ArbitrationResult:
    chosen: dict[str, Option | None] = field(default_factory=dict)
    ranked: dict[str, list[Option]] = field(default_factory=dict)  # top-k, chosen first
    notes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


def make_option(offer: Offer, score: Score, cfg: EngineConfig) -> Option:
    return Option(offer, score, cfg.offer_priority.get(offer.offer_id, 1.0))


def _best_per_offer(options: list[Option]) -> list[Option]:
    best: dict[str, Option] = {}
    for o in options:
        current = best.get(o.offer.offer_id)
        if current is None or o.rank_value > current.rank_value:
            best[o.offer.offer_id] = o
    return sorted(best.values(), key=lambda o: o.rank_value, reverse=True)


def arbitrate(options_by_customer: dict[str, list[Option]], cfg: EngineConfig) -> ArbitrationResult:
    result = ArbitrationResult()
    positive: dict[str, list[Option]] = {}
    for cid, options in options_by_customer.items():
        keep = []
        for o in options:
            if o.score.dclv <= 0:
                continue
            if o.offer.offer_id in cfg.disabled_offers:
                result.notes[cid].append(f"{o.offer.offer_id} disabled by marketing")
                continue
            keep.append(o)
        positive[cid] = keep
        result.notes[cid] = sorted(set(result.notes[cid]))

    capacity = dict(cfg.channel_capacity)
    pool = sorted(
        (o for opts in positive.values() for o in opts),
        key=lambda o: (-o.rank_value, o.customer_id, o.offer.offer_id, o.channel),
    )
    for o in pool:
        cid = o.customer_id
        if result.chosen.get(cid) is not None:
            continue
        left = capacity.get(o.channel)
        if left is not None and left <= 0:
            note = f"{o.channel} capacity reached"
            if note not in result.notes[cid]:
                result.notes[cid].append(note)
            continue
        result.chosen[cid] = o
        if left is not None:
            capacity[o.channel] = left - 1

    for cid in options_by_customer:
        chosen = result.chosen.setdefault(cid, None)
        others = [o for o in _best_per_offer(positive[cid]) if chosen is None or o.offer.offer_id != chosen.offer.offer_id]
        result.ranked[cid] = ([chosen] if chosen else []) + others[: cfg.top_k - (1 if chosen else 0)]
    return result
