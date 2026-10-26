"""MCP server: read-only tools over the API, called the way an MCP client calls them."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import api.service as service_module
from api.main import app
from api.service import EngineService
from mcp_server import server


@pytest.fixture(scope="module")
def mcp_client(ds):
    service_module._service = EngineService(ds)
    server.set_client(TestClient(app))
    yield server.mcp
    service_module._service = None
    server._client = None


def call(mcp, name, **arguments):
    result = asyncio.run(mcp.call_tool(name, arguments))
    assert not getattr(result, "isError", False), result
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


def test_tools_are_read_only(mcp_client):
    names = {t.name for t in asyncio.run(mcp_client.list_tools())}
    assert {"recommend", "customer_360", "explain_decision", "compliance_audit", "impact_report"} <= names
    assert not any(word in n for n in names for word in ("approve", "send", "set_", "review"))


def test_recommend_and_explain_slide9_case(mcp_client):
    rows = call(mcp_client, "recommend", customer_ids=["MG-100001"])["decisions"]
    assert rows[0]["channel"] == "portal_popup" and rows[0]["tone"] == "sie"
    why = call(mcp_client, "explain_decision", customer_id="MG-100001")
    assert why["rule_trace"] and why["scores"]["dclv"] > 0
    assert "dCLV" in why["formula"]


def test_compliance_audit_reports_zero(mcp_client):
    assert call(mcp_client, "compliance_audit")["violations"] == 0


def test_list_actions_and_customer_360(mcp_client):
    top = call(mcp_client, "list_actions", status="proposed", limit=3)
    assert len(top["items"]) == 3 and top["total"] > 3
    c = call(mcp_client, "customer_360", customer_id="MG-100004")
    assert c["flags"]["dunning"] and c["recommended_action"]["action_type"] in ("service", None)
