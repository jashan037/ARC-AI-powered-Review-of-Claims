"""Compact answers: summary_markdown and sections next to an answer_markdown that never changes."""
import json
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, settings
from app.rendering.compact import claim_reasons, one_line, useful_caveats, what_if_words
from app.rendering.render import _val, render_claim_assessment, short_label
from app.retrieval.azure_search import get_retriever
from app.tools import claims_engine as E
from app.tools.registry import TurnContext, call_tool, render_final

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))
R = get_retriever()
STATUSES = {"ok", "warning", "problem", "info"}
OFFICER = "The officer decides."


def claim_render(sid, what_if=None):
    ctx = TurnContext(session={"claim": SAMPLES[sid]["claim"], "uin": settings.default_uin, "history": []}, retriever=R)
    rid = call_tool("assess_claim", {"what_if": what_if} if what_if else {}, ctx)["result_id"]
    assert call_tool("final_answer", dict(answer_type="claim_assessment", headline="x", result_id=rid), ctx) == {"status": "accepted"}
    return render_final(ctx.final, ctx), ctx.results[rid]["data"]


def qa_render(final, ctx=None):
    ctx = ctx or TurnContext(session={"uin": settings.default_uin, "history": []}, retriever=R)
    ctx.length_caps_rejected = True     # these tests feed the renderer longer content than a live model is allowed; the caps have their own tests
    assert call_tool("final_answer", final, ctx) == {"status": "accepted"}
    return render_final(ctx.final, ctx)


def key(ctx, ref):
    return call_tool("get_clause", {"clause_ref": ref}, ctx)["clauses"][0]["chunk_key"]


# ---------------------------------------------------------------- answer_markdown is exactly as it was
def test_answer_markdown_is_byte_for_byte_unchanged(tmp_path):
    """The 19 golden answers were saved before the compact fields existed. Any change to the full answer must be a deliberate update of tests/golden."""
    for script in ("render_samples.py", "render_examples.py"):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev" / script), str(tmp_path)], capture_output=True, text=True, cwd=ROOT, timeout=120)
        assert p.returncode == 0, p.stderr
    golden = {f.name: f.read_text(encoding="utf-8") for f in (ROOT / "tests" / "golden" / "answers").glob("*.md")}
    assert len(golden) == 19
    for name, text in golden.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text, name


# ---------------------------------------------------------------- claim assessment
def test_tc07_summary_has_the_three_figures_reasons_next_steps_and_the_officer_line():
    r, _ = claim_render("TC07")
    s = r.summary_markdown
    assert "SYN-CLM-007" in s and "Rohan Verma" in s and "Optima Lite" in s and "appendectomy" in s
    assert "Likely eligible — pending documents" in s
    assert "| Estimated payment | Confirmed today | Held for documents |" in s and "**₹1,22,125** | **₹1,01,625** | **₹20,500**" in s
    reasons = re.findall(r"^\d\. (.+)$", s, re.M)
    assert len(reasons) == 3
    assert "Room rent above the plan limit: −₹49,875" in reasons[0] and "Prescription missing: ₹20,500 on hold" in reasons[1] and "12 non-medical items (Annexure B): −₹12,500" in reasons[2]
    assert "Request the prescription" in s and OFFICER in s
    assert len(s.splitlines()) <= 20 < len(r.markdown.splitlines())                  # the point of the exercise: short next to the full answer


def test_reasons_are_ranked_by_rupee_impact_and_capped_at_three():
    _, res = claim_render("TC08")                                                   # room rent, an aggregate deductible and non-medical items
    impacts = [amount for amount, _ in claim_reasons(res)]
    assert impacts == sorted(impacts, reverse=True) and len(impacts) == 3
    assert "more in the details" not in render_claim_assessment(res, R).summary_markdown
    four = E.assess(dict(SAMPLES["TC08"]["claim"], copay_percent=10))                # a co-payment makes a fourth reason
    assert len(claim_reasons(four)) == 4
    s = render_claim_assessment(four, R).summary_markdown
    assert len(re.findall(r"^\d\. ", s, re.M)) == 3 and "+ 1 more in the details" in s and "Co-payment" not in s.split("**Main reasons**")[1].split("more in")[0]


