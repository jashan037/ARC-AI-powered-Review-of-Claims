"""The claims team's report PDF: the numbers are the engine's, it is reproducible, it carries nothing from the chat, and it downloads with the right headers.

The text is read back out of the PDF with pdftotext when poppler is installed (the tool the spec names) and with pypdf otherwise, so the
assertions hold either way; the pdftotext and pdfinfo checks below skip themselves when poppler is missing.
"""
import io
import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import intake, main, report
from app.config import ROOT
from app.tools import claims_engine as E
from app.tools.fmt import inr

client = TestClient(main.app, raise_server_exceptions=False)
EXPECTED = json.load(open(ROOT / "demo" / "samples" / "expected_outcomes.json", encoding="utf-8"))
COMMON = EXPECTED["common"]
PDFTOTEXT = shutil.which("pdftotext")
PDFINFO = shutil.which("pdfinfo")


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2026-09-21")


def session(which="on_time"):
    s = {"id": which, "history": [], "claim": None}
    for name, data in intake.sample_files(which):
        intake.store(s, name, intake.process_file(name, data))
    assert intake.build(s)["status"] == "ready"
    return s


def pypdf_pages(pdf: bytes) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]


def text_of(pdf: bytes, tmp_path) -> str:
    """The whole PDF as text: pdftotext when it is installed, pypdf otherwise."""
    if PDFTOTEXT:
        f = tmp_path / "r.pdf"
        f.write_bytes(pdf)
        return subprocess.run([PDFTOTEXT, "-layout", str(f), "-"], capture_output=True, text=True, check=True, timeout=30).stdout
    return "\n".join(pypdf_pages(pdf))


def numbers(text: str) -> set[int]:
    return {int(m.replace(",", "")) for m in re.findall(r"₹\s?([\d,]+)", text)}


# ---------------------------------------------------------------- the figures are the engine's
@pytest.mark.parametrize("which", ["on_time", "late_filing", "expired"])
def test_every_amount_in_the_pdf_is_an_amount_the_engine_produced(which, tmp_path):
    s = session(which)
    res = E.assess(s["claim"])
    text = text_of(report.report_pdf(s), tmp_path)
    allowed = {int(round(l["billed"])) for l in res["bill"]["lines"]} | {int(round(l["payable"])) for l in res["bill"]["lines"]}
    a = res["amounts"]
    allowed |= {int(round(v)) for v in (a["gross_billed"], a["admissible_total"], a["held_pending"], a["estimated_payable_if_docs_supplied"],
                                       a["payable_confirmed_now"], *a["deductions"].values())}
    allowed |= {int(round(v)) for v in (res["bill"]["room_limit_per_day"] or 0, res["bill"]["room_rate_per_day"] or 0)}
    sched = s["documents"]["policy_schedule"]["fields"]
    allowed |= {int(s["claim"]["claimed_amount"]), int(s["claim"]["base_si_lakh"] * 100000), int(s["claim"]["sum_insured_available"]), int(sched["bonus"]), 0}
    allowed |= {1, 100000}          # "KYC form (claims above ₹1 lakh)": the checklist label, not an amount of this claim
    assert numbers(text) <= allowed, (which, sorted(numbers(text) - allowed))


def test_the_headline_figures_are_the_hand_derived_ones(tmp_path):
    text = text_of(report.report_pdf(session("on_time")), tmp_path)
    for value in (COMMON["hospital_bill_total"], COMMON["estimated_payment_if_in_force"], COMMON["counted_now_if_in_force"],
                  COMMON["held_until_the_prescription_arrives"], COMMON["room_reduction"], COMMON["associated_reduction"], COMMON["non_medical_total"]):
        assert inr(value) in text.replace("−", "-"), (value, inr(value))


def test_the_rupee_sign_renders_and_is_not_a_box(tmp_path):
    text = text_of(report.report_pdf(session("on_time")), tmp_path)
    assert "₹1,84,500" in text and "�" not in text


# ---------------------------------------------------------------- reproducible, and nothing from the chat
def test_the_same_documents_give_the_same_bytes_apart_from_the_timestamp():
    import datetime as dt
    when = dt.datetime(2026, 9, 21, 10, 30, tzinfo=report.IST)
    a, b = report.report_pdf(session(), now=when), report.report_pdf(session(), now=when)
    assert a == b and len(a) < 300 * 1024
    later = report.report_pdf(session(), now=when + dt.timedelta(hours=1))
    assert later != a                                    # only the timestamp differs
    assert report.report_id(session()) == report.report_id(session())


def test_the_report_id_is_the_hash_of_the_facts_and_changes_with_them():
    import hashlib
    s = session()
    blob = json.dumps(report.canonical_facts(s), sort_keys=True, ensure_ascii=False, default=str).encode()
    assert report.report_id(s) == hashlib.sha256(blob).hexdigest()[:8] and len(report.report_id(s)) == 8
    other = session("expired")
    assert report.report_id(other) != report.report_id(s)


def test_no_chat_text_and_no_what_if_figure_reaches_the_report(tmp_path):
    s = session()
    s["history"] = [dict(user="what if the room rent was 5000 a day?", reply="Your estimated payment would rise to ₹1,60,000, with ₹1,39,500 counted so far.")]
    text = text_of(report.report_pdf(s), tmp_path)
    for forbidden in ("1,60,000", "1,39,500", "what if", "counted so far,", "Your estimated payment would"):
        assert forbidden not in text, forbidden
    assert "estimates" in text.lower() and "Not a claim decision" in text


