"""Claim intake from documents: recognising each file, reading the fields, building a claim the engine assesses exactly like the hand-made TC07."""
import io
import json

import pytest
from pypdf import PdfWriter

from app import intake as I
from app.config import ROOT, settings
from app.tools import claims_engine as E
from tests.pdfmaker import edit, make_pdf

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))
MANIFEST = json.load(open(ROOT / "demo" / "documents" / "manifest.json", encoding="utf-8"))
EXPECTED = json.load(open(ROOT / "demo" / "documents" / "expected_extraction.json", encoding="utf-8"))
FILES = dict(I.sample_files())


def session(**skip):
    """A session with the 10 sample documents stored, except the named files."""
    s = {"id": "t", "history": [], "claim": None}
    for name, data in FILES.items():
        if name not in skip.get("skip", ()):
            I.store(s, name, I.process_file(name, data))
    return s


def add(s, name, data):
    return I.store(s, name, I.process_file(name, data))


# ---------------------------------------------------------------- recognising the ten sample documents
def test_there_are_ten_sample_pdfs_and_the_manifest_lists_them():
    assert sorted(FILES) == sorted(f["file"] for f in MANIFEST["files"]) and len(FILES) == 10


@pytest.mark.parametrize("entry", MANIFEST["files"], ids=lambda e: e["file"])
def test_every_sample_document_is_recognised_as_the_type_the_manifest_says(entry):
    r = I.process_file(entry["file"], FILES[entry["file"]])
    assert r["status"] == "recognised" and r["type"] == entry["document_type"], r


def test_the_checklist_is_the_expected_documents_in_a_fixed_order():
    assert [d["id"] for d in I.EXPECTED] == ["policy_schedule", "claim_form", "photo_id_age_proof", "discharge_summary", "final_bill_receipts", "diagnostic_reports_bills",
                                             "previous_consultation_papers", "pharmacy_bills_prescription", "kyc", "neft_form"]
    engine_ids = {d["id"] for d in E.DOC_RULES}
    assert [d["id"] for d in I.EXPECTED if d["id"] != "policy_schedule"] and all(d["id"] in engine_ids for d in I.EXPECTED if d["id"] != "policy_schedule")   # the engine knows every one


# ---------------------------------------------------------------- reading the fields (against the ground truth that ships with the demo)
def test_fields_match_the_expected_extraction():
    s = session()
    F = lambda k: s["documents"][k]["fields"]   # noqa: E731
    ps, cf, ds, hb = EXPECTED["policy_schedule"], EXPECTED["claim_form"], EXPECTED["discharge_summary"], EXPECTED["hospital_bill"]
    assert F("policy_schedule")["policy_number"] == ps["policy_number"] and F("policy_schedule")["plan"] == ps["plan"] == "Optima Lite"
    assert F("policy_schedule")["base_si"] == ps["base_sum_insured"] and F("policy_schedule")["bonus"] == ps["cumulative_bonus"]
    assert F("policy_schedule")["first_inception"] == ps["first_policy_inception"] and F("policy_schedule")["policy_period"] == [ps["policy_period_start"], ps["policy_period_end"]]
    assert F("policy_schedule")["insured_name"] == ps["insured_name"] and F("policy_schedule")["aggregate_deductible"] == 0 and F("policy_schedule")["protect_benefit_opted"] is False
    assert F("policy_schedule")["policy_uin"] == "HDFHLIP25041V062425" and F("policy_schedule")["copay_percent"] == 0
    assert F("claim_form")["claimed_amount"] == cf["claimed_amount"] and F("claim_form")["diagnosis"] == cf["diagnosis"] and F("claim_form")["icd10"] == cf["icd10"]
    assert F("claim_form")["admission"] == "2025-09-10T14:30" and F("claim_form")["discharge"] == "2025-09-14T11:00"
    assert F("claim_form")["hospital"].startswith("Riverside") and F("claim_form")["hospital_network"] is True and F("claim_form")["is_accident"] is False
    assert F("claim_form")["procedure"] == "Laparoscopic appendectomy" and F("claim_form")["pre_existing"] is False
    assert F("discharge_summary")["procedure"] == ds["procedure"] and F("discharge_summary")["admission"][:10] == ds["admission_date"] and F("discharge_summary")["length_of_stay"] == 4
    bill = F("final_bill_receipts")
    assert bill["total"] == hb["total"] and len(bill["lines"]) == hb["line_count"] == 23 and bill["room_rate_per_day"] == hb["room_rate_per_day"] and bill["room_days"] == hb["room_days"]
    by_section = {}
    for l in bill["lines"]:
        by_section[l["section"]] = by_section.get(l["section"], 0) + l["amount"]
    assert list(by_section.values()) == [hb["department_totals"][k] for k in ("room_and_nursing", "professional_fees_and_ot", "pharmacy", "investigations", "miscellaneous")]
    assert F("pharmacy_bills_prescription")["total"] == 20500 and F("pharmacy_bills_prescription")["prescription_attached"] is False
    assert all(s["documents"][k]["fields"].get("patient_name") == "Rohan Verma" for k in ("photo_id_age_proof", "kyc", "neft_form", "diagnostic_reports_bills", "previous_consultation_papers"))