def test_tc07_sections():
    r, _ = claim_render("TC07")
    ids = [x["id"] for x in r.sections]
    assert ids == ["coverage", "waiting", "room", "nonpayable", "documents", "estimate", "evidence"]
    by = {x["id"]: x for x in r.sections}
    assert by["nonpayable"]["title"] == "Non-payable items (12)" and by["documents"]["title"] == "Documents (8 of 9 complete)"
    assert by["room"]["title"] == "Room rent working" and "62.5%" in by["room"]["markdown"] and by["estimate"]["markdown"].startswith("```text")
    assert "₹1,22,125" in by["estimate"]["markdown"] and "Gloves" in by["nonpayable"]["markdown"] or "gloves" in by["nonpayable"]["markdown"].lower()
    assert by["evidence"]["title"].startswith("Evidence (") and "Policy B.1.1.1 Note iii" in by["evidence"]["markdown"]
    for x in r.sections:
        assert set(x) == {"id", "title", "status", "markdown"} and x["status"] in STATUSES and x["markdown"].strip() and not x["markdown"].lstrip().startswith("###")
    assert len(set(ids)) == len(ids)


def test_a_not_payable_claim_says_so_and_why_in_the_summary():
    r, _ = claim_render("TC02")
    s = r.summary_markdown
    assert "Likely not payable" in s and "**Not payable**" in s and "30-day waiting period" in s and "14 days" in s
    assert "| Estimated payment" not in s and "Main reasons" not in s and OFFICER in s
    assert [x["id"] for x in r.sections] == ["coverage", "waiting", "bill_review", "estimate", "evidence"]


@pytest.mark.parametrize("sid", sorted(SAMPLES))
def test_red_flags_are_never_hidden_in_the_summary(sid):
    r, res = claim_render(sid)
    s = r.summary_markdown
    assert OFFICER in s and s.count(OFFICER) == 1
    if res["recommendation"] == "likely_not_payable":
        assert "Likely not payable" in s and "**Not payable**" in s
        for k in res["checks"]:
            if k["status"] == "violated":
                assert k["name"] in s and k["detail"] in s
    if res["recommendation"] == "needs_human_review":
        assert "Needs human review" in s
    for k in res["checks"]:
        if k["status"] == "needs_review":
            assert k["code"] in s and k["detail"] in s
    for i in res["inconsistencies"]:
        assert i["field"].replace("_", " ") in s and _val(i["discharge_summary"]) in s and _val(i["bill"]) in s


def test_document_conflicts_and_review_points_get_their_own_sections_too():
    r, res = claim_render("TC10")
    assert res["inconsistencies"] and r.sections[0]["id"] == "conflicts" and r.sections[0]["status"] == "problem"
    r9, res9 = claim_render("TC09")
    assert any(k["status"] == "needs_review" for k in res9["checks"]) and any(x["id"] == "review" for x in r9.sections)


# ---------------------------------------------------------------- what-if
def test_a_what_if_shows_before_and_after_computed_by_the_engine():
    r, res = claim_render("TC07", {"room_rate_per_day": 5000})
    base = E.assess(SAMPLES["TC07"]["claim"])["amounts"]["estimated_payable_if_docs_supplied"]
    assert res["baseline"]["estimated"] == base == 122125
    s = r.summary_markdown
    assert "Estimated payment ₹1,22,125 → ₹1,72,000" in s and "confirmed today ₹1,01,625 → ₹1,51,500" in s
    assert "Room rent per day = ₹5,000" in s and "room rate per day = 5000" not in s and "What-if scenario, not the claim as submitted" in s
    assert "room rate per day = 5000" in r.markdown                                 # the full answer keeps its original banner