def test_the_report_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(main, "get_agent", lambda: (_ for _ in ()).throw(AssertionError("the model must not be called")))
    assert report.report_pdf(session())[:4] == b"%PDF"


# ---------------------------------------------------------------- shape: one page 1, at most four pages
@pytest.mark.parametrize("which", ["on_time", "late_filing", "expired"])
def test_page_one_holds_the_whole_summary_and_the_report_is_at_most_four_pages(which):
    pages = pypdf_pages(report.report_pdf(session(which)))
    assert 1 <= len(pages) <= report.MAX_PAGES, len(pages)
    first = pages[0]
    for heading in ("Claim Review Report", "At a glance", "Readiness checks", "Money summary", "Needs a person's judgement", "Suggested next steps for the reviewer"):
        assert heading in first, (which, heading)
    assert "Decide the claim" in first                                  # the last numbered step is still on page 1
    assert "How the amount was worked out" not in first                 # and the detail starts on the next page
    assert "How the amount was worked out" in pages[1]
    for n, page in enumerate(pages, 1):
        assert f"Page {n} of {len(pages)}" in page and report.report_id(session(which)) in page


@pytest.mark.parametrize("which,heading", [(w, h) for w in ["on_time", "expired"] for h in
                                           ["How the amount was worked out", "Document register", "Where each key fact came from", "Policy provisions applied", "Basis and limits"]])
def test_the_later_pages_carry_every_section(which, heading):
    assert heading in "\n".join(pypdf_pages(report.report_pdf(session(which))))


def test_the_expired_set_leads_with_the_policy_not_being_in_force():
    first = pypdf_pages(report.report_pdf(session("expired")))[0]
    assert "Not met" in first and "policy period 15 Mar 2025 to 14 Mar 2026" in first.replace("\n", " ")
    assert "Nothing appears payable" in first
    assert "renewal" in first.lower()


def test_states_are_words_not_colours():
    first = pypdf_pages(report.report_pdf(session("on_time")))[0]
    assert "Results are words, not colours" in first.replace("\n", " ")
    assert "Pass" in first and "Missing" in first


def test_document_counts_have_one_source_of_truth():
    s = session("on_time")
    counts = intake.document_counts(s["documents"])
    assert (counts["received"], counts["expected"], counts["partial"]) == (9, 10, 1)
    assert f"{counts['received']} of {counts['expected']} received" in pypdf_pages(report.report_pdf(s))[0].replace("\n", " ")


def test_where_each_fact_came_from_names_the_file_and_the_page():
    pages = pypdf_pages(report.report_pdf(session("on_time")))
    text = "\n".join(pages).replace("\n", " ")
    assert "policy_schedule.pdf" in text and "hospital_bill.pdf" in text and "pharmacy_bills.pdf" in text
    assert intake.field_pages({"policy_number": "SYN-2805-0000-0001"}, ["nothing here", "SYN-2805-0000-0001 is on page two"])["policy_number"] == 2


def test_the_provisions_table_names_clauses_with_their_page():
    rows = report.provisions(E.assess(session("on_time")["claim"]))
    assert ("B.1.1.1 Note iii", "Proportionate deduction on room rent", "12") in rows
    assert all(page.isdigit() for _, _, page in rows), rows


# ---------------------------------------------------------------- the endpoint
def ready_session() -> str:
    sid = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{sid}/documents/sample")
    assert client.post(f"/sessions/{sid}/intake").json()["status"] == "ready"
    return sid


def test_the_endpoint_serves_the_pdf_with_download_headers():
    sid = ready_session()
    r = client.get(f"/sessions/{sid}/report.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="ARC-claim-report-CLM-20260910-0001.pdf"'
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"


def test_the_endpoint_is_not_available_before_the_claim_is_ready():
    sid = client.post("/sessions").json()["session_id"]
    assert client.get(f"/sessions/{sid}/report.pdf").status_code == 404
    assert client.get("/sessions/nosuchsession/report.pdf").status_code == 404


def test_deleting_the_session_takes_the_report_with_it():
    sid = ready_session()
    assert client.delete(f"/sessions/{sid}").json() == {"deleted": True}
    assert client.get(f"/sessions/{sid}/report.pdf").status_code == 404


# ---------------------------------------------------------------- poppler checks (skipped when poppler is not installed)
@pytest.mark.skipif(not PDFINFO, reason="poppler (pdfinfo) is not installed")
def test_the_pdf_opens_in_a_real_pdf_tool(tmp_path):
    f = tmp_path / "r.pdf"
    f.write_bytes(report.report_pdf(session()))
    out = subprocess.run([PDFINFO, str(f)], capture_output=True, text=True, check=True, timeout=30).stdout
    assert "Pages:" in out and "595.276 x 841.89" in out                    # A4 in points
    assert re.search(r"Pages:\s+[1-4]\b", out), out


@pytest.mark.skipif(not PDFTOTEXT, reason="poppler (pdftotext) is not installed")
def test_pdftotext_reads_the_same_figures(tmp_path):
    text = text_of(report.report_pdf(session()), tmp_path)
    assert inr(COMMON["estimated_payment_if_in_force"]) in text and inr(COMMON["counted_now_if_in_force"]) in text
