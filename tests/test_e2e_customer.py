"""The customer page in a real browser (Playwright driving the Chrome that is already installed), against a real server with the offline agent.

Skipped automatically when Playwright or Chrome is not available:  pip install -r requirements-dev.txt
"""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from app.config import ROOT
from app.intake import MAX_FILE_BYTES
from tests.pdfmaker import edit, make_pdf

pw = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

DOCS = ROOT / "reference" / "demo_documents"
ALL = sorted(str(p) for p in DOCS.glob("*.pdf"))
FORBIDDEN = re.compile(r"\b(tools?|traces?|tracing|chunks?|chunk_keys?|result_ids?|tool_trace|trace_summary|assess_claim|search_policy|get_clause|final_answer)\b", re.I)
RX = ["Doctor's Prescription", "Patient", "Rohan Verma, 28 years, male", "Rx: Ceftriaxone injection, Metronidazole IV, Pantoprazole injection"]


@pytest.fixture(scope="module")
def base():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, RETRIEVER="local", AGENT_MODE="offline", CORS_ORIGINS="")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.skip("could not start the test server")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chrome is not available to Playwright: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1100, "height": 900})
    pg = ctx.new_page()
    pg.set_default_timeout(15000)
    pg.problems = []
    pg.on("pageerror", lambda e: pg.problems.append(f"pageerror: {e}"))
    pg.on("console", lambda m: pg.problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
    yield pg
    ctx.close()
    assert pg.problems == [], pg.problems              # no script errors and no content-security-policy violations in any test


def sample_flow(page, base):
    page.goto(base + "/")
    page.click("#use-sample")
    expect(page.locator("#ready-text")).to_contain_text("We recognised 9 of 10 documents")
    page.click("#continue")
    expect(page.locator("#screen-chat")).to_be_visible()


def visible_text(page):
    return page.inner_text("body") + "\n" + " ".join(page.eval_on_selector_all("[title],[aria-label],[placeholder]", "els => els.map(e => e.title + ' ' + (e.getAttribute('aria-label')||'') + ' ' + (e.placeholder||''))"))


# ---------------------------------------------------------------- screen 1
def test_screen_one_starts_with_an_empty_checklist_and_the_agreed_words(page, base):
    page.goto(base + "/")
    expect(page.get_by_role("heading", name="Upload your claim documents")).to_be_visible()
    expect(page.locator("#checklist li")).to_have_count(10)
    expect(page.locator("#checklist li.received")).to_have_count(0)
    expect(page.locator("#use-sample")).to_have_text("Use sample documents")
    expect(page.get_by_text("Demo: please upload only sample documents.")).to_be_visible()
    expect(page.locator("#screen-chat")).to_be_hidden()
    expect(page.locator(".site-footer")).to_have_text("This is an AI-assisted estimate, not a claim decision. A claims officer makes the final decision.")
    assert page.locator("select, aside, nav").count() == 0                         # no sidebar, no dropdown
    assert page.locator(".mark").count() == 1 and page.inner_text(".word") == "ARC"


def test_sample_documents_tick_the_checklist_and_show_progress(page, base):
    page.add_init_script("""window.__progress = []; document.addEventListener('DOMContentLoaded', () => { const t = document.getElementById('progress-text');
        new MutationObserver(() => window.__progress.push(t.textContent)).observe(t, {childList: true, characterData: true, subtree: true}); });""")
    page.goto(base + "/")
    page.click("#use-sample")
    expect(page.locator("#checklist li.received")).to_have_count(9)
    expect(page.locator("#checklist li.partial")).to_have_count(1)
    expect(page.locator("#checklist li.partial")).to_contain_text("Pharmacy bills with prescription")
    expect(page.locator("#checklist li.partial")).to_contain_text("prescription is still missing")
    expect(page.locator("#file-list li.ok")).to_have_count(10)
    expect(page.locator("#ready-text")).to_contain_text("Still missing: The doctor's prescription for your pharmacy bills")
    seen = " | ".join(page.evaluate("window.__progress"))
    assert "Reading the sample documents" in seen and "Checking your claim" in seen
    assert page.locator("#files-box").evaluate("d => d.open") is False              # everything read fine: the list of files folds away, the checklist is the story
    expect(page.locator("#files-summary")).to_have_text("Your files (10)")
    page.locator("#files-summary").click()
    expect(page.locator("#file-list li").first).to_be_visible()                       # and it is one click away
    assert page.evaluate("document.querySelector('#ready').getBoundingClientRect().top") < 500       # the way forward is near the top, not below ten file rows


def test_uploading_files_with_the_file_picker_gives_the_same_result(page, base):
    page.goto(base + "/")
    page.set_input_files("#file-input", ALL)
    expect(page.locator("#checklist li.received")).to_have_count(9)
    expect(page.locator("#file-list")).to_contain_text("hospital_bill.pdf")
    expect(page.locator("#file-list")).to_contain_text("Recognised as: Final hospital bill with receipts.")
    expect(page.locator("#continue")).to_be_enabled()


def test_dropping_files_onto_the_area_works_and_the_area_lights_up_while_dragging(page, base):
    import base64
    files = {p.split("/")[-1]: base64.b64encode(open(p, "rb").read()).decode() for p in ALL}
    page.goto(base + "/")
    page.evaluate("document.getElementById('dropzone').dispatchEvent(new DragEvent('dragenter', {bubbles: true, cancelable: true}))")
    expect(page.locator("#dropzone")).to_have_class(re.compile(r"\bover\b"))
    page.evaluate("""(files) => { const dt = new DataTransfer();
        for (const [name, b64] of Object.entries(files)) { dt.items.add(new File([Uint8Array.from(atob(b64), c => c.charCodeAt(0))], name, {type: 'application/pdf'})); }
        document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})); }""", files)
    expect(page.locator("#checklist li.received")).to_have_count(9)
    expect(page.locator("#dropzone")).not_to_have_class(re.compile(r"\bover\b"))
    expect(page.locator("#file-list li.ok")).to_have_count(10)


def test_the_drop_area_is_reachable_and_usable_from_the_keyboard(page, base):
    page.goto(base + "/")
    page.focus("#dropzone")
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("Enter")
    chooser.value.set_files(ALL[:3])
    expect(page.locator("#file-list li")).to_have_count(3)


def test_a_wrong_document_shows_plain_reasons_and_replacing_it_fixes_them(page, base, tmp_path):
    bad = tmp_path / "hospital_bill.pdf"
    bad.write_bytes(edit((DOCS / "hospital_bill.pdf").read_bytes(), {"Rohan Verma": "Rohan Varma"}))
    page.goto(base + "/")
    page.set_input_files("#file-input", [p for p in ALL if not p.endswith("hospital_bill.pdf")] + [str(bad)])
    expect(page.locator("#attention")).to_be_visible()
    expect(page.locator("#reasons")).to_contain_text("doesn't match your policy (Rohan Verma)")
    expect(page.locator("#ready")).to_be_hidden()
    expect(page.locator("#checklist li.flag")).to_have_count(1)
    flagged = page.locator("#checklist li.flag")
    expect(flagged).to_contain_text("Final hospital bill with receipts")
    expect(flagged).to_contain_text("Please check this document (hospital_bill.pdf).")
    assert flagged.evaluate("li => getComputedStyle(li.querySelector('.tick'), '::before').content") == '"!"'      # an amber "!", not a tick
    assert page.locator("#checklist li.received:not(.flag)").count() == 8            # eight ordinary ticks; the ninth document is received but flagged, and looks it
    assert page.locator("#files-box").evaluate("d => d.open") is True                # a problem keeps the file list open
    expect(page.locator("#files-summary")).to_contain_text("1 need a look")
    expect(page.locator("#file-list li.check")).to_contain_text("Needs a look: see above")
    assert page.locator("#file-list li.check .file-state").inner_text() != ""
    assert not FORBIDDEN.search(page.inner_text("#attention"))
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Replace: Final hospital bill with receipts").click()
    chooser.value.set_files(str(DOCS / "hospital_bill.pdf"))
    expect(page.locator("#ready-text")).to_contain_text("We recognised 9 of 10 documents")
    expect(page.locator("#attention")).to_be_hidden()
    expect(page.locator("#file-list")).to_contain_text("This replaced the earlier file.")


def test_unusable_files_get_friendly_messages(page, base, tmp_path):
    txt, big = tmp_path / "notes.txt", tmp_path / "huge.pdf"
    txt.write_bytes(b"just some notes " * 20)
    big.write_bytes(b"%PDF-1.4\n" + b"0" * (MAX_FILE_BYTES + 10))
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(make_pdf([]))
    page.goto(base + "/")
    page.set_input_files("#file-input", [str(txt), str(big), str(scan), ALL[0]])
    expect(page.locator("#file-list li.bad")).to_have_count(3)
    expect(page.locator("#file-list")).to_contain_text("This isn't a PDF. Please upload PDF files for now.")
    expect(page.locator("#file-list")).to_contain_text("larger than 5 MB")
    expect(page.locator("#file-list")).to_contain_text("It may be a scan or a photo")
    assert not re.search(r"traceback|exception|pypdf", page.inner_text("#file-list"), re.I)


def test_server_problems_are_shown_in_plain_words(page, base):
    page.route("**/documents/sample", lambda route: route.abort())
    page.goto(base + "/")
    page.click("#use-sample")
    expect(page.locator("#attention")).to_contain_text("We couldn't reach the server")
    page.unroute("**/documents/sample")
    page.route("**/documents/sample", lambda route: route.fulfill(status=500, content_type="application/json", body='{"error":{"code":"internal_error","message":"boom"}}'))
    page.click("#use-sample")
    expect(page.locator("#attention")).to_contain_text("Something went wrong on our side")
    assert "boom" not in page.inner_text("#attention")
    page.problems.clear()                                                            # the browser logs the aborted and failed requests themselves


# ---------------------------------------------------------------- screen 2
def test_chat_starts_with_the_claim_summary_and_three_suggestions(page, base):
    sample_flow(page, base)
    first = page.locator("#thread .msg-arc").first
    expect(first).to_contain_text("Rohan Verma")
    expect(first).to_contain_text("Optima Lite, sum insured ₹5,00,000")
    expect(first).to_contain_text("Still needed: The doctor's prescription for your pharmacy bills.")
    chips = page.locator("#thread .chip")
    expect(chips).to_have_count(3)
    assert [c.strip() for c in chips.all_inner_texts()] == ["How much will be paid?", "Why was my room rent reduced?", "What documents are missing?"]
    assert page.locator("select, aside").count() == 0 and page.locator("#chat-input").count() == 1
    expect(page.get_by_role("button", name="Add a document")).to_be_visible()
    assert not FORBIDDEN.search(visible_text(page))


def test_an_answer_is_a_short_summary_and_show_more_opens_the_details(page, base):
    sample_flow(page, base)
    page.get_by_role("button", name="How much will be paid?").click()
    answer = page.locator("#thread .msg-arc").nth(1)
    expect(answer).to_contain_text("₹1,22,125")
    expect(answer).to_contain_text("₹1,01,625")
    expect(answer).to_contain_text("Likely eligible once your documents are complete")
    expect(answer).to_contain_text("Room rent above your plan's limit: −₹49,875")
    assert "The officer decides" not in answer.inner_text() and "Annexure" not in answer.inner_text()
    show_more = answer.get_by_role("button", name="Show more")
    expect(show_more).to_be_visible()
    expect(answer.get_by_text("Room rent working")).to_be_hidden()                   # details and policy references are hidden until asked for
    expect(answer.get_by_text("Policy references")).to_be_hidden()
    show_more.click()
    expect(answer.get_by_role("button", name="Show less")).to_be_visible()
    expect(answer.get_by_text("Room rent working")).to_be_visible()
    expect(answer.get_by_text("Documents (8 of 9 complete)")).to_be_visible()
    expect(answer.get_by_text("Policy references")).to_be_visible()
    chip = answer.get_by_role("button", name="B.1.1.1 Note iii p.12")
    quote = answer.locator(".ref-quote").nth(2)
    expect(quote).to_be_hidden()
    chip.click()
    expect(quote).to_be_visible()
    expect(quote).to_contain_text("Proportionate deduction on Room Rent")
    answer.get_by_role("button", name="Show less").click()
    expect(answer.get_by_text("Room rent working")).to_be_hidden()
    assert not FORBIDDEN.search(visible_text(page))


def test_only_the_latest_answer_has_suggestions_and_never_more_than_three(page, base):
    sample_flow(page, base)
    page.get_by_role("button", name="How much will be paid?").click()
    expect(page.locator("#thread .chip")).to_have_count(3)
    page.get_by_role("button", name="Why was my room rent reduced?").click()
    expect(page.locator("#thread .msg-arc").nth(2)).to_contain_text("₹12,000")
    expect(page.locator("#thread .chips")).to_have_count(1)                          # the earlier suggestions were removed
    labels = [c.strip() for c in page.locator("#thread .chip").all_inner_texts()]
    assert len(labels) == 3 and "How much will be paid?" not in labels and "Why was my room rent reduced?" not in labels
    assert page.locator("#thread > *").last.get_attribute("class") == "chips"       # they sit under the latest answer
    page.fill("#chat-input", "What documents are missing?")
    page.keyboard.press("Enter")
    expect(page.locator("#thread .msg-arc").nth(3)).to_contain_text("prescription")


def test_the_page_shows_only_three_suggestions_even_if_the_server_sends_more(page, base):
    sample_flow(page, base)
    six = ", ".join(f'"Suggestion number {i}?"' for i in range(1, 7))
    body = ('{"session_id":"x","status":"ok","answer_type":"general_answer","answer_markdown":"Hello","summary_markdown":"A short answer.","sections":[],"citations":[],"suggestions":[' + six + ']}')
    page.route("**/chat", lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    page.get_by_role("button", name="How much will be paid?").click()
    expect(page.locator("#thread .msg-arc").last).to_contain_text("A short answer.")
    assert [c.strip() for c in page.locator("#thread .chip").all_inner_texts()] == ["Suggestion number 1?", "Suggestion number 2?", "Suggestion number 3?"]
    assert page.locator("#thread .msg-arc").last.get_by_role("button").count() == 0          # a plain answer has no "Show more" when there is nothing more to show


def test_typing_a_question_shows_progress_and_the_question(page, base):
    sample_flow(page, base)
    page.fill("#chat-input", "What documents are missing?")
    page.click("#send")
    expect(page.locator("#thread .msg-me")).to_have_text("What documents are missing?")
    expect(page.locator("#thread .msg-arc").nth(1)).to_contain_text("8 of 9 documents complete")
    assert page.input_value("#chat-input") == ""


def test_add_a_document_reruns_intake_and_updates_the_claim(page, base, tmp_path):
    sample_flow(page, base)
    rx = tmp_path / "prescription.pdf"
    rx.write_bytes(make_pdf(RX))
    page.set_input_files("#add-input", str(rx))
    expect(page.locator("#thread")).to_contain_text("Added: Doctor's prescription. I checked your claim again.")      # a final message, not a passing one
    expect(page.locator("#thread .msg-arc").last).to_contain_text("All the documents we asked for are here.")
    expect(page.locator("#thread .chip")).to_have_count(3)
    page.get_by_role("button", name="How much will be paid?").click()
    answer = page.locator("#thread .msg-arc").last
    expect(answer).to_contain_text("₹1,22,125")
    assert "held until it arrives" not in answer.inner_text() and "Prescription missing" not in answer.inner_text()


def test_adding_an_unusable_document_says_so_and_changes_nothing(page, base, tmp_path):
    sample_flow(page, base)
    txt = tmp_path / "notes.txt"
    txt.write_bytes(b"just some notes " * 20)
    page.set_input_files("#add-input", str(txt))
    expect(page.locator("#thread")).to_contain_text("We couldn't use notes.txt: This isn't a PDF.")
    expect(page.locator("#thread")).to_contain_text("Nothing was added.")
    expect(page.locator("#screen-chat")).to_be_visible()


def test_adding_a_conflicting_document_sends_the_customer_back_to_fix_it(page, base, tmp_path):
    sample_flow(page, base)
    kyc = tmp_path / "kyc.pdf"
    kyc.write_bytes(edit((DOCS / "kyc_form.pdf").read_bytes(), {"Rohan Verma": "Someone Else"}))
    page.set_input_files("#add-input", str(kyc))
    expect(page.locator("#screen-upload")).to_be_visible()
    expect(page.locator("#attention")).to_contain_text("doesn't match your policy")
    expect(page.locator("#screen-chat")).to_be_hidden()


def test_a_slow_or_failed_answer_is_explained_and_can_be_retried(page, base):
    sample_flow(page, base)
    body = '{"session_id":"x","status":"timeout","answer_type":"general_answer","answer_markdown":"## Please try again\\n\\nNothing was assessed.","summary_markdown":"**This is taking longer than expected.** Nothing was assessed. Please try again in a moment.","sections":[],"citations":[],"suggestions":[]}'
    page.route("**/chat", lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    page.get_by_role("button", name="How much will be paid?").click()
    warn = page.locator("#thread .msg-arc.warn")
    expect(warn).to_contain_text("This is taking longer than expected")
    expect(warn.get_by_role("button", name="Try again")).to_be_visible()
    assert page.locator("#thread .chip").count() == 0                                # no suggestions for an answer that did not happen
    page.unroute("**/chat")
    warn.get_by_role("button", name="Try again").click()
    expect(page.locator("#thread .msg-arc").last).to_contain_text("₹1,22,125")


def test_a_server_error_in_chat_is_friendly(page, base):
    sample_flow(page, base)
    page.route("**/chat", lambda route: route.fulfill(status=500, content_type="application/json", body='{"error":{"code":"internal_error","message":"x"}}'))
    page.get_by_role("button", name="How much will be paid?").click()
    expect(page.locator("#thread .msg-arc.problem")).to_contain_text("Something went wrong on our side")
    page.problems.clear()                                                            # the browser logs the failed request itself


# ---------------------------------------------------------------- a server that forgot the visit, and a server that is out of date
GONE = '{"error":{"code":"http_404","message":"Unknown session. Create one with POST /sessions."}}'


def test_a_forgotten_visit_before_any_upload_recovers_without_bothering_the_customer(page, base):
    hits = {"n": 0}

    def first_time_gone(route):
        hits["n"] += 1
        route.fulfill(status=404, content_type="application/json", body=GONE) if hits["n"] == 1 else route.continue_()
    page.route("**/documents/sample", first_time_gone)
    page.goto(base + "/")
    page.click("#use-sample")
    expect(page.locator("#ready-text")).to_contain_text("We recognised 9 of 10 documents")
    assert hits["n"] == 2                                                             # the server forgot us once; the page started a new visit and asked again
    assert "session" not in page.inner_text("#attention").lower()
    page.problems.clear()                                                             # the browser logs the 404 itself


def test_a_forgotten_visit_in_the_chat_sends_the_customer_back_to_upload_with_a_plain_reason(page, base):
    sample_flow(page, base)
    page.route("**/chat", lambda route: route.fulfill(status=404, content_type="application/json", body=GONE))
    page.get_by_role("button", name="How much will be paid?").click()
    expect(page.locator("#screen-upload")).to_be_visible()
    expect(page.locator("#screen-chat")).to_be_hidden()
    expect(page.locator("#attention")).to_contain_text("The server was restarted, so your visit ended and we've started a new one. Please add your documents again.")
    expect(page.locator("#checklist li.received")).to_have_count(0)                  # the documents went with the old visit: the checklist says so
    page.unroute("**/chat")
    page.click("#use-sample")                                                         # and starting again works
    expect(page.locator("#ready-text")).to_contain_text("We recognised 9 of 10 documents")
    page.problems.clear()


def test_a_forgotten_visit_when_adding_a_document_starts_over_and_says_why(page, base, tmp_path):
    sample_flow(page, base)
    rx = tmp_path / "prescription.pdf"
    rx.write_bytes(make_pdf(RX))
    page.route("**/documents", lambda route: route.fulfill(status=404, content_type="application/json", body=GONE))
    page.set_input_files("#add-input", str(rx))
    expect(page.locator("#thread")).to_contain_text("The server was restarted, so your visit ended")
    page.problems.clear()


def test_a_server_that_predates_intake_is_recognised_at_once(page, base):
    page.route(re.compile(r".*/sessions$"), lambda route: route.fulfill(status=200, content_type="application/json", body='{"session_id":"abc123"}'))   # what the old server answered
    page.goto(base + "/")
    expect(page.locator("#attention")).to_contain_text("This ARC service is out of date, so uploads aren't available yet.")
    expect(page.locator("#attention")).to_contain_text("scripts/run_demo.sh")
    assert page.get_attribute("#dropzone", "aria-disabled") == "true"
    page.click("#use-sample", force=True)                                            # Playwright will not click a disabled link; force it to prove nothing happens
    page.wait_for_timeout(500)
    assert page.locator("#file-list li").count() == 0                                # nothing is sent to a server that cannot read it
    assert "session has ended" not in page.inner_text("body").lower()


def test_a_missing_route_is_not_reported_as_a_lost_session(page, base):
    page.route("**/documents/sample", lambda route: route.fulfill(status=404, content_type="application/json", body='{"error":{"code":"http_404","message":"Not Found"}}'))
    page.goto(base + "/")
    page.click("#use-sample")
    expect(page.locator("#attention")).to_contain_text("This service is out of date, so we can't do that yet.")
    assert "session" not in page.inner_text("#attention").lower() and "reload" not in page.inner_text("#attention").lower()
    page.problems.clear()


# ---------------------------------------------------------------- no developer vocabulary, and dev mode
def test_no_developer_words_appear_anywhere_in_a_normal_visit(page, base, tmp_path):
    page.goto(base + "/")
    assert not FORBIDDEN.search(page.content()) and not FORBIDDEN.search(visible_text(page))
    page.click("#use-sample")
    expect(page.locator("#ready")).to_be_visible()
    assert not FORBIDDEN.search(page.content())
    page.click("#continue")
    for question in ("How much will be paid?", "Why was my room rent reduced?", "What documents are missing?"):
        page.get_by_role("button", name=question).click()
        expect(page.locator("#thread .msg-arc").last).not_to_have_text("")
        page.get_by_role("button", name="Show more").last.click()
    html, shown = page.content(), visible_text(page)
    assert not FORBIDDEN.search(html), FORBIDDEN.search(html).group(0)
    assert not FORBIDDEN.search(shown), FORBIDDEN.search(shown).group(0)
    assert page.locator("text=How ARC got this answer").count() == 0 and page.locator(".dev-badge, .dev-trace, .dev-banner").count() == 0
    assert page.locator("script[src*='dev']").count() == 0 and page.evaluate("typeof window.ARCDev") == "undefined"


def test_dev_mode_adds_the_badge_the_banner_and_the_trace_panel(page, base):
    page.goto(base + "/?dev=1")
    badge = page.locator(".dev-badge")
    expect(badge).to_have_text("Offline stand-in")                                   # the test server runs the offline agent
    expect(page.locator(".dev-banner")).to_contain_text("not the real ARC agent")
    page.click("#use-sample")
    expect(page.locator("#ready")).to_be_visible()
    page.click("#continue")
    page.get_by_role("button", name="How much will be paid?").click()
    trace = page.locator(".dev-trace")
    expect(trace).to_have_count(1)
    assert trace.evaluate("d => d.open") is False                                    # collapsed
    trace.locator("summary").click()
    expect(trace).to_contain_text("assess_claim")
    expect(trace).to_contain_text("ok")
    expect(trace).to_contain_text("Arguments are never shown.")
    text = trace.inner_text()
    assert "what_if" not in text and "room_rate" not in text and re.search(r"\d+ ms", text)     # tool name, ok, milliseconds only


# ---------------------------------------------------------------- layout
def test_both_screens_fit_a_phone_without_sideways_scrolling(browser, base):
    ctx = browser.new_context(viewport={"width": 375, "height": 800}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.set_default_timeout(15000)
    fits = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    page.goto(base + "/")
    assert page.evaluate(fits)
    page.click("#use-sample")
    expect(page.locator("#ready")).to_be_visible()
    assert page.evaluate(fits)
    page.click("#continue")
    assert page.evaluate(fits)
    page.get_by_role("button", name="How much will be paid?").click()
    page.get_by_role("button", name="Show more").click()
    expect(page.locator("#thread .msg-arc").last).to_contain_text("₹1,22,125")
    assert page.evaluate(fits)
    ctx.close()


SIZES = [(1280, 720), (1366, 680), (1024, 600), (390, 844), (360, 640)]


def chips_and_bar(pg):
    return pg.evaluate("""() => {
        const box = e => { const b = e.getBoundingClientRect(); return { top: b.top, bottom: b.bottom }; };
        return { vh: innerHeight, bar: box(document.getElementById('composer')), chips: [...document.querySelectorAll('#thread .chip')].map(box) };
    }""")


def assert_chips_clear_of_the_input_bar(pg, where):
    got = chips_and_bar(pg)
    assert len(got["chips"]) == 3, f"{where}: expected 3 suggestions, found {len(got['chips'])}"
    for i, c in enumerate(got["chips"]):
        assert c["top"] >= 0 and c["bottom"] <= got["bar"]["top"] - 4 and c["bottom"] <= got["vh"], f"{where}: suggestion {i + 1} is hidden or touches the input bar: {c} bar={got['bar']} vh={got['vh']}"


@pytest.mark.parametrize("size", SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_the_suggestions_are_fully_visible_above_the_input_bar(browser, base, size):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    pg = ctx.new_page()
    pg.set_default_timeout(15000)
    try:
        pg.goto(base + "/")
        pg.click("#use-sample")
        expect(pg.locator("#ready")).to_be_visible()
        pg.click("#continue")
        expect(pg.locator("#thread .chip")).to_have_count(3)
        pg.wait_for_timeout(300)
        assert_chips_clear_of_the_input_bar(pg, "first message")
        pg.get_by_role("button", name="How much will be paid?").click()
        expect(pg.locator("#thread .msg-arc").nth(1)).to_contain_text("₹1,22,125")
        expect(pg.locator("#thread .chip")).to_have_count(3)
        pg.wait_for_timeout(300)
        assert_chips_clear_of_the_input_bar(pg, "after an assessment")
    finally:
        ctx.close()


def test_a_plain_question_gets_one_line_and_no_show_more(page, base):
    page.goto(base + "/")
    page.click("#use-sample")
    expect(page.locator("#ready")).to_be_visible()
    page.click("#continue")
    page.fill("#chat-input", "what's my name")
    page.keyboard.press("Enter")
    answer = page.locator("#thread .msg-arc").nth(1)
    expect(answer).to_contain_text("Your name on this claim is Rohan Verma.")
    expect(answer.get_by_role("button", name="Show more")).to_have_count(0)
    assert "₹" not in answer.inner_text() and page.problems == []
