"""Stateful service behind the API: current config, decision time, engine runs, reviews.

Human in the loop (slide 12): the engine only proposes. Marketing approves or rejects
each action, may switch to a ranked alternative (slide 8), and must approve the German
content template first (slide 10). Nothing is ever sent from here.
"""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import pandas as pd

from nba_engine.config import CHANNELS, SNAPSHOT, EngineConfig
from nba_engine.content import all_templates, lint, template_for
from nba_engine.decision_log import CHANNEL_LABELS, DRIVER_LABELS, Decision
from nba_engine.loader import Dataset, default_dataset
from nba_engine.pipeline import Engine
from nba_engine.segments import SEGMENT_ORDER, customer_segments
from nba_engine.signals import SIGNAL_LABELS

DECISION_TIMES = {
    "snapshot": ("Snapshot 30 Sep, 23:59", SNAPSHOT),
    "oct1-1200": ("Replay 1 Oct, 12:00", pd.Timestamp("2026-10-01 12:00")),
    "oct1-1800": ("Replay 1 Oct, 18:00", pd.Timestamp("2026-10-01 18:00")),
    "oct2-0600": ("Replay 2 Oct, 06:00", pd.Timestamp("2026-10-02 06:00")),
}

DEMO_CASES = [
    {"customer_id": "MG-100009", "label": "Slide 7 · failed charging → service fix", "time": "oct1-1800"},
    {"customer_id": "MG-100001", "label": "Slide 9 · formal, weekly portal → pop-up, Sie", "time": "snapshot"},
    {"customer_id": "MG-100060", "label": "Slide 6 · gas meter deregistered → StromWärme", "time": "snapshot"},
    {"customer_id": "MG-100266", "label": "Slide 6 · wallbox bought → Autostrom", "time": "snapshot"},
    {"customer_id": "MG-100125", "label": "Slide 6 · 3 heat-pump clicks → follow-up", "time": "snapshot"},
    {"customer_id": "MG-100004", "label": "Exclusion · dunning + complaint → service only", "time": "snapshot"},
]

# EngineConfig fields marketing may change through the API, with their types.
EDITABLE_CONFIG = {
    "disabled_offers": set, "offer_priority": dict, "disabled_channels": set, "channel_capacity": dict,
    "allow_du_tone": bool, "max_sales_contacts_30d": int, "control_share": float,
    "service_p_accept_prior": float, "allow_retention_on_cancellation": bool,
    "reject_penalty_on_any_open_ticket": bool, "signal_strength": dict,
}


class ReviewError(ValueError):
    pass


@dataclass
class Run:
    engine: Engine
    decisions: list[Decision]
    as_of: pd.Timestamp
    by_id: dict[str, Decision] = field(default_factory=dict)
    by_customer: dict[str, Decision] = field(default_factory=dict)

    def __post_init__(self):
        self.by_id = {d.decision_id: d for d in self.decisions}
        self.by_customer = {d.customer_id: d for d in self.decisions}


def _now() -> str:
    return str(pd.Timestamp.now().floor("s"))


