"""Arbitration (slide 9 step 4): rank by expected CLV gain, drop negative actions,
respect marketing switches, priorities and channel capacity.

Two solvers assign at most one action per customer under channel capacities:

- "greedy" (default): one pass over all positive options sorted by rank value
  (dCLV x marketing priority). When a channel is full, the customer falls back to their
  next best option. O(M log M) for M options.
- "lp" (ML-Upgrade V3): the exact optimum of  max Σ rank_value · x  subject to
  Σ x ≤ 1 per customer and Σ x ≤ capacity per channel. The constraint matrix is the
  incidence matrix of a bipartite graph (customers × channels), so it is totally
  unimodular and the simplex solution is integral. The shadow price of a channel is the
  expected CLV gain of one more slot (re-solved with capacity + 1, because the LP is
  often degenerate and its duals are then not unique).

Greedy gives a scarce call to the customer with the highest dCLV even when that
customer's second-best option is almost as good. The LP gives it to the customer who
loses most without it. Without binding capacity both solvers choose the same actions.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from nba_engine.config import EngineConfig
from nba_engine.eligibility import Offer
from nba_engine.scoring import Score

# Penalty per position within a customer's options (greedy order). It is larger than the
# solver tolerance (~1e-7), so a customer's ties resolve exactly like greedy ties
# (-rank_value, offer_id, channel), and stays below one cent even for 60 options.
_TIE_EPS = 1e-4


@dataclass(frozen=True)
class Option:
    """An eligible, scored candidate."""

    offer: Offer
    score: Score
    priority: float = 1.0
    # ML-Upgrade V1: dCLV under a posterior sample of p_accept. Only reorders options;
    # the dCLV > 0 filter always uses the expected value.
    sample_dclv: float | None = None

    @property
    def customer_id(self) -> str:
        return self.score.customer_id

    @property
    def channel(self) -> str:
        return self.score.channel

    @property
    def rank_value(self) -> float:
        if self.sample_dclv is not None:
            return max(self.sample_dclv, 1e-6) * self.priority
        return self.score.dclv * self.priority


@dataclass
class ArbitrationResult:
    chosen: dict[str, Option | None] = field(default_factory=dict)
    ranked: dict[str, list[Option]] = field(default_factory=dict)  # top-k, chosen first
    notes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    solver: str = "greedy"
    objective: float = 0.0  # Σ rank_value of chosen actions
    shadow_prices: dict[str, float] = field(default_factory=dict)  # LP only: value of one more slot


def make_option(offer: Offer, score: Score, cfg: EngineConfig, sample_dclv: float | None = None) -> Option:
    return Option(offer, score, cfg.offer_priority.get(offer.offer_id, 1.0), sample_dclv)


def _best_per_offer(options: list[Option]) -> list[Option]:
    best: dict[str, Option] = {}
    for o in options:
        current = best.get(o.offer.offer_id)
        if current is None or o.rank_value > current.rank_value:
            best[o.offer.offer_id] = o
    return sorted(best.values(), key=lambda o: o.rank_value, reverse=True)


def _greedy_order(options) -> list[Option]:
    return sorted(options, key=lambda o: (-o.rank_value, o.customer_id, o.offer.offer_id, o.channel))


def _assign_greedy(pool: list[Option], capacity: dict[str, int], result: ArbitrationResult) -> None:
    left = dict(capacity)
    for o in pool:
        cid = o.customer_id
        if result.chosen.get(cid) is not None:
            continue
        slots = left.get(o.channel)
        if slots is not None and slots <= 0:
            note = f"{o.channel} capacity reached"
            if note not in result.notes[cid]:
                result.notes[cid].append(note)
            continue
        result.chosen[cid] = o
        if slots is not None:
            left[o.channel] = slots - 1


def _assign_lp(pool: list[Option], capacity: dict[str, int], result: ArbitrationResult) -> None:
    from scipy.optimize import linprog
    from scipy.sparse import coo_matrix

    if not pool:
        result.shadow_prices = {ch: 0.0 for ch in capacity}
        return
    customers = sorted({o.customer_id for o in pool})
    row_of = {c: i for i, c in enumerate(customers)}
    channels = sorted(capacity)
    channel_row = {ch: len(customers) + k for k, ch in enumerate(channels)}

    rows, cols = [], []
    for j, o in enumerate(pool):
        rows.append(row_of[o.customer_id])
        cols.append(j)
        if o.channel in channel_row:
            rows.append(channel_row[o.channel])
            cols.append(j)
    a_ub = coo_matrix(([1.0] * len(rows), (rows, cols)), shape=(len(customers) + len(channels), len(pool))).tocsr()
    b_ub = [1.0] * len(customers) + [float(capacity[ch]) for ch in channels]
    position: dict[str, int] = defaultdict(int)
    cost = []
    for o in pool:
        cost.append(-o.rank_value + position[o.customer_id] * _TIE_EPS)
        position[o.customer_id] += 1

    def solve(rhs):
        res = linprog(cost, A_ub=a_ub, b_ub=rhs, bounds=(0, 1), method="highs-ds")
        if res.status != 0:
            raise RuntimeError(f"LP arbitration failed: {res.message}")
        if any(0.01 < x < 0.99 for x in res.x):
            raise RuntimeError("LP arbitration returned a fractional solution")
        return res

    res = solve(b_ub)
    for j, x in enumerate(res.x):
        if x > 0.5:
            result.chosen[pool[j].customer_id] = pool[j]
    # The LP is often degenerate, so its duals are not unique. The value of one more slot
    # is therefore measured directly: optimum with capacity + 1 minus current optimum.
    for ch in channels:
        rhs = list(b_ub)
        rhs[channel_row[ch]] += 1
        result.shadow_prices[ch] = max(0.0, res.fun - solve(rhs).fun)

    full = {ch for ch in channels if sum(1 for o in result.chosen.values() if o and o.channel == ch) >= capacity[ch]}
    best = {}
    for o in pool:  # pool is in greedy order, so the first option per customer is their best
        best.setdefault(o.customer_id, o)
    for cid, top in best.items():
        if top.channel in full and result.chosen.get(cid) is not top:
            result.notes[cid].append(f"{top.channel} capacity reached")


def arbitrate(options_by_customer: dict[str, list[Option]], cfg: EngineConfig) -> ArbitrationResult:
    result = ArbitrationResult(solver=cfg.solver)
    positive: dict[str, list[Option]] = {}
    for cid, options in options_by_customer.items():
        keep = []
        for o in options:
            if o.score.dclv <= 0:
                continue
            if o.offer.offer_id in cfg.disabled_offers:
                result.notes[cid].append(f"{o.offer.offer_id} disabled by marketing")
                continue
            if o.channel in cfg.disabled_channels:
                result.notes[cid].append(f"{o.channel} not approved by marketing")
                continue
            keep.append(o)
        positive[cid] = keep
        result.notes[cid] = sorted(set(result.notes[cid]))

    pool = _greedy_order(o for opts in positive.values() for o in opts)
    capacity = dict(cfg.channel_capacity)
    if cfg.solver == "lp":
        _assign_lp(pool, capacity, result)
    elif cfg.solver == "greedy":
        _assign_greedy(pool, capacity, result)
    else:
        raise ValueError(f"unknown solver {cfg.solver}")

    for cid in options_by_customer:
        chosen = result.chosen.setdefault(cid, None)
        others = [o for o in _best_per_offer(positive[cid]) if chosen is None or o.offer.offer_id != chosen.offer.offer_id]
        result.ranked[cid] = ([chosen] if chosen else []) + others[: cfg.top_k - (1 if chosen else 0)]
    result.objective = sum(o.rank_value for o in result.chosen.values() if o)
    return result


def capacity_report(options_by_customer: dict[str, list[Option]], cfg: EngineConfig) -> dict:
    """Greedy vs. LP on the same options, plus LP shadow prices per capacity-limited channel."""
    from dataclasses import replace

    greedy = arbitrate(options_by_customer, replace(cfg, solver="greedy"))
    lp = arbitrate(options_by_customer, replace(cfg, solver="lp"))
    used = defaultdict(int)
    for o in lp.chosen.values():
        if o:
            used[o.channel] += 1
    return {
        "greedy_objective": greedy.objective,
        "lp_objective": lp.objective,
        "lp_gain": lp.objective - greedy.objective,
        "changed_customers": sum(
            1 for cid in options_by_customer
            if (greedy.chosen.get(cid) and (greedy.chosen[cid].offer.offer_id, greedy.chosen[cid].channel))
            != (lp.chosen.get(cid) and (lp.chosen[cid].offer.offer_id, lp.chosen[cid].channel))
        ),
        "channels": [
            {"channel": ch, "capacity": cap, "used": used[ch], "shadow_price": lp.shadow_prices.get(ch, 0.0)}
            for ch, cap in sorted(cfg.channel_capacity.items())
        ],
    }