def test_what_if_that_changes_the_recommendation_says_so():
    r, res = claim_render("TC07", {"documents": {"pharmacy_bills_prescription": {"present": True, "complete": True}}})
    assert res["baseline"]["recommendation"] == "likely_eligible_pending_documents" and res["recommendation"] == "likely_eligible"
    assert "Likely eligible" in r.summary_markdown and "Documents = updated" in r.summary_markdown and "→" in r.summary_markdown


def test_no_baseline_without_a_what_if():
    _, res = claim_render("TC07")
    assert "baseline" not in res and "→" not in claim_render("TC07")[0].summary_markdown


def test_what_if_words_use_plain_language_and_currency():
    assert what_if_words({"room_rate_per_day": 5000}) == "Room rent per day = ₹5,000"
    assert what_if_words({"protect_benefit_opted": True, "copay_percent": 10}) == "Protect Benefit opted = yes, Co-payment = 10%"
    assert what_if_words({"base_si_lakh": 7.5}) == "Base sum insured = ₹7,50,000"
    assert what_if_words({"aggregate_deductible_remaining": 25000}) == "Aggregate deductible remaining = ₹25,000"


# ---------------------------------------------------------------- deduction, documents, waiting
def test_deduction_summary_shows_the_formula_steps_and_keeps_evidence_in_a_section():
    ctx = TurnContext(session={"claim": SAMPLES["TC07"]["claim"], "uin": settings.default_uin, "history": []}, retriever=R)
    rid = call_tool("assess_claim", {}, ctx)["result_id"]
    r = qa_render(dict(answer_type="deduction_explanation", headline="x", result_id=rid, focus="all"), ctx)
    s = r.summary_markdown
    assert [f"{i}. **" in s for i in range(1, 7)] == [True] * 6 and "62.5%" in s and "₹12,000" in s and "₹37,875" in s
    assert "**Result** — Estimated insurer payment: **₹1,22,125**" in s and OFFICER in s and "Evidence" not in s
    assert [x["id"] for x in r.sections] == ["nonmedical", "evidence"] and r.sections[0]["title"] == "Non-medical items (12)" and "| Item |" in r.sections[0]["markdown"]


def test_documents_summary_lists_only_what_is_missing():
    ctx = TurnContext(session={"claim": SAMPLES["TC07"]["claim"], "uin": settings.default_uin, "history": []}, retriever=R)
    rid = call_tool("assess_claim", {}, ctx)["result_id"]
    r = qa_render(dict(answer_type="documents_answer", headline="One document is missing.", result_id=rid, next_steps=["Ask for the prescription."]), ctx)
    s = r.summary_markdown
    assert "**8 of 9 documents complete.**" in s and "Pharmacy bills with prescription — prescription missing" in s and "Claim form" not in s and "✅" not in s
    assert "Ask for the prescription." in s and OFFICER in s
    full = next(x for x in r.sections if x["id"] == "checklist")
    assert full["title"] == "Full checklist (8 of 9 complete)" and full["markdown"].count("✅") == 8 and "Claim form" in full["markdown"]


def test_documents_summary_when_everything_is_complete():
    ctx = TurnContext(session={"claim": SAMPLES["TC01"]["claim"], "uin": settings.default_uin, "history": []}, retriever=R)
    rid = call_tool("assess_claim", {}, ctx)["result_id"]
    s = qa_render(dict(answer_type="documents_answer", headline="Nothing is missing.", result_id=rid), ctx).summary_markdown
    assert "**All 9 documents are complete.**" in s and "⚠️" not in s and "❌" not in s


