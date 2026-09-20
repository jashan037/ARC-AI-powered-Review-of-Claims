from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _session(sample):
    sid = client.post("/sessions").json()["session_id"]
    assert client.post(f"/sessions/{sid}/claim", json={"sample_id": sample}).json()["loaded"]
    return sid


def test_health_and_samples():
    assert client.get("/health").json()["status"] == "ok"
    assert len(client.get("/samples").json()) == 12


def test_assess_endpoint_is_deterministic_and_formatted():
    r = client.post("/assess", json={"sample_id": "TC07"}).json()
    md = r["answer_markdown"]
    assert r["recommendation"] == "likely_eligible_pending_documents"
    for must in ("## AI-Assisted Claim Assessment", "### Recommendation", "### Coverage", "### Waiting Period", "### Room Rent",
                 "### Non-payable Items", "### Missing Documents", "### Estimated Assessment", "₹1,22,125", "₹1,01,625"):
        assert must in md, must
    assert md.rstrip().endswith("The claims officer decides.")


def test_chat_assessment_then_followup_explanation():
    sid = _session("TC07")
    a = client.post(f"/sessions/{sid}/chat", json={"message": "Please assess this claim"}).json()
    assert a["answer_type"] == "claim_assessment"
    b = client.post(f"/sessions/{sid}/chat", json={"message": "Why was the room rent deducted?"}).json()
    assert b["answer_type"] == "deduction_explanation"
    assert "62.5%" in b["answer_markdown"] and "₹5,000 ÷ ₹8,000" in b["answer_markdown"] and "₹37,875" in b["answer_markdown"]


def test_what_if_changes_the_number():
    sid = _session("TC07")
    md = client.post(f"/sessions/{sid}/chat", json={"message": "Assess it, what if the room rent was 5,000 per day"}).json()["answer_markdown"]
    assert "Room-rent adjustment" not in md and "Within the plan limit" in md
    assert "₹1,72,000" in md          # 1,84,500 minus 12,500 non-medical, no proportionate deduction at the limit


def test_bad_inputs_are_rejected_cleanly():
    sid = client.post("/sessions").json()["session_id"]
    assert client.post(f"/sessions/{sid}/claim", json={"claim": {"claim_id": "x"}}).status_code == 422
    assert client.post("/sessions/nope/chat", json={"message": "hi"}).status_code == 404


def test_what_if_banner_is_shown():
    sid = _session("TC07")
    md = client.post(f"/sessions/{sid}/chat", json={"message": "Assess it, what if the room rent was 5,000 per day"}).json()["answer_markdown"]
    assert "What-if scenario, not the claim as submitted" in md
    md2 = client.post(f"/sessions/{sid}/chat", json={"message": "Assess this claim"}).json()["answer_markdown"]
    assert "What-if" not in md2
