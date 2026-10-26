"""Channel & Tone (slide 9 step 5).

Tone: Du/Sie is not labelled, so it is derived from the customer's own words in
service_interactions.message_text and the "Kunde:" turns of call_transcripts. Agent turns
are ignored (agents always say "Sie"). Default and tie: "sie".

Channel affinity: how responsive a customer is on a channel compared with the population,
from human newsletter opens (Apple MPP excluded), portal visits and app visits. It
multiplies the acceptance odds, so channel choice still runs through dCLV.

Both are personalisation and only apply with profiling_analytics consent (slide 12).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from nba_engine.config import SNAPSHOT, EngineConfig
from nba_engine.loader import Dataset
from nba_engine.state import CustomerState

_FORMAL = re.compile(
    r"\b(Sehr geehrte\w*|Guten Tag|Mit freundlichen Grüßen|Freundliche Grüße|Sie|Ihnen|Ihre[mnrs]?|"
    r"auf Wiederhören|im Voraus)\b"
)
_INFORMAL = re.compile(
    r"\b(Hi|Hey|Hallo|LG|VG|du|dich|dir|dein\w*|ihr|euch|euer\w*|habt|könnt|tschüss|Tschüss|Servus)\b"
)


@dataclass(frozen=True)
class ToneResult:
    tone: str  # "du" / "sie"
    formal_hits: int
    informal_hits: int
    texts: int
    used: bool  # False: profiling not allowed or no text, default "sie" applied
    du_blocked: bool = False  # detected "du", but marketing has not approved Du content

    @property
    def evidence(self) -> str:
        if not self.used:
            return "default Sie"
        text = f"{self.texts} texts, {self.formal_hits} formal vs {self.informal_hits} informal markers"
        return text + ("; Du detected but not approved, using Sie" if self.du_blocked else "")


def tone_markers(text: str) -> tuple[int, int]:
    """(formal, informal) marker counts in one customer text."""
    return len(_FORMAL.findall(text)), len(_INFORMAL.findall(text))


def customer_turns(transcript: str) -> str:
    return " ".join(line[len("Kunde:"):].strip() for line in transcript.splitlines() if line.startswith("Kunde:"))


class ToneDetector:
    def __init__(self, ds: Dataset, cfg: EngineConfig):
        self.cfg = cfg
        self.texts: dict[str, list[tuple[pd.Timestamp, str]]] = defaultdict(list)
        si = ds.service_interactions.dropna(subset=["message_text"])
        for r in si.itertuples(index=False):
            self.texts[r.customer_id].append((r.created_at, r.message_text))
        for r in ds.call_transcripts.itertuples(index=False):
            self.texts[r.customer_id].append((r.call_started_at, customer_turns(r.transcript)))

    def detect(self, state: CustomerState, as_of: pd.Timestamp = SNAPSHOT) -> ToneResult:
        texts = [t for ts, t in self.texts.get(state.customer_id, []) if pd.isna(ts) or ts <= as_of]
        formal = informal = votes = 0
        for text in texts:
            f, i = tone_markers(text)
            formal, informal = formal + f, informal + i
            votes += (i > f) - (f > i)  # one vote per text, so one long call does not dominate
        allowed = state.profiling_allowed or not self.cfg.tone_requires_profiling
        if not allowed or not texts:
            return ToneResult("sie", formal, informal, len(texts), used=False)
        if votes > 0 and not self.cfg.allow_du_tone:
            return ToneResult("sie", formal, informal, len(texts), used=True, du_blocked=True)
        return ToneResult("du" if votes > 0 else "sie", formal, informal, len(texts), used=True)


@dataclass(frozen=True)
class ChannelAffinity:
    factors: dict[str, float] = field(default_factory=dict)  # channel -> odds multiplier
    evidence: dict[str, str] = field(default_factory=dict)
    used: bool = False

    def factor(self, channel: str) -> float:
        return self.factors.get(channel, 1.0) if self.used else 1.0


class AffinityModel:
    """Customer engagement per channel relative to the population, smoothed toward 1."""

    WINDOW_DAYS = 91  # portal/app sessions cover Jul-Sep
    NEWSLETTERS = 6  # last six newsletters

    def __init__(self, ds: Dataset, cfg: EngineConfig):
        self.cfg = cfg
        nl = ds.newsletter_performance.merge(ds.newsletter_campaigns[["campaign_id", "sent_at"]], on="campaign_id")
        self.newsletters = nl[nl["delivered"].fillna(False)][["customer_id", "sent_at", "human_open"]]
        self.sessions = ds.portal_sessions[["customer_id", "session_start", "client"]]
        delivered = self.newsletters
        self.pop_open_rate = float(delivered["human_open"].mean())
        weeks = self.WINDOW_DAYS / 7
        portal_users = ds.customer_master.loc[ds.customer_master["portal_account"], "customer_id"]
        app_users = ds.customer_master.loc[ds.customer_master["app_installed"], "customer_id"]
        self.pop_portal_per_week = len(self.sessions[self.sessions["customer_id"].isin(portal_users)]) / max(len(portal_users), 1) / weeks
        app = self.sessions[self.sessions["client"] == "app"]
        self.pop_app_per_week = len(app[app["customer_id"].isin(app_users)]) / max(len(app_users), 1) / weeks
        self._nl_by_cid = {cid: g for cid, g in self.newsletters.groupby("customer_id")}
        self._sess_by_cid = {cid: g for cid, g in self.sessions.groupby("customer_id")}

    def _clip(self, x: float) -> float:
        lo, hi = self.cfg.affinity_bounds
        return min(max(x, lo), hi)

    def affinity(self, state: CustomerState, as_of: pd.Timestamp = SNAPSHOT) -> ChannelAffinity:
        cid = state.customer_id
        factors, evidence = {}, {}

        nl = self._nl_by_cid.get(cid)
        if nl is not None:
            recent = nl[nl["sent_at"] <= as_of].sort_values("sent_at").tail(self.NEWSLETTERS)
            n, opens = len(recent), int(recent["human_open"].sum())
            if n:
                # Beta smoothing with 2 pseudo-newsletters at the population rate
                rate = (opens + 2 * self.pop_open_rate) / (n + 2)
                factors["newsletter_email"] = self._clip(rate / self.pop_open_rate)
                evidence["newsletter_email"] = f"{opens}/{n} newsletters opened"

        sess = self._sess_by_cid.get(cid)
        since = as_of - pd.Timedelta(days=self.WINDOW_DAYS)
        weeks = self.WINDOW_DAYS / 7
        if state.portal_account:
            k = 0 if sess is None else int(((sess["session_start"] > since) & (sess["session_start"] <= as_of)).sum())
            per_week = (k + 2 * self.pop_portal_per_week) / (weeks + 2)
            factors["portal_popup"] = self._clip(per_week / self.pop_portal_per_week)
            evidence["portal_popup"] = f"{k / weeks:.1f} portal visits/week"
        if state.app_installed:
            k = 0 if sess is None else int(
                ((sess["client"] == "app") & (sess["session_start"] > since) & (sess["session_start"] <= as_of)).sum()
            )
            per_week = (k + 2 * self.pop_app_per_week) / (weeks + 2)
            factors["app_push"] = self._clip(per_week / self.pop_app_per_week)
            evidence["app_push"] = f"{k / weeks:.1f} app visits/week"

        return ChannelAffinity(factors, evidence, used=state.profiling_allowed)
