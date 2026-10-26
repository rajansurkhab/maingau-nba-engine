"""API and human-in-the-loop workflow."""
import pytest
from fastapi.testclient import TestClient

import api.service as service_module
from api.main import app
from api.service import EngineService


@pytest.fixture()
def client(ds):
    service_module._service = EngineService(ds)
    yield TestClient(app)
    service_module._service = None


def first_proposal(client, **params):
    return client.get("/api/decisions", params={"status": "proposed", **params}).json()["items"][0]


def approve_content(client, offer_id, tone):
    r = client.post(f"/api/templates/{offer_id}:{tone}/review", json={"status": "approved", "actor": "qa"})
    assert r.status_code == 200


def test_summary_and_meta(client):
    s = client.get("/api/summary").json()
    assert s["kpis"]["customers"] == 750
    assert s["kpis"]["approved"] == 0
    assert {g["segment"] for g in s["segments"]} == {"High value", "Mid value", "Low value", "New customer"}
    meta = client.get("/api/meta").json()
    assert len(meta["offers"]) == 18 and len(meta["channels"]) == 6


def test_nothing_is_approved_without_approved_content(client):
    d = first_proposal(client)
    r = client.post(f"/api/decisions/{d['decision_id']}/review", json={"status": "approved", "actor": "qa"})
    assert r.status_code == 409
    approve_content(client, d["offer_id"], d["tone"])
    r = client.post(f"/api/decisions/{d['decision_id']}/review", json={"status": "approved", "actor": "qa"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert client.get("/api/summary").json()["kpis"]["approved"] == 1


def test_reject_needs_no_content_and_is_final(client):
    d = first_proposal(client)
    url = f"/api/decisions/{d['decision_id']}/review"
    assert client.post(url, json={"status": "rejected", "actor": "qa", "note": "not now"}).status_code == 200
    assert client.post(url, json={"status": "approved", "actor": "qa"}).status_code == 409


def test_control_group_and_no_action_cannot_be_approved(client):
    for status in ("held_out", "no_action"):
        d = client.get("/api/decisions", params={"status": status}).json()["items"][0]
        r = client.post(f"/api/decisions/{d['decision_id']}/review", json={"status": "approved", "actor": "qa"})
        assert r.status_code == 409


def test_override_only_with_ranked_alternatives(client):
    detail = None
    for item in client.get("/api/decisions", params={"status": "proposed"}).json()["items"]:
        detail = client.get(f"/api/decisions/{item['decision_id']}").json()
        if any(a["offer_id"] != detail["offer_id"] for a in detail["alternatives"]):
            break
    alt = next(a for a in detail["alternatives"] if a["offer_id"] != detail["offer_id"])
    url = f"/api/decisions/{detail['decision_id']}/review"
    bad = client.post(url, json={"status": "approved", "actor": "qa", "override": {"offer_id": "P-XX", "channel": "sms"}})
    assert bad.status_code in (400, 409)
    approve_content(client, alt["offer_id"], detail["tone"])
    r = client.post(url, json={"status": "approved", "actor": "qa", "override": {"offer_id": alt["offer_id"], "channel": alt["channel"]}})
    assert r.status_code == 200
    after = client.get(f"/api/decisions/{detail['decision_id']}").json()
    assert after["overridden"] and after["offer_id"] == alt["offer_id"]
    assert after["original"]["offer_name"] == detail["offer_name"]
    assert after["p_accept"] == pytest.approx(alt["p_accept"])


def test_bulk_review_reports_content_failures(client):
    ids = [d["decision_id"] for d in client.get("/api/decisions", params={"status": "proposed", "limit": 5}).json()["items"]]
    res = client.post("/api/decisions/review", json={"decision_ids": ids, "status": "approved", "actor": "qa"}).json()
    assert res["done"] == [] and set(res["failed"]) == set(ids)


def test_config_change_reruns_and_carries_over_reviews(client):
    d = first_proposal(client)
    approve_content(client, d["offer_id"], d["tone"])
    client.post(f"/api/decisions/{d['decision_id']}/review", json={"status": "approved", "actor": "qa"})
    other_offer = "P-MO-01" if d["offer_id"] != "P-MO-01" else "P-IN-01"
    r = client.put("/api/config", json={"actor": "qa", "changes": {"disabled_offers": [other_offer]}})
    assert r.status_code == 200
    assert r.json()["config"]["disabled_offers"] == [other_offer]
    rows = client.get("/api/decisions", params={"q": d["customer_id"]}).json()["items"]
    assert rows[0]["status"] == "approved"
    assert all(x["offer_id"] != other_offer for x in client.get("/api/decisions").json()["items"])
    log = client.get("/api/log/reviews").json()
    assert len(log["config_changes"]) == 1 and len(log["reviews"]) >= 2  # original + carried over


def test_config_rejects_unknown_fields(client):
    r = client.put("/api/config", json={"actor": "qa", "changes": {"horizon_years": 50}})
    assert r.status_code == 400


def test_customer_360_and_recommend(client):
    c = client.get("/api/customers/MG-100001").json()
    assert c["decision"]["channel"] == "portal_popup" and c["tone"] == "sie"
    assert c["decision"]["content"]["subject_en"] and c["decision"]["content"]["subject_de"]
    assert client.get("/api/customers/MG-999999").status_code == 404
    rec = client.post("/api/recommend", json={"customer_ids": ["MG-100001", "MG-100060"]}).json()
    assert [r["customer_id"] for r in rec] == ["MG-100001", "MG-100060"]


def test_decision_time_replay(client):
    assert client.put("/api/time", json={"time_key": "oct1-1800"}).status_code == 200
    c = client.get("/api/customers/MG-100009").json()
    assert c["decision"]["offer_id"] == "P-SV-01"
    assert client.put("/api/time", json={"time_key": "nope"}).status_code == 400


def test_log_download_is_jsonl(client):
    r = client.get("/api/log/download")
    assert r.status_code == 200
    assert len(r.text.strip().splitlines()) >= 750


def test_state_survives_a_restart(ds, tmp_path):
    """Approvals, content status and settings are persisted, so a demo never starts from zero."""
    path = tmp_path / "state.json"
    first = EngineService(ds, state_path=path)
    d = next(r for r in first.decisions(status="proposed"))
    first.review_template(f"{d['offer_id']}:{d['tone']}", "approved", "qa")
    first.review(d["decision_id"], "approved", "qa")
    first.update_config({"solver": "lp"}, "qa")

    second = EngineService(ds, state_path=path)
    assert second.cfg.solver == "lp"
    assert second.content_approved(d["offer_id"], d["tone"])
    after = next(r for r in second.decisions() if r["customer_id"] == d["customer_id"])
    assert after["status"] == "approved"


def test_new_endpoints(client):
    cap = client.get("/api/capacity").json()
    assert cap["lp_objective"] >= cap["greedy_objective"] - 1e-6
    learn = client.get("/api/learn").json()
    assert learn["pooled"]["control_n"] > 0 and learn["learning_curve"]["points"]
    audit = client.get("/api/audit").json()
    assert audit["violations"] == 0 and any(c["id"] == "content" for c in audit["checks"])
    impact = client.get("/api/impact").json()
    assert impact["comparison"]["engine_expected_gain"] > impact["comparison"]["legacy_expected_gain"]
    assert client.put("/api/config", json={"actor": "qa", "changes": {"exploration": "maybe"}}).status_code == 400
