"""MCP server for the MAINGAU Next Best Action engine (architecture slide, slide 10).

Read-only tools over the cockpit's HTTP API, so an AI assistant and the marketing team
see the same decisions, settings and reviews. There is deliberately no approve or send
tool: approval stays a human action in the cockpit (slide 12, human in the loop).

Run (the API must be up, default http://localhost:8000):
    .venv/Scripts/python mcp_server/server.py            # stdio transport

Register with Claude Code:
    claude mcp add maingau-nba -- <repo>/.venv/Scripts/python <repo>/mcp_server/server.py
"""
from __future__ import annotations

import os

import httpx
from mcp.server.mcpserver import MCPServer

API_URL = os.environ.get("NBA_API_URL", "http://localhost:8000")

mcp = MCPServer(
    name="maingau-nba",
    instructions=(
        "Next Best Action engine for MAINGAU Energie. Use these tools to explain and analyse "
        "recommendations: why a customer got an action, which hard rules blocked alternatives, "
        "the expected CLV gain, compliance and impact. You cannot approve or send anything; "
        "marketing approves in the cockpit. Customer-facing texts are German."
    ),
)

_client: httpx.Client | None = None


def set_client(client: httpx.Client) -> None:
    """Inject an HTTP client (tests pass a FastAPI TestClient)."""
    global _client
    _client = client


def _get(path: str, **params):
    global _client
    if _client is None:
        _client = httpx.Client(base_url=API_URL, timeout=120)
    r = _client.get(path, params={k: v for k, v in params.items() if v is not None})
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict):
    global _client
    if _client is None:
        _client = httpx.Client(base_url=API_URL, timeout=120)
    r = _client.post(path, json=body)
    r.raise_for_status()
    return r.json()


_ROW_FIELDS = ("customer_id", "segment", "status", "offer_id", "offer_name", "action_type", "channel",
               "tone", "p_accept", "dclv", "reason", "content_approved")


@mcp.tool()
def recommend(customer_ids: list[str]) -> dict:
    """Current recommendation for the given customers (IDs like MG-100009): offer, channel,
    Du/Sie tone, expected CLV gain (dclv, EUR), acceptance probability, one-line reason and
    review status (proposed, approved, rejected, held_out = control group, no_action)."""
    rows = _post("/api/recommend", {"customer_ids": customer_ids})
    return {"decisions": [{k: r.get(k) for k in _ROW_FIELDS} for r in rows]}


@mcp.tool()
def list_actions(status: str = "proposed", offer_id: str | None = None, channel: str | None = None,
                 segment: str | None = None, limit: int = 20) -> dict:
    """Top recommended actions by expected CLV gain. Filters: status (proposed, approved,
    rejected, held_out, no_action), offer_id (e.g. P-EN-03), channel (newsletter_email, email,
    sms, outbound_call, portal_popup, app_push), segment (High value, Mid value, Low value,
    New customer)."""
    data = _get("/api/decisions", status=status, offer=offer_id, channel=channel, segment=segment,
                limit=max(1, min(limit, 100)))
    return {"total": data["total"], "items": [{k: r.get(k) for k in _ROW_FIELDS} for r in data["items"]]}


@mcp.tool()
def customer_360(customer_id: str) -> dict:
    """Customer status: segment, CLV, churn risk and drivers, exclusion flags, consents,
    trigger signals, contracts, recent service cases and the recommended action."""
    c = _get(f"/api/customers/{customer_id}")
    d = c["decision"]
    return {
        "customer_id": c["customer_id"], "segment": c["segment"], "city": c["city"], "since": c["since"],
        "tone": c["tone"], "tone_evidence": c["tone_evidence"], "profiling_allowed": c["profiling_allowed"],
        "value": c["value"], "flags": c["flags"], "consents": c["consents"], "signals": c["signals"],
        "contracts": c["contracts"], "tickets": c["tickets"][:3],
        "recommended_action": {k: d.get(k) for k in _ROW_FIELDS},
    }


@mcp.tool()
def explain_decision(customer_id: str) -> dict:
    """Why the engine chose this action: reason, dCLV breakdown (p_accept, CLV now / if
    accepted / if rejected, contact cost), rule trace, ranked alternatives, every blocked
    offer with the rule that blocked it, logged policy and review history."""
    c = _get(f"/api/customers/{customer_id}")
    d = c["decision"]
    return {
        "customer_id": customer_id,
        "action": {k: d.get(k) for k in _ROW_FIELDS},
        "formula": "dCLV = p*(CLV_accept - CLV_now) + (1-p)*(CLV_reject - CLV_now) - contact_cost",
        "scores": {k: d.get(k) for k in ("p_accept", "p_accept_base", "clv_now", "clv_accept", "clv_reject",
                                         "contact_cost", "dclv")},
        "changed_by_marketing": d.get("original"),
        "rule_trace": d.get("rule_trace"),
        "alternatives": d.get("alternatives"),
        "blocked_offers": [
            {"offer": x["offer_name"], "rule": x["blocked_by"], "detail": x["blocked_detail"], "dclv": x["dclv"]}
            for x in c["candidates"] if not x["eligible"]
        ],
        "policy": d.get("inputs", {}).get("policy"),
        "review_history": d.get("history"),
        "config_version": d.get("config_version"),
    }


@mcp.tool()
def compliance_audit() -> dict:
    """Independent re-check of every current decision against the hard rules (consent,
    exclusions, profiling consent, no negative value, reasons, payment data, human approval,
    approved content), recomputed from raw data. 'violations' must be 0."""
    return _get("/api/audit")


@mcp.tool()
def impact_report() -> dict:
    """Value of this engine vs. the legacy rule engine for the same customers with the same
    dCLV formula, plus an audit of the legacy actions."""
    return _get("/api/impact")


@mcp.tool()
def learning_report() -> dict:
    """Causal evidence (uplift vs. control group with confidence interval), the A/B sample
    size plan, and the logged policy (solver, exploration, propensity)."""
    data = _get("/api/learn")
    data.pop("learning_curve", None)
    return data


@mcp.tool()
def get_config() -> dict:
    """Current steering settings (offers, priorities, channels, capacities, tone approval,
    contact limit, control group share, solver, exploration) and their change history."""
    return _get("/api/config")


if __name__ == "__main__":
    mcp.run()
