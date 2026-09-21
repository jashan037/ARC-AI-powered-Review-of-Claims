"""The document endpoints: upload, sample documents, intake, limits, the customer audience, and what is (not) logged."""
import json
import logging

from fastapi.testclient import TestClient

from app import intake as I
from app import main
from app.config import settings
from app.observability import JsonFormatter
from tests.helpers.pdfmaker import edit, make_pdf

client = TestClient(main.app, raise_server_exceptions=False)
FILES = dict(I.sample_files())


def new_session(audience="customer"):
    r = client.post("/sessions", json={"audience": audience})
    assert r.status_code == 200
    return r.json()["session_id"]


def upload(sid, named):
    return client.post(f"/sessions/{sid}/documents", files=[("files", (n, d, "application/pdf")) for n, d in named])


# ---------------------------------------------------------------- the whole happy path with the sample documents
def test_sample_documents_then_intake_then_chat():
    sid = new_session()
    r = client.post(f"/sessions/{sid}/documents/sample")
    assert r.status_code == 200
    body = r.json()
    assert len(body["files"]) == 10 and all(f["status"] == "recognised" for f in body["files"])
    assert [c["state"] for c in body["checklist"]].count("received") == 9 and body["checklist"][7]["state"] == "partial"

    out = client.post(f"/sessions/{sid}/intake").json()
    assert out["status"] == "ready" and out["reasons"] == [] and out["missing"] == ["The doctor's prescription for your pharmacy bills"]
    assert out["claim"]["insured"] == "Rohan Verma" and out["claim"]["plan"] == "Optima Lite" and "claim_id" in out["claim"]
    assert "One document is still missing" in out["first_message"] and "suggestions" not in out and "summary_markdown" not in out
    assert "bill_lines" not in json.dumps(out) and "documents_data" not in json.dumps(out)              # the customer never gets the raw claim back

    chat = client.post(f"/sessions/{sid}/chat", json={"message": "How much will be paid?"}).json()      # the intake loaded the claim into the session
    assert set(chat) == {"status", "reply", "sources"} and "₹1,22,125" in chat["reply"] and "₹1,01,625" in chat["reply"]


def test_uploading_files_works_the_same_as_the_sample_button():
    sid = new_session()
    r = upload(sid, sorted(FILES.items()))
    assert r.status_code == 200 and all(f["status"] == "recognised" for f in r.json()["files"])
    assert client.post(f"/sessions/{sid}/intake").json()["status"] == "ready"


def test_adding_the_prescription_updates_the_claim_and_the_chat_uses_it():
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")
    rx = make_pdf(["Doctor's Prescription", "Patient", "Rohan Verma, 28 years, male", "Rx: Ceftriaxone injection, Metronidazole IV, Pantoprazole injection"])
    added = upload(sid, [("prescription.pdf", rx)]).json()
    assert added["files"][0]["type"] == "prescription" and all(c["state"] == "received" for c in added["checklist"])
    out = client.post(f"/sessions/{sid}/intake").json()
    assert out["status"] == "ready" and out["missing"] == [] and "Your documents look complete." in out["first_message"]
    chat = client.post(f"/sessions/{sid}/chat", json={"message": "How much will be paid?"}).json()
    assert "₹1,22,125" in chat["reply"]


# ---------------------------------------------------------------- needs_attention
def test_intake_with_nothing_uploaded_needs_attention():
    sid = new_session()
    out = client.post(f"/sessions/{sid}/intake").json()
    assert out["status"] == "needs_attention" and len(out["reasons"]) == 3 and out["checklist"][0]["state"] == "missing" and "claim" not in out
    assert client.post(f"/sessions/{sid}/chat", json={"message": "hello"}).status_code == 200      # chat still answers policy questions; it just has no claim


def test_a_wrong_document_gives_plain_reasons_and_replacing_it_fixes_them():
    sid = new_session()
    files = dict(FILES)
    files["hospital_bill.pdf"] = edit(FILES["hospital_bill.pdf"], {"Rohan Verma": "Rohan Varma"})
    upload(sid, sorted(files.items()))
    out = client.post(f"/sessions/{sid}/intake").json()
    assert out["status"] == "needs_attention" and len(out["reasons"]) == 1
    assert out["reasons"][0]["documents"] == ["final_bill_receipts"] and "doesn't match your policy" in out["reasons"][0]["message"]
    assert not any(w in json.dumps(out["reasons"]).lower() for w in ("traceback", "exception", "engine", "json", "e.1.7"))
    fixed = upload(sid, [("hospital_bill.pdf", FILES["hospital_bill.pdf"])]).json()
    assert fixed["files"][0]["replaced"] is True
    assert client.post(f"/sessions/{sid}/intake").json()["status"] == "ready"


def test_a_session_that_had_a_claim_loses_it_when_a_new_document_conflicts():
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    assert client.post(f"/sessions/{sid}/intake").json()["status"] == "ready" and main.SESSIONS[sid]["claim"]
    upload(sid, [("kyc.pdf", edit(FILES["kyc_form.pdf"], {"Rohan Verma": "Someone Else"}))])
    assert client.post(f"/sessions/{sid}/intake").json()["status"] == "needs_attention" and main.SESSIONS[sid]["claim"] is None