def test_waiting_summary_is_the_headline_and_the_compact_table():
    ctx = TurnContext(session={"uin": settings.default_uin, "history": []}, retriever=R)
    rid = call_tool("check_waiting_period", dict(first_policy_inception="2025-03-01", admission_date="2026-07-15", diagnosis="cataract"), ctx)["result_id"]
    r = qa_render(dict(answer_type="waiting_period_answer", headline="Not served yet.", result_id=rid, next_steps=["Check for portability credits."],
                       caveats=["Answer based solely on the policy wording."]), ctx)
    s = r.summary_markdown
    assert s.startswith("Not served yet.") and "| Waiting period | Applies? |" in s and "1 Mar 2027" in s and "16 months" in s and OFFICER in s
    assert "Check for portability" not in s and "Evidence" not in s
    assert [x["id"] for x in r.sections] == ["next_steps", "evidence"]                # the boilerplate caveat is dropped, the next step is on demand


# ---------------------------------------------------------------- coverage and definition answers
def _points(n, ctx, statuses=None):
    k = key(ctx, "C.1.b")
    return [dict(label=f"Point {i}", status=(statuses or {}).get(i, "info"), detail=f"Detail of point {i}.", citations=[k]) for i in range(1, n + 1)]


def test_coverage_summary_has_verdict_headline_three_one_line_points_and_details_on_demand():
    ctx = TurnContext(session={"uin": settings.default_uin, "history": []}, retriever=R)
    r = qa_render(dict(answer_type="coverage_answer", verdict="covered_with_conditions", headline="Covered. Subject to a waiting period.", points=_points(5, ctx),
                       next_steps=["Check the inception date."], caveats=["Answer limited to the policy wording.", "The Schedule may modify this."], citations=[key(ctx, "C.1.b")]), ctx)
    s = r.summary_markdown
    assert s.startswith("**🟠 Covered, with conditions**") and "Covered. Subject to a waiting period." in s
    assert len(re.findall(r"^- .* \*\*Point \d\*\*", s, re.M)) == 3 and "Point 4" not in s and OFFICER in s
    ids = [x["id"] for x in r.sections]
    assert ids == ["points", "next_steps", "notes", "evidence"] and next(x for x in r.sections if x["id"] == "points")["title"] == "All points (5)"
    assert "Point 5" in r.sections[0]["markdown"] and "Policy C.1.b" in r.sections[0]["markdown"]
    assert "The Schedule may modify this." in r.sections[2]["markdown"] and "limited to the policy wording" not in r.sections[2]["markdown"]


def test_a_problem_point_is_never_left_out_of_the_top_three():
    ctx = TurnContext(session={"uin": settings.default_uin, "history": []}, retriever=R)
    r = qa_render(dict(answer_type="coverage_answer", verdict="not_covered", headline="Not covered.", points=_points(6, ctx, {5: "problem"}), citations=[key(ctx, "C.1.b")]), ctx)
    s = r.summary_markdown
    assert "🔴 **Point 5**" in s and len(re.findall(r"^- ", s, re.M)) == 3 and "**🔴 Not covered**" in s


def test_long_points_are_one_line_in_the_summary_and_complete_in_the_details():
    ctx = TurnContext(session={"uin": settings.default_uin, "history": []}, retriever=R)
    long = "word " * 80
    r = qa_render(dict(answer_type="definition_answer", headline="Meaning.", points=[dict(label="L", status="info", detail=long, citations=[key(ctx, "C.1.b")])],
                       citations=[key(ctx, "C.1.b")]), ctx)
    line = next(l for l in r.summary_markdown.splitlines() if "**L**" in l)
    assert line.endswith("…") and len(line) < 200 and long.strip() in next(x for x in r.sections if x["id"] == "points")["markdown"]
    assert r.summary_markdown.startswith("**📖 What the policy says**")


def test_insufficient_information_keeps_where_to_look_in_the_summary():
    r = qa_render(dict(answer_type="insufficient_information", headline="The wording does not say.", next_steps=["Check the annual report."]))
    assert "⚪ Insufficient information" in r.summary_markdown and "Check the annual report." in r.summary_markdown and OFFICER in r.summary_markdown