# ---------------------------------------------------------------- the claim it builds is the hand-made TC07
def test_the_sample_documents_produce_a_ready_claim_that_assesses_like_tc07():
    s = session()
    out = I.build(s)
    assert out["status"] == "ready" and out["reasons"] == [] and s["claim"] is out["claim"]
    res, ref = E.assess(out["claim"]), E.assess(SAMPLES["TC07"]["claim"])
    a = res["amounts"]
    assert (a["estimated_payable_if_docs_supplied"], a["payable_confirmed_now"], a["held_pending"]) == (122125, 101625, 20500)
    assert a["deductions"] == ref["amounts"]["deductions"] == {"room": 12000.0, "associated": 37875.0, "non_medical": 12500.0}
    assert res["recommendation"] == ref["recommendation"] == "likely_eligible_pending_documents"
    assert {d["id"]: d["status"] for d in res["documents"]["checklist"]} == {d["id"]: d["status"] for d in ref["documents"]["checklist"]}


def test_the_bill_lines_are_the_same_as_the_hand_made_ones():
    got = I.build(session())["claim"]["bill_lines"]
    ref = SAMPLES["TC07"]["claim"]["bill_lines"]
    assert [(l["description"], l["category"], l["amount"]) for l in got] == [(l["description"], l["category"], l["amount"]) for l in ref]
    nm = [l for l in got if l["category"] == "non_medical"]
    assert len(nm) == 12 and all(isinstance(l["annexure_b_no"], int) for l in nm)     # #9 vs #24 for "Attendant food charges" is a different but equally non-payable entry


def test_the_claim_carries_the_schedule_and_form_details():
    c = I.build(session())["claim"]
    ref = SAMPLES["TC07"]["claim"]
    for key in ("insured_name", "plan", "base_si_lakh", "sum_insured_available", "first_policy_inception", "admission_datetime", "discharge_datetime", "is_accident", "pre_existing",
                "diagnosis", "procedure", "hospital", "hospital_network", "differential_billing", "room_rate_per_day", "room_days", "claimed_amount", "aggregate_deductible_remaining",
                "copay_percent", "policy_uin", "icd10", "prescription_missing"):
        assert c[key] == ref[key], key
    assert c["claim_id"] == "CLM-20250910-0001" and c["documents"]["pharmacy_bills_prescription"] == {"present": True, "complete": False, "missing_parts": ["prescription"]}


def test_checklist_ticks_nine_and_flags_the_prescription():
    cl = I.build(session())["checklist"]
    assert [c["state"] for c in cl].count("received") == 9 and cl[7]["id"] == "pharmacy_bills_prescription" and cl[7]["state"] == "partial"
    assert "prescription is still missing" in cl[7]["note"] and cl[0]["filename"] == "policy_schedule.pdf"
    assert I.build(session())["missing"] == ["The doctor's prescription for your pharmacy bills"]