# ---------------------------------------------------------------- unusable files, limits, hostile input
def test_files_that_cannot_be_used_get_plain_messages_and_do_not_stop_the_good_ones():
    sid = new_session()
    r = upload(sid, [("notes.txt", b"hello " * 50), ("scan.pdf", make_pdf([])), ("claim_form.pdf", FILES["claim_form.pdf"]), ("junk.pdf", b"%PDF-1.4\n" + b"x" * 200)])
    assert r.status_code == 200
    got = {f["filename"]: f for f in r.json()["files"]}
    assert got["notes.txt"]["status"] == "not_pdf" and got["scan.pdf"]["status"] == "no_text" and got["junk.pdf"]["status"] == "unreadable" and got["claim_form.pdf"]["status"] == "recognised"
    assert got["scan.pdf"]["message"] == I.FILE_PROBLEMS["no_text"]


def test_a_file_over_5mb_is_refused_by_name_but_the_request_is_accepted():
    sid = new_session()
    big = b"%PDF-1.4\n" + b"0" * (I.MAX_FILE_BYTES + 10)
    r = upload(sid, [("big.pdf", big), ("claim_form.pdf", FILES["claim_form.pdf"])])
    got = {f["filename"]: f["status"] for f in r.json()["files"]}
    assert r.status_code == 200 and got == {"big.pdf": "too_large", "claim_form.pdf": "recognised"}


def test_a_whole_upload_over_the_limit_gets_413_and_other_routes_keep_the_small_limit():
    sid = new_session()
    r = client.post(f"/sessions/{sid}/documents", files=[("files", ("a.pdf", b"%PDF" + b"0" * (settings.max_upload_bytes + 1024), "application/pdf"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large" and "26 MB" in r.json()["error"]["message"]
    r = client.post(f"/sessions/{sid}/chat", json={"message": "x" * 100, "pad": "y" * (settings.max_request_bytes + 10)})
    assert r.status_code == 413 and "256 KB" in r.json()["error"]["message"]                     # chat is still limited to a few hundred KB
    r = client.post(f"/sessions/{sid}/documents", content=iter([b"a" * 1_000_000] * 28), headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413                                                                  # chunked, no Content-Length: the limit still applies


def test_more_than_fifteen_files_at_once_is_a_friendly_422():
    sid = new_session()
    r = upload(sid, [(f"f{i}.pdf", FILES["kyc_form.pdf"]) for i in range(I.MAX_FILES_PER_UPLOAD + 1)])
    assert r.status_code == 422 and r.json()["error"]["message"] == "Please upload up to 15 files at a time."


def test_no_files_is_a_clean_422_and_unknown_sessions_are_404():
    sid = new_session()
    assert client.post(f"/sessions/{sid}/documents").status_code == 422
    assert client.post("/sessions/nope/documents/sample").status_code == 404 and client.post("/sessions/nope/intake").status_code == 404
    assert upload("nope", [("a.pdf", FILES["kyc_form.pdf"])]).status_code == 404


def test_hostile_file_names_are_stripped_and_returned_as_plain_text():
    sid = new_session()
    r = upload(sid, [("../../etc/passwd", b"x" * 50), ("C:\\Users\\me\\<img src=x onerror=alert(1)>.pdf", FILES["kyc_form.pdf"]), ("a" * 400 + ".pdf", FILES["neft_form.pdf"])])
    names = [f["filename"] for f in r.json()["files"]]
    assert names[0] == "passwd" and "/" not in names[1] and "\\" not in names[1] and "<" not in names[1] and ">" not in names[1] and len(names[2]) == 100


def test_a_new_visit_says_the_server_supports_intake():
    body = client.post("/sessions", json={"audience": "customer"}).json()
    assert body["intake"] is True and set(body) == {"session_id", "intake"}          # the page uses this to notice an older server



# ---------------------------------------------------------------- the customer hears customer wording; the officer keeps the officer wording
def chat(sid, msg="How much will be paid?"):
    return client.post(f"/sessions/{sid}/chat", json={"message": msg}).json()



# ---------------------------------------------------------------- privacy
def test_only_counts_are_logged_never_file_names_or_contents():
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(JsonFormatter().format(record))
    h = H()
    logging.getLogger("claims").addHandler(h)
    try:
        sid = new_session()
        upload(sid, [("Rohan_Verma_secret_bill.pdf", FILES["hospital_bill.pdf"]), ("notes.txt", b"private text " * 20)])
    finally:
        logging.getLogger("claims").removeHandler(h)
    line = next(r for r in records if '"documents"' in r)
    assert '"files": 2' in line and '"recognised": 1' in line
    blob = " ".join(records)
    for private in ("Rohan_Verma_secret_bill", "notes.txt", "Rohan Verma", "SYN-2805", "private text", "1,84,500", "184500"):
        assert private not in blob, private


def test_a_session_refuses_documents_beyond_the_cap_with_a_plain_message(monkeypatch):
    import dataclasses
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, max_docs_per_session=3))
    sid = new_session()
    r = upload(sid, [(f"f{i}.pdf", FILES["kyc_form.pdf"]) for i in range(5)])
    got = r.json()["files"]
    assert r.status_code == 200 and [f["status"] for f in got] == ["recognised"] * 3 + ["limit"] * 2
    assert got[3]["message"] == "You have reached the limit of 3 documents for one claim, so this one was not added."
    assert upload(sid, [("again.pdf", FILES["kyc_form.pdf"])]).json()["files"][0]["status"] == "limit"


def test_only_the_first_intake_message_carries_the_estimate_sentence():
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    first = client.post(f"/sessions/{sid}/intake").json()["first_message"]
    later = client.post(f"/sessions/{sid}/intake").json()["first_message"]
    assert first.endswith("Amounts I give are estimates; your insurer's team makes the final decision.") and "estimates" not in later
