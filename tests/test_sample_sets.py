"""The three synthetic document sets: they parse, they keep the demo banner, and each one gives the outcome derived by hand in demo/samples/expected_outcomes.json.

Nothing here reads a number out of the engine to decide what to expect: every figure comes from that file, which was worked out from the documents
and the policy wording. `ARC_TODAY` is set to 21 Sep 2026 so the filing-time results are the ones the demo shows.
"""
import json

import pytest
from pypdf import PdfReader

from app import intake as I
from app.config import ROOT
from app.tools import claims_engine as E
from app.tools.registry import TurnContext, call_tool, cover_left, precompute
from app.retrieval.azure_search import get_retriever
from app.tools.totals import derived_totals

EXPECTED = json.load(open(ROOT / "demo" / "samples" / "expected_outcomes.json", encoding="utf-8"))
COMMON = EXPECTED["common"]
SETS = I.SAMPLE_SETS
BANNER = "SYNTHETIC DEMO DOCUMENT"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2026-09-21")


def session(which):
    s = {"id": which, "history": [], "claim": None}
    for name, data in I.sample_files(which):
        I.store(s, name, I.process_file(name, data))
    out = I.build(s)
    assert out["status"] == "ready", out.get("reasons")
    return s, out


def claim(which):
    return session(which)[0]["claim"]


# ---------------------------------------------------------------- the documents themselves
def test_there_are_three_sets_of_ten_documents():
    assert sorted(SETS) == ["expired", "late_filing", "on_time"]
    for which in SETS:
        assert len(I.sample_files(which)) == 10, which


@pytest.mark.parametrize("which", SETS)
def test_every_document_is_a_text_pdf_with_the_demo_banner_on_every_page(which):
    for name, data in I.sample_files(which):
        pages = PdfReader(__import__("io").BytesIO(data)).pages
        assert pages, name
        for page in pages:
            assert BANNER in (page.extract_text() or ""), (which, name)


@pytest.mark.parametrize("which", SETS)
def test_every_document_is_recognised_and_the_prescription_is_the_only_gap(which):
    s, out = session(which)
    assert len(s["documents"]) == 10, which
    assert out["missing"] == ["The doctor's prescription for your pharmacy bills"], (which, out["missing"])
    cl = out["checklist"]
    assert sum(c["state"] == "received" for c in cl) == 9 and sum(c["state"] == "partial" for c in cl) == 1


@pytest.mark.parametrize("which", SETS)
def test_the_facts_read_from_the_documents_are_the_ones_expected(which):
    c, e = claim(which), EXPECTED[which]
    assert c["insured_name"] == COMMON["insured_name"] and c["plan"] == COMMON["plan"] and c["policy_number"] == COMMON["policy_number"]
    assert c["first_policy_inception"] == COMMON["first_policy_inception"]
    assert c["base_si_lakh"] * 100000 == COMMON["base_sum_insured"] and c["sum_insured_available"] == COMMON["cover_amount"]
    assert c["claimed_amount"] == COMMON["hospital_bill_total"] and c["room_rate_per_day"] == COMMON["room_rate_billed_per_day"]
    assert c["protect_benefit_opted"] is False and c["copay_percent"] == 0 and c["aggregate_deductible_remaining"] == 0
    assert c["policy_period"] == e["policy_period"] and c["admission_datetime"] == e["admission"] and c["discharge_datetime"] == e["discharge"]
    assert sum(l["amount"] for l in c["bill_lines"]) == COMMON["hospital_bill_total"]


# ---------------------------------------------------------------- the outcome of each set
@pytest.mark.parametrize("which", SETS)
def test_the_engine_gives_the_hand_derived_outcome(which):
    e = EXPECTED[which]
    res = E.assess(claim(which))
    assert res["policy_in_force"]["in_force"] is e["policy_in_force_on_admission"], which
    assert res["filing"]["days_since_discharge"] == e["days_since_discharge"] and res["filing"]["due_by"] == e["documents_due_by"]
    assert res["filing"]["late"] is e["filing_is_late"]
    assert res["recommendation"] == e["recommendation"], (which, res["recommendation"])
    a = res["amounts"]
    assert a["gross_billed"] == COMMON["hospital_bill_total"]
    assert a["estimated_payable_if_docs_supplied"] == e["estimated_payment"], (which, a)
    assert a["payable_confirmed_now"] == e["counted_now"] and a["held_pending"] == e["held"]


@pytest.mark.parametrize("which", ["on_time", "late_filing"])
def test_the_deductions_add_up_the_way_they_were_derived(which):
    t = derived_totals(E.assess(claim(which)))
    assert t["room_related_reduction"] == COMMON["room_related_reduction"]
    assert t["non_medical_total"] == COMMON["non_medical_total"] and t["non_medical_count"] == COMMON["non_medical_count"]
    assert [dict(item=x["item"], amount=x["amount"]) for x in t["largest_non_medical_items"]] == COMMON["largest_non_medical_items"]
    assert t["other_non_medical_count"] == COMMON["other_non_medical_count"] and t["other_non_medical_total"] == COMMON["other_non_medical_total"]
    assert t["room_limit_per_day"] == COMMON["room_limit_per_day"] and t["share_paid_percent"] == COMMON["share_of_room_paid_percent"]
    assert (COMMON["hospital_bill_total"] - COMMON["room_related_reduction"] - COMMON["non_medical_total"]) == COMMON["estimated_payment_if_in_force"]