# ---------------------------------------------------------------- adding the prescription re-runs intake
def test_adding_a_prescription_releases_the_held_amount():
    s = session()
    I.build(s)
    shown = add(s, "prescription.pdf", make_pdf(["Doctor's Prescription", "Patient", "Rohan Verma, 28 years, male", "Date", "10/09/2025", "Rx: Ceftriaxone inj, Metronidazole IV, Pantoprazole inj"]))
    assert shown["status"] == "recognised" and shown["type"] == "prescription" and shown["label"] == "Doctor's prescription"
    out = I.build(s)
    a = E.assess(out["claim"])["amounts"]
    assert out["status"] == "ready" and out["missing"] == [] and all(c["state"] == "received" for c in out["checklist"])
    assert (a["held_pending"], a["payable_confirmed_now"], a["estimated_payable_if_docs_supplied"]) == (0, 122125, 122125)
    assert E.assess(out["claim"])["recommendation"] == "likely_eligible" and out["claim"]["prescription_missing"] is False


def test_a_prescription_attached_to_the_pharmacy_bill_counts_too():
    s = session(skip={"pharmacy_bills.pdf"})
    add(s, "pharm.pdf", edit(FILES["pharmacy_bills.pdf"], {"Doctor's prescription: not attached to this bill.": "Doctor's prescription: attached to this bill."}))
    out = I.build(s)
    assert out["status"] == "ready" and out["missing"] == [] and E.assess(out["claim"])["amounts"]["held_pending"] == 0


def test_a_newer_file_of_the_same_type_replaces_the_older_one():
    s = session()
    again = add(s, "schedule_v2.pdf", edit(FILES["policy_schedule.pdf"], {"Rs. 50,000": "Rs. 60,000"}))
    assert again["replaced"] is True and "replaced the earlier file" in again["message"] and s["documents"]["policy_schedule"]["filename"] == "schedule_v2.pdf"
    assert I.build(s)["claim"]["sum_insured_available"] == 560000
    same = add(s, "schedule_v2_copy.pdf", edit(FILES["policy_schedule.pdf"], {"Rs. 50,000": "Rs. 60,000"}))
    assert same["replaced"] is False                                                   # the identical file uploaded twice is not "a replacement"


# ---------------------------------------------------------------- needs_attention, with plain reasons
def reasons(s):
    out = I.build(s)
    assert out["status"] == "needs_attention" and s["claim"] is None and "claim" not in out
    return out["reasons"], out


@pytest.mark.parametrize("skipped,words,doc", [("policy_schedule.pdf", "policy schedule", "policy_schedule"), ("hospital_bill.pdf", "hospital bill", "final_bill_receipts"),
                                               ("claim_form.pdf", "claim form", "claim_form")])
def test_a_missing_essential_document_asks_for_it(skipped, words, doc):
    rs, out = reasons(session(skip=(skipped,)))
    assert len(rs) == 1 and words in rs[0]["message"] and rs[0]["message"].startswith("We couldn't find") and rs[0]["documents"] == [doc]


def test_nothing_uploaded_asks_for_the_three_essentials():
    rs, _ = reasons({"id": "t", "history": [], "claim": None})
    assert [r["documents"][0] for r in rs] == ["policy_schedule", "final_bill_receipts", "claim_form"]


def test_missing_optional_documents_do_not_block_the_claim():
    s = session(skip=("photo_id_proof.pdf", "kyc_form.pdf", "neft_form.pdf", "lab_report.pdf", "consultation_papers.pdf", "pharmacy_bills.pdf", "discharge_summary.pdf"))
    out = I.build(s)
    assert out["status"] == "ready" and len(out["missing"]) == 7
    res = E.assess(out["claim"])
    assert res["recommendation"] == "likely_eligible_pending_documents" and len(res["documents"]["missing"]) == 7


