"""HTTP API for the NBA engine plus the marketing web UI.

Run from the repository root:
    .venv/Scripts/python -m uvicorn api.main:app --port 8000

The endpoints map 1:1 to the MCP tools on the architecture slide (slide 10):
    recommend      -> POST /api/recommend
    customer_360   -> GET  /api/customers/{customer_id}
    get_config     -> GET  /api/config        set_config -> PUT /api/config
    approve        -> POST /api/decisions/{decision_id}/review
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.service import DECISION_TIMES, DEMO_CASES, EngineService, ReviewError, get_service
from nba_engine.config import CHANNELS
from nba_engine.decision_log import CHANNEL_LABELS
from nba_engine.segments import SEGMENT_ORDER

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _default(obj):
    if hasattr(obj, "item"):  # numpy scalars
        return obj.item()
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


class SafeJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=_default).encode("utf-8")


app = FastAPI(
    title="MAINGAU Next Best Action API",
    description="The engine proposes, marketing approves. Nothing is sent automatically.",
    default_response_class=SafeJSONResponse,
)


class ReviewRequest(BaseModel):
    status: Literal["approved", "rejected"]
    actor: str = Field(min_length=1)
    note: str = ""
    override: dict | None = None  # {"offer_id": ..., "channel": ...}


class BulkReviewRequest(BaseModel):
    decision_ids: list[str]
    status: Literal["approved", "rejected"]
    actor: str = Field(min_length=1)
    note: str = ""


class ConfigUpdate(BaseModel):
    actor: str = Field(min_length=1)
    changes: dict


class TimeUpdate(BaseModel):
    time_key: str


class TemplateReview(BaseModel):
    status: Literal["approved", "rejected", "pending"]
    actor: str = Field(min_length=1)


class ActorBody(BaseModel):
    actor: str = Field(min_length=1)


class RecommendRequest(BaseModel):
    customer_ids: list[str] | None = None


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc
    except ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --- meta and run -------------------------------------------------------------------------

@app.get("/api/meta")
def meta(svc: EngineService = Depends(get_service)):
    catalog = svc.ds.product_catalog
    return {
        "decision_times": [{"key": k, "label": label} for k, (label, _) in DECISION_TIMES.items()],
        "time_key": svc.time_key,
        "channels": [{"key": c, "label": CHANNEL_LABELS[c]} for c in CHANNELS],
        "offers": [{"offer_id": r.product_id, "name": r.product_name, "type": r.action_type,
                    "business_line": r.business_line} for r in catalog.itertuples()],
        "segments": list(SEGMENT_ORDER),
        "demo_cases": DEMO_CASES,
    }


@app.put("/api/time")
def set_time(body: TimeUpdate, svc: EngineService = Depends(get_service)):
    _guard(svc.set_time, body.time_key)
    return svc.summary()


@app.get("/api/summary")
def summary(svc: EngineService = Depends(get_service)):
    return svc.summary()


@app.post("/api/recommend")
def recommend(body: RecommendRequest, svc: EngineService = Depends(get_service)):
    """MCP tool `recommend`: the current proposals for the given customers (all if empty)."""
    run = svc.run()
    ids = body.customer_ids or list(run.by_customer)
    missing = [c for c in ids if c not in run.by_customer]
    if missing:
        raise HTTPException(404, f"unknown customers: {missing[:5]}")
    return [svc.decision_row(run, run.by_customer[c]) for c in ids]


# --- decisions and review (human in the loop) ----------------------------------------------

@app.get("/api/decisions")
def decisions(status: str | None = None, offer: str | None = None, channel: str | None = None,
              segment: str | None = None, q: str | None = None, limit: int = 1000,
              svc: EngineService = Depends(get_service)):
    rows = svc.decisions(status=status, offer=offer, channel=channel, segment=segment, q=q)
    return {"total": len(rows), "items": rows[:limit]}


@app.get("/api/decisions/{decision_id}")
def decision(decision_id: str, svc: EngineService = Depends(get_service)):
    return _guard(svc.decision_detail, decision_id)


@app.post("/api/decisions/{decision_id}/review")
def review(decision_id: str, body: ReviewRequest, svc: EngineService = Depends(get_service)):
    """MCP tool `approve`: approve, reject, or approve a ranked alternative instead."""
    return _guard(svc.review, decision_id, body.status, body.actor, body.note, body.override)


@app.post("/api/decisions/review")
def bulk_review(body: BulkReviewRequest, svc: EngineService = Depends(get_service)):
    return svc.bulk_review(body.decision_ids, body.status, body.actor, body.note)


@app.get("/api/customers/{customer_id}")
def customer_360(customer_id: str, svc: EngineService = Depends(get_service)):
    return _guard(svc.customer_360, customer_id)


# --- steering -----------------------------------------------------------------------------

@app.get("/api/config")
def get_config(svc: EngineService = Depends(get_service)):
    return {"config": svc.config_dict(), "history": svc.config_history}


@app.put("/api/config")
def set_config(body: ConfigUpdate, svc: EngineService = Depends(get_service)):
    config = _guard(svc.update_config, body.changes, body.actor)
    return {"config": config, "summary": svc.summary()}


@app.post("/api/config/reset")
def reset_config(body: ActorBody, svc: EngineService = Depends(get_service)):
    config = svc.reset_config(body.actor)
    return {"config": config, "summary": svc.summary()}


# --- content approval ---------------------------------------------------------------------

@app.get("/api/templates")
def templates(svc: EngineService = Depends(get_service)):
    return svc.templates()


@app.post("/api/templates/{key}/review")
def review_template(key: str, body: TemplateReview, svc: EngineService = Depends(get_service)):
    return _guard(svc.review_template, key, body.status, body.actor)


# --- decision log -------------------------------------------------------------------------

@app.get("/api/log/reviews")
def review_log(svc: EngineService = Depends(get_service)):
    return {"reviews": svc.review_log(), "config_changes": svc.config_history,
            "entries": len(svc.log_entries())}


@app.get("/api/log/download")
def download_log(svc: EngineService = Depends(get_service)):
    entries = svc.log_entries()
    lines = (json.dumps(e, ensure_ascii=False, default=_default) + "\n" for e in entries)
    return StreamingResponse(lines, media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=decision_log.jsonl"})


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