class EngineService:
    def __init__(self, ds: Dataset | None = None, log_path: Path | None = None):
        self.ds = ds or default_dataset()
        self.log_path = log_path
        self.lock = threading.RLock()
        self.cfg = EngineConfig()
        self.time_key = "snapshot"
        self.segments = customer_segments(self.ds)
        catalog = self.ds.product_catalog
        self._offer_names = dict(zip(catalog["product_id"], catalog["product_name"]))
        self._offer_types = dict(zip(catalog["product_id"], catalog["action_type"]))
        self._runs: dict[tuple[str, str], Run] = {}
        # latest review per customer; re-applied by later runs when the action is still allowed
        self._reviews: dict[str, dict] = {}
        self.template_status: dict[str, dict] = {
            t.key: {"status": "pending", "actor": None, "ts": None} for t in all_templates()
        }
        self.config_history: list[dict] = []

    # --- runs -----------------------------------------------------------------------------

    @property
    def as_of(self) -> pd.Timestamp:
        return DECISION_TIMES[self.time_key][1]

    def run(self) -> Run:
        with self.lock:
            key = (self.cfg.version(), self.time_key)
            if key not in self._runs:
                engine = Engine(self.ds, self.cfg, log_path=self.log_path)
                run = Run(engine, engine.recommend(as_of=self.as_of), self.as_of)
                self._carry_over_reviews(run)
                self._runs[key] = run
            return self._runs[key]

    def _carry_over_reviews(self, run: Run) -> None:
        """Re-apply each customer's latest review when the new run still allows that action.

        Same action -> same status. An approved action that is now only an alternative is
        approved as an override. Anything else goes back to marketing for a fresh review.
        """
        for d in run.decisions:
            prior = self._reviews.get(d.customer_id)
            if d.status != "proposed" or not prior or prior["tone"] != d.tone:
                continue
            note = f"carried over from {prior['decision_id']}"
            action = (prior["offer_id"], prior["channel"])
            if action == (d.offer_id, d.channel):
                run.engine.log.set_status(d.decision_id, prior["status"], prior["actor"], _now(), note=note)
            elif prior["status"] == "approved" and action in {(a["offer_id"], a["channel"]) for a in d.alternatives}:
                run.engine.log.set_status(d.decision_id, "approved", prior["actor"], _now(), note=note,
                                          override={"offer_id": action[0], "channel": action[1]})

    def set_time(self, time_key: str) -> None:
        if time_key not in DECISION_TIMES:
            raise ValueError(f"unknown decision time {time_key}")
        with self.lock:
            self.time_key = time_key

    # --- config ---------------------------------------------------------------------------

    def config_dict(self) -> dict:
        cfg = asdict(self.cfg)
        out = {k: (sorted(v) if isinstance(v, set) else v) for k, v in cfg.items()}
        out["version"] = self.cfg.version()
        return out

    def update_config(self, patch: dict, actor: str) -> dict:
        unknown = set(patch) - set(EDITABLE_CONFIG)
        if unknown:
            raise ValueError(f"not editable: {sorted(unknown)}")
        changes = {}
        for key, value in patch.items():
            kind = EDITABLE_CONFIG[key]
            changes[key] = set(value) if kind is set else dict(value) if kind is dict else kind(value)
        if "channel_capacity" in changes:
            changes["channel_capacity"] = {k: int(v) for k, v in changes["channel_capacity"].items()
                                           if v is not None and k in CHANNELS}
        with self.lock:
            before = self.cfg.version()
            self.cfg = replace(self.cfg, **changes)
            self.config_history.append({"ts": _now(), "actor": actor, "from": before,
                                        "to": self.cfg.version(), "changed": sorted(changes)})
        return self.config_dict()

    def reset_config(self, actor: str) -> dict:
        with self.lock:
            before = self.cfg.version()
            self.cfg = EngineConfig()
            self.config_history.append({"ts": _now(), "actor": actor, "from": before,
                                        "to": self.cfg.version(), "changed": ["reset"]})
        return self.config_dict()

    # --- content --------------------------------------------------------------------------

    def templates(self) -> list[dict]:
        out = []
        for t in all_templates():
            out.append({
                "key": t.key, "offer_id": t.offer_id, "offer_name": self._offer_names.get(t.offer_id, t.offer_id),
                "tone": t.tone, "subject_en": t.subject_en, "body_en": t.body_en,
                "subject_de": t.subject, "body_de": t.body,
                "issues": lint(t.subject, t.body, t.tone), **self.template_status[t.key],
            })
        return out

    def review_template(self, key: str, status: str, actor: str) -> dict:
        if key not in self.template_status:
            raise KeyError(key)
        if status not in ("approved", "rejected", "pending"):
            raise ValueError("status must be approved, rejected or pending")
        offer_id, tone = key.split(":")
        t = template_for(offer_id, tone)
        if status == "approved" and lint(t.subject, t.body, t.tone):
            raise ReviewError("template has lint issues and cannot be approved")
        with self.lock:
            self.template_status[key] = {"status": status, "actor": actor, "ts": _now()}
        return self.template_status[key]

    def content_approved(self, offer_id: str, tone: str) -> bool:
        return self.template_status.get(f"{offer_id}:{tone}", {}).get("status") == "approved"

    # --- decisions ------------------------------------------------------------------------

    def _effective(self, run: Run, d: Decision) -> tuple[str | None, str | None]:
        override = run.engine.log.override(d.decision_id)
        if override:
            return override["offer_id"], override["channel"]
        return d.offer_id, d.channel

    def decision_row(self, run: Run, d: Decision) -> dict:
        offer_id, channel = self._effective(run, d)
        override = run.engine.log.override(d.decision_id)
        alt = next((a for a in d.alternatives if override and a["offer_id"] == offer_id and a["channel"] == channel), None)
        return {
            "decision_id": d.decision_id,
            "customer_id": d.customer_id,
            "segment": self.segments.get(d.customer_id, ""),
            "status": run.engine.log.status(d.decision_id),
            "offer_id": offer_id,
            "offer_name": alt["offer_name"] if alt else d.offer_name,
            "action_type": self._offer_types.get(offer_id) if alt else d.action_type,
            "channel": channel,
            "tone": d.tone,
            "p_accept": alt["p_accept"] if alt else d.p_accept,
            "dclv": alt["dclv"] if alt else d.dclv,
            "clv": d.inputs["clv_reported"],
            "reason": d.reason,
            "signal": d.inputs.get("signal_used"),
            "profiling": d.inputs["profiling_allowed"],
            "content_approved": self.content_approved(offer_id, d.tone) if offer_id else None,
            "overridden": override is not None,
        }

    def decisions(self, status: str | None = None, offer: str | None = None, channel: str | None = None,
                  segment: str | None = None, q: str | None = None) -> list[dict]:
        run = self.run()
        rows = [self.decision_row(run, d) for d in run.decisions]
        if status:
            rows = [r for r in rows if r["status"] == status]
        if offer:
            rows = [r for r in rows if r["offer_id"] == offer]
        if channel:
            rows = [r for r in rows if r["channel"] == channel]
        if segment:
            rows = [r for r in rows if r["segment"] == segment]
        if q:
            rows = [r for r in rows if q.lower() in r["customer_id"].lower() or q.lower() in (r["reason"] or "").lower()]
        return sorted(rows, key=lambda r: (r["dclv"] is None, -(r["dclv"] or 0)))

    def decision_detail(self, decision_id: str) -> dict:
        run = self.run()
        d = run.by_id[decision_id]
        row = self.decision_row(run, d)
        content = None
        if row["offer_id"]:
            t = template_for(row["offer_id"], d.tone)
            content = {"key": t.key, "status": self.template_status[t.key]["status"],
                       "subject_en": t.subject_en, "body_en": t.body_en,
                       "subject_de": t.subject, "body_de": t.body}
        alternatives = [
            {**a, "channel_label": CHANNEL_LABELS[a["channel"]],
             "content_approved": self.content_approved(a["offer_id"], d.tone)}
            for a in d.alternatives
        ]
        scores = {"clv_now": d.clv_now, "clv_accept": d.clv_accept, "clv_reject": d.clv_reject,
                  "contact_cost": d.contact_cost, "p_accept_base": d.inputs.get("p_accept_base")}
        original = None
        override = run.engine.log.override(decision_id)
        if override:
            # re-score the alternative marketing chose, so the numbers shown belong to it
            state = run.engine.states(run.as_of)[d.customer_id]
            ctx = run.engine.evaluate_customer(state, run.as_of)
            s = next(s for _, s in ctx.scored if (s.offer_id, s.channel) == (override["offer_id"], override["channel"]))
            scores = {"clv_now": s.clv_now, "clv_accept": s.clv_accept, "clv_reject": s.clv_reject,
                      "contact_cost": s.contact_cost, "p_accept_base": ctx.p_base[(s.offer_id, s.channel)]}
            original = {"offer_name": d.offer_name, "channel": d.channel, "dclv": d.dclv, "reason": d.reason}
        return {
            **row,
            **scores,
            "original": original,
            "rule_trace": d.rule_trace, "blocked": d.blocked, "notes": d.notes,
            "alternatives": alternatives, "content": content, "control_group": d.control_group,
            "inputs": d.inputs, "config_version": d.config_version, "as_of": d.as_of,
            "history": [e for e in run.engine.log.entries if e["kind"] == "status" and e["decision_id"] == decision_id],
        }

    def review(self, decision_id: str, status: str, actor: str, note: str = "",
               override: dict | None = None) -> dict:
        with self.lock:
            run = self.run()
            if decision_id not in run.by_id:
                raise KeyError(decision_id)
            d = run.by_id[decision_id]
            offer_id = override["offer_id"] if override else d.offer_id
            if status == "approved" and offer_id and not self.content_approved(offer_id, d.tone):
                raise ReviewError(f"content for {offer_id} ({d.tone.capitalize()}) is not approved yet")
            try:
                run.engine.log.set_status(decision_id, status, actor, _now(), note=note, override=override)
            except ValueError as exc:
                raise ReviewError(str(exc)) from exc
            offer_id, channel = self._effective(run, d)
            self._reviews[d.customer_id] = {"status": status, "actor": actor, "decision_id": decision_id,
                                            "offer_id": offer_id, "channel": channel, "tone": d.tone}
            return self.decision_row(run, d)

    def bulk_review(self, decision_ids: list[str], status: str, actor: str, note: str = "") -> dict:
        done, failed = [], {}
        for decision_id in decision_ids:
            try:
                self.review(decision_id, status, actor, note)
                done.append(decision_id)
            except (ReviewError, KeyError) as exc:
                failed[decision_id] = str(exc)
        return {"done": done, "failed": failed}

    # --- summary --------------------------------------------------------------------------

    def summary(self) -> dict:
        run = self.run()
        rows = [self.decision_row(run, d) for d in run.decisions]
        frame = pd.DataFrame(rows)
        acted = frame[frame["offer_id"].notna()]
        status_counts = Counter(frame["status"])
        segments = []
        for seg in SEGMENT_ORDER:
            part = frame[frame["segment"] == seg]
            part_acted = part[part["offer_id"].notna()]
            segments.append({
                "segment": seg,
                "customers": int(len(part)),
                "avg_clv": float(part["clv"].mean()) if len(part) else 0.0,
                "actions": int(len(part_acted)),
                "service_actions": int((part_acted["action_type"] == "service").sum()),
                "expected_gain": float(part_acted["dclv"].sum()),
                "avg_gain": float(part_acted["dclv"].mean()) if len(part_acted) else 0.0,
                "top_offer": part_acted["offer_name"].value_counts().index[0] if len(part_acted) else None,
            })
        by_offer = acted.groupby("offer_name").agg(gain=("dclv", "sum"), count=("dclv", "size"))
        by_channel = acted.groupby("channel").size()
        approved = frame[frame["status"] == "approved"]
        pending_content = {
            (r["offer_id"], r["tone"]) for r in rows if r["status"] == "proposed" and not r["content_approved"]
        }
        return {
            "as_of": str(run.as_of),
            "time_key": self.time_key,
            "config_version": self.cfg.version(),
            "kpis": {
                "customers": int(len(frame)),
                "proposed": status_counts.get("proposed", 0),
                "approved": status_counts.get("approved", 0),
                "rejected": status_counts.get("rejected", 0),
                "held_out": status_counts.get("held_out", 0),
                "no_action": status_counts.get("no_action", 0),
                "expected_gain": float(acted["dclv"].sum()),
                "approved_gain": float(approved["dclv"].sum()),
                "templates_pending": sum(1 for s in self.template_status.values() if s["status"] != "approved"),
                "proposals_blocked_by_content": sum(
                    1 for r in rows if r["status"] == "proposed" and not r["content_approved"]),
                "content_keys_needed": len(pending_content),
            },
            "by_offer": [{"label": k, "gain": float(v.gain), "count": int(v["count"])}
                         for k, v in by_offer.sort_values("gain", ascending=False).iterrows()],
            "by_channel": [{"channel": k, "label": CHANNEL_LABELS[k], "count": int(v)}
                           for k, v in by_channel.sort_values(ascending=False).items()],
            "segments": segments,
        }

    # --- customer 360 ---------------------------------------------------------------------

    def customer_360(self, customer_id: str) -> dict:
        run = self.run()
        if customer_id not in run.by_customer:
            raise KeyError(customer_id)
        engine, ds = run.engine, self.ds
        state = engine.states(run.as_of)[customer_id]
        ctx = engine.evaluate_customer(state, run.as_of)
        master = ds.customer_master.set_index("customer_id").loc[customer_id]
        decision = run.by_customer[customer_id]

        best: dict[str, dict] = {}
        for result, s in ctx.scored:
            offer = result.candidate.offer
            current = best.get(offer.offer_id)
            if current is None or (result.eligible, s.dclv) > (current["eligible"], current["dclv"]):
                best[offer.offer_id] = {
                    "offer_id": offer.offer_id, "offer_name": offer.name, "action_type": offer.action_type,
                    "eligible": result.eligible,
                    "blocked_by": result.blocked_by.rule_id if result.blocked_by else None,
                    "blocked_detail": result.blocked_by.detail if result.blocked_by else None,
                    "channel": s.channel, "p_accept": s.p_accept, "dclv": s.dclv,
                }

        def rows(frame: pd.DataFrame, cols: list[str]) -> list[dict]:
            part = frame[frame["customer_id"] == customer_id][cols]
            return [{k: (None if pd.isna(v) else str(v) if isinstance(v, pd.Timestamp) else v)
                     for k, v in r.items()} for r in part.to_dict("records")]

        si = ds.service_interactions
        tickets = si[(si["customer_id"] == customer_id) & (si["created_at"] <= run.as_of)].sort_values(
            "created_at", ascending=False)
        events = ds.events[(ds.events["customer_id"] == customer_id) & (ds.events["ingested_at"] <= run.as_of)]
        return {
            "customer_id": customer_id,
            "since": str(master.customer_since.date()),
            "city": master.city,
            "household_size": int(master.household_size),
            "segment": self.segments.get(customer_id),
            "tone": ctx.tone.tone,
            "tone_evidence": ctx.tone.evidence,
            "profiling_allowed": state.profiling_allowed,
            "value": {"clv": ctx.value.clv_reported, "churn": ctx.value.churn,
                      "annual_margin": ctx.value.annual_margin, "cost_to_serve": ctx.value.cost_to_serve,
                      "drivers": [DRIVER_LABELS[d] for d in ctx.value.drivers if d in DRIVER_LABELS
                                  and d != "dunning_ge2"]},
            "flags": {"open_complaint": state.open_complaint, "open_service_case": state.open_service_case,
                      "dunning": state.dunning_level >= 1, "cancellation_received": state.cancellation_received},
            "consents": dict(sorted(state.consents.items())),
            "signals": [{"label": SIGNAL_LABELS[s.signal_id], "evidence": s.evidence, "used": s.used}
                        for s in ctx.signals],
            "decision": self.decision_detail(decision.decision_id),
            "candidates": sorted(best.values(), key=lambda c: (not c["eligible"], -c["dclv"])),
            "contracts": {
                "energy": rows(ds.energy_contracts, ["commodity", "tariff", "status", "price_guarantee_until"]),
                "mobile": rows(ds.mobile_contracts, ["plan", "status", "data_volume_gb", "avg_data_used_gb_3m"]),
                "autostrom": rows(ds.autostrom_contracts, ["status", "car_brand", "car_model"]),
                "hems": rows(ds.hems_devices, ["device_type", "connection_status"]),
            },
            "tickets": [
                {"created_at": str(r.created_at), "category": r.category, "channel": r.channel, "status": r.status,
                 "message": None if pd.isna(r.message_text) else r.message_text}
                for r in tickets.head(6).itertuples()
            ],
            "events": [{"occurred_at": str(r.occurred_at), "event_type": r.event_type} for r in events.itertuples()],
        }

    # --- log ------------------------------------------------------------------------------

    def review_log(self) -> list[dict]:
        """Reviews across every run (config version x decision time), newest first."""
        self.run()
        out = []
        for (version, time_key), run in self._runs.items():
            for e in run.engine.log.entries:
                if e["kind"] != "status":
                    continue
                d = run.by_id[e["decision_id"]]
                offer_id = (e.get("override") or {}).get("offer_id") or d.offer_id
                out.append({**e, "customer_id": d.customer_id, "offer_id": offer_id,
                            "offer_name": self._offer_names.get(offer_id, offer_id),
                            "config_version": version, "time_key": time_key})
        return sorted(out, key=lambda e: e["ts"], reverse=True)

    def log_entries(self) -> list[dict]:
        return self.run().engine.log.entries


_service: EngineService | None = None


def get_service() -> EngineService:
    global _service
    if _service is None:
        from nba_engine.config import REPO_ROOT
        _service = EngineService(log_path=REPO_ROOT / "logs" / "decision_log.jsonl")
    return _service