def test_a_document_in_another_persons_name_is_flagged_and_names_the_document():
    s = session()
    add(s, "bill.pdf", edit(FILES["hospital_bill.pdf"], {"Rohan Verma": "Rohan Varma"}))
    rs, _ = reasons(s)
    assert len(rs) == 1 and "final hospital bill with receipts (Rohan Varma) doesn't match your policy (Rohan Verma)" in rs[0]["message"] and rs[0]["documents"] == ["final_bill_receipts"]


def test_a_different_policy_number_is_flagged():
    s = session()
    add(s, "kyc.pdf", edit(FILES["kyc_form.pdf"], {"SYN-2805-0000-0001": "SYN-9999-0000-0002"}))
    rs, _ = reasons(s)
    assert "policy number on your KYC form (SYN-9999-0000-0002)" in rs[0]["message"] and rs[0]["documents"] == ["kyc", "policy_schedule"]


def test_different_admission_dates_between_documents_are_flagged():
    s = session()
    add(s, "ds.pdf", edit(FILES["discharge_summary.pdf"], {"10/09/2025 14:30": "12/09/2025 14:30"}))
    rs, _ = reasons(s)
    msgs = " ".join(r["message"] for r in rs)
    assert "admission date on your claim form (10 Sep 2025) is different from your discharge summary (12 Sep 2025)" in msgs
    assert any(r["documents"] == ["claim_form", "discharge_summary"] for r in rs)


def test_a_bill_that_does_not_add_up_is_flagged():
    s = session()
    add(s, "bill.pdf", edit(FILES["hospital_bill.pdf"], {"2,400": "2,500"}))
    rs, _ = reasons(s)
    assert any("add up to ₹1,84,600, but its total says ₹1,84,500" in r["message"] for r in rs)


def test_a_claim_form_amount_that_differs_from_the_bill_is_flagged():
    s = session()
    add(s, "cf.pdf", edit(FILES["claim_form.pdf"], {"Rs. 1,84,500": "Rs. 1,90,000"}))
    rs, _ = reasons(s)
    assert any("amount on your claim form (₹1,90,000) is different from your hospital bill (₹1,84,500)" in r["message"] for r in rs)


def test_fixing_the_wrong_document_clears_the_reasons():
    s = session()
    add(s, "bill.pdf", edit(FILES["hospital_bill.pdf"], {"Rohan Verma": "Rohan Varma"}))
    assert I.build(s)["status"] == "needs_attention"
    add(s, "hospital_bill.pdf", FILES["hospital_bill.pdf"])                            # the customer replaces it with the right one
    assert I.build(s)["status"] == "ready"


def test_a_schedule_with_an_unknown_plan_is_reported_not_guessed():
    s = session(skip=("policy_schedule.pdf",))
    add(s, "ps.pdf", edit(FILES["policy_schedule.pdf"], {"Optima Lite": "Mystery Plan", "Optima Secure": "Mystery Product"}))
    rs, _ = reasons(s)
    assert "couldn't read your plan, sum insured or start date" in rs[0]["message"]


def test_a_bill_whose_items_cannot_be_read_is_reported():
    s = session(skip=("hospital_bill.pdf",))
    add(s, "bill.pdf", make_pdf(["Final Hospital Bill (Itemised)", "Patient", "Rohan Verma", "no table here"]))
    rs, _ = reasons(s)
    assert any("couldn't read the items on the hospital bill" in r["message"] for r in rs)


# ---------------------------------------------------------------- files we cannot use
def enc_pdf():
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.encrypt("secret", algorithm="RC4-128")
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