def test_general_answer_is_just_the_headline():
    r = qa_render(dict(answer_type="general_answer", headline="Hello. Ask me about a claim."))
    assert r.summary_markdown.strip() == "Hello. Ask me about a claim." and r.sections == []


# ---------------------------------------------------------------- notes that only restate the disclaimer are dropped
@pytest.mark.parametrize("note", ["This answer is based solely on the policy wording.", "Final decision rests with the claims officer.", "AI-assisted estimate, not a decision.",
                                  "Conclusion is based solely on the policy wording and the waiting-period check; final decision depends on document verification and underwriting.",
                                  "Final payment subject to receipt of all documents and final audit under policy terms."])
def test_disclaimer_only_notes_are_dropped(note):
    assert useful_caveats([note]) == []


def test_useful_parts_of_a_note_survive():
    assert useful_caveats(["Answer based solely on the policy wording; endorsements or rider benefits may modify applicability."]) == ["endorsements or rider benefits may modify applicability."]
    assert useful_caveats(["Exact applicability depends on the insured's inception date."]) == ["Exact applicability depends on the insured's inception date."]
    assert useful_caveats(None) == [] and useful_caveats([]) == []


def test_one_line_truncates_at_a_word():
    assert one_line("a b c", 10) == "a b c" and one_line("alpha beta gamma delta", 12) == "alpha beta…"


# ---------------------------------------------------------------- one clause label format everywhere
def test_clause_labels_use_one_spelling_and_chips_are_short():
    r, _ = claim_render("TC07")
    assert all("A1.2" not in c["citation"] and "A1.1" not in c["citation"] for c in r.citations)
    assert any(c["citation"] == "Policy A.1.2 Def. 5, p.8" and c["label"] == "A.1.2 Def. 5 p.8" for c in r.citations)
    assert short_label("Policy C.1.b, p.28") == "C.1.b p.28" and short_label("Policy Annexure B, p.49") == "Annexure B p.49"
    assert "A1.2" not in json.dumps(r.sections) and "A1.1" not in json.dumps(r.sections)


# ---------------------------------------------------------------- the API
client = TestClient(__import__("app.main", fromlist=["app"]).app, raise_server_exceptions=False)


def test_chat_returns_summary_and_sections_next_to_the_unchanged_full_answer():
    sid = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{sid}/claim", json={"sample_id": "TC07"})
    body = client.post(f"/sessions/{sid}/chat", json={"message": "Please assess this claim"}).json()
    assert "## AI-Assisted Claim Assessment" in body["answer_markdown"] and "### Estimated Assessment" in body["answer_markdown"]
    assert "₹1,22,125" in body["summary_markdown"] and body["summary_markdown"] != body["answer_markdown"]
    assert [s["id"] for s in body["sections"]][0] == "coverage" and all(set(s) == {"id", "title", "status", "markdown"} for s in body["sections"])
    assert all("label" in c for c in body["citations"])


def test_assess_returns_summary_and_sections_too():
    body = client.post("/assess", json={"sample_id": "TC02"}).json()
    assert "**Not payable**" in body["summary_markdown"] and body["sections"] and "## AI-Assisted Claim Assessment" in body["answer_markdown"]


def test_a_failed_turn_still_has_a_summary():
    from types import SimpleNamespace as NS
    from app import main
    from app.agent.runner import AgentResult
    stub = AgentResult("## Please try again\n\nNothing was assessed.\n", "general_answer", status="timeout")
    orig = main.get_agent
    main.get_agent = lambda: NS(ask=lambda *a, **k: stub)
    try:
        sid = client.post("/sessions").json()["session_id"]
        body = client.post(f"/sessions/{sid}/chat", json={"message": "hi"}).json()
    finally:
        main.get_agent = orig
    assert body["summary_markdown"] == stub.markdown and body["sections"] == []