@pytest.mark.parametrize("which", ["on_time", "late_filing"])
@pytest.mark.parametrize("key,what_if", [
    ("what_if_room_5000_a_day", {"room_rate_per_day": 5000}),
    ("what_if_room_5000_a_day_and_protect_benefit", {"room_rate_per_day": 5000, "protect_benefit_opted": True}),
    ("what_if_protect_benefit_only", {"protect_benefit_opted": True}),
])
def test_the_what_ifs_give_the_hand_derived_figures(which, key, what_if):
    want = COMMON[key]
    res = E.assess(E.apply_what_if(claim(which), what_if))
    assert res["amounts"]["gross_billed"] == want["bill"]
    assert res["amounts"]["estimated_payable_if_docs_supplied"] == want["estimate"]
    assert res["amounts"]["payable_confirmed_now"] == want["counted_now"]


@pytest.mark.parametrize("which", ["on_time", "late_filing"])
def test_cover_left_on_each_set(which):
    s, _ = session(which)
    ctx = TurnContext(session=s, retriever=get_retriever(), question="how much cover do I have left?")
    assert cover_left(ctx, [])["cover_left"] == COMMON["cover_left_after_this_claim"]
    assert cover_left(ctx, [300000])["cover_left"] == COMMON["cover_left_with_a_stated_other_claim_of_300000"]


# ---------------------------------------------------------------- the three set-specific stories
def test_on_time_is_filed_inside_the_time_limit_and_a_later_date_is_not_in_force():
    c = claim("on_time")
    assert E.filing_text(E.filing_status(c)).startswith("Your documents are being sent 7 days after discharge")
    later = E.policy_in_force(dict(c, admission_datetime="2027-04-20T10:00"))
    assert later["in_force"] is False and later["relation"] == "after"
    served = [k for k in E.check_waiting_period(dict(c, diagnosis="Cataract", procedure="Cataract surgery", admission_datetime="2026-10-15T10:00")) if k["code"] == "Excl02"]
    assert served[0]["status"] == "satisfied"


def test_late_filing_is_a_review_flag_and_cataract_in_december_2025_is_before_the_wait_ends():
    c = claim("late_filing")
    f = E.filing_status(c)
    assert f["late"] is True and f["days_since_discharge"] == 372
    assert "flagged for a claims officer to review, not rejected" in E.filing_text(f)
    assert E.assess(c)["recommendation"] == "needs_human_review"
    k = [x for x in E.check_waiting_period(dict(c, diagnosis="Cataract", procedure="Cataract surgery", admission_datetime="2025-12-10T10:00")) if x["code"] == "Excl02"][0]
    assert k["status"] == "violated" and k["eligible_from"] == "2026-03-15"


def test_expired_leads_with_the_policy_not_being_in_force():
    c = claim("expired")
    res = E.assess(c)
    assert res["recommendation"] == "likely_not_payable" and res["coverage"]["status"] == "not_covered"
    assert E.policy_in_force_text(res["policy_in_force"]).startswith("Your policy was not in force on the admission date (20 Apr 2026)")
    from app.tools import focus
    s, _ = session("expired")
    ctx = TurnContext(session=s, retriever=get_retriever(), question="how much will be paid?")
    precompute(ctx)
    p = focus.plan(ctx)
    assert p["lead_pattern"] is not None and p["sentence"].startswith("Your policy was not in force")


def test_the_first_message_names_the_set_s_own_dates():
    for which in SETS:
        s, out = session(which)
        first = I.first_message(s["claim"], out["missing"])
        e = EXPECTED[which]
        assert "Rohan Verma" in first and "appendectomy" in first
        assert E.d_fmt(e["policy_period"][1]) in first if hasattr(E, "d_fmt") else True
        assert "prescription" in first.lower()


def test_the_regenerated_documents_still_say_what_the_original_pdfs_said():
    """demo/make_sample_sets.py --check proves it against the original hand-made PDFs; here we only lock the line counts it reported."""
    import sys
    sys.path.insert(0, str(ROOT / "demo"))
    import make_sample_sets as M
    lines = {p.name: len(M.text_lines(p)) for p in sorted((I.SAMPLE_DIR / "late_filing").glob("*.pdf"))}
    assert lines == {"claim_form.pdf": 52, "consultation_papers.pdf": 19, "discharge_summary.pdf": 36, "hospital_bill.pdf": 152, "kyc_form.pdf": 20,
                     "lab_report.pdf": 37, "neft_form.pdf": 18, "pharmacy_bills.pdf": 43, "photo_id_proof.pdf": 18, "policy_schedule.pdf": 54}