@pytest.mark.parametrize("name,data,status", [
    ("notes.txt", b"just some notes " * 20, "not_pdf"), ("photo.jpg", b"\xff\xd8\xff\xe0" + b"0" * 200, "not_pdf"), ("empty.pdf", b"", "not_pdf"),
    ("broken.pdf", b"%PDF-1.4\n" + b"not really a pdf " * 30, "unreadable"), ("scan.pdf", make_pdf([]), "no_text"),
    ("huge.pdf", b"%PDF-1.4\n" + b"0" * (I.MAX_FILE_BYTES + 1), "too_large"), ("long.pdf", make_pdf(["Some text on every page of this long file"], pages=I.MAX_PAGES + 1), "too_many_pages"),
    ("lorem.pdf", make_pdf(["Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore"]), "unrecognised")])
def test_unusable_files_get_a_plain_reason_and_are_not_stored(name, data, status):
    s = {"id": "t", "history": [], "claim": None}
    shown = add(s, name, data)
    assert shown["status"] == status and shown["message"] == I.FILE_PROBLEMS[status] and "documents" not in s
    assert not any(w in shown["message"].lower() for w in ("exception", "traceback", "pypdf", "error:"))


def test_a_password_protected_pdf_is_reported():
    try:
        data = enc_pdf()
    except Exception as e:   # noqa: BLE001 - some pypdf builds need an extra crypto package for encryption
        pytest.skip(f"cannot build an encrypted test file here: {e}")
    assert I.process_file("locked.pdf", data)["status"] == "encrypted"


def test_reading_never_raises_whatever_the_bytes():
    import random
    rnd = random.Random(7)
    for _ in range(40):
        blob = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(0, 400)))
        for data in (blob, b"%PDF-1.4\n" + blob, FILES["claim_form.pdf"][: rnd.randint(20, 3000)]):
            assert I.process_file("x.pdf", data)["status"] in ("not_pdf", "unreadable", "no_text", "unrecognised", "recognised")


# ---------------------------------------------------------------- small helpers, summary and suggestions
def test_helpers():
    assert I.money("Rs. 1,84,500") == 184500 and I.money("Nil") is None and I.iso_date("10/09/2025 14:30") == "2025-09-10" and I.iso_dt("10/09/2025", "14:30") == "2025-09-10T14:30"
    assert I.norm_name("Rohan Verma, 28 years, male") == "rohan verma" == I.norm_name("Mr. ROHAN  VERMA (synthetic)") and I.norm_name("Rohan Varma") != I.norm_name("Rohan Verma")
    assert I.nice_date("2025-09-05T09:00") == "5 Sep 2025"


def test_the_summary_is_short_plain_and_lists_what_is_still_needed():
    out = I.build(session())
    md = I.claim_summary_markdown(out["claim"], out["missing"])
    assert "Rohan Verma" in md and "Optima Lite, sum insured ₹5,00,000" in md and "Laparoscopic appendectomy for Acute appendicitis" in md
    assert "10 Sep 2025 to 14 Sep 2025 (4 days)" in md and "₹1,84,500" in md and "**Still needed:** The doctor's prescription for your pharmacy bills." in md
    assert len(md.splitlines()) <= 14 and not any(w in md.lower() for w in ("annexure", "e.1.7", "engine", "json"))
    complete = I.claim_summary_markdown(out["claim"], [])
    assert "All the documents we asked for are here." in complete and "Still needed" not in complete


def test_suggestions_fit_the_claim_and_skip_what_was_asked():
    c = I.build(session())["claim"]
    assert I.suggestions(c, []) == ["How much will be paid?", "Why was my room rent reduced?", "What documents are missing?"]
    assert I.suggestions(c, ["How much will be paid?"]) == ["Why was my room rent reduced?", "What documents are missing?", "Which items are not payable?"]
    assert I.suggestions(c, ["why was my ROOM RENT reduced"])[:2] == ["How much will be paid?", "What documents are missing?"]
    assert I.suggestions(None, []) == []
    not_payable = E.assess(SAMPLES["TC02"]["claim"]) and SAMPLES["TC02"]["claim"]
    assert I.suggestions(not_payable, []) == ["What did you find on my claim?", "What happens next?", "What documents are missing?"]   # phrasings tried on the real agent
    assert len(I.suggestions(c, ["a", "b", "c", "d", "e", "f"])) == 3
