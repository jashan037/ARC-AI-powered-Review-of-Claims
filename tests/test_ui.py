"""The customer page in a real browser (Playwright with the installed Chrome; skipped without them).

Offline: the app with a stand-in agent that returns fixed plain replies, so nothing here talks to Azure. Three views at three real paths,
the accessibility pass (axe-core), the keyboard flow, the colours, the geometry, the report download and the responsive checks.
"""
import colorsys
import re

import pytest

from app.config import ROOT
from app.tools.canned import NOT_READY  # noqa: F401  (imported so the module fails loudly if the fixed message moves)

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from tests.helpers.uiserver import Server  # noqa: E402

DOCS = sorted((ROOT / "demo" / "samples" / "on_time").glob("*.pdf"))
AXE = ROOT / "tests" / "helpers" / "axe.min.js"
# the palette: white and black ink, one cyan accent (and its band, tint and text shade), and the status colours of the file rows and the product picture
INK, ACCENT, SURFACE2 = (11, 11, 12), (56, 198, 236), (244, 244, 245)
ALLOWED = {INK, ACCENT, SURFACE2, (13, 181, 237), (234, 248, 253), (11, 111, 140),          # accent: band, tint, as text
           (42, 154, 48), (29, 107, 34), (234, 246, 235),                                       # ok: dot, text, tint
           (246, 168, 37), (255, 246, 229), (92, 58, 0),                                        # attention: dot, tint, text
           (180, 35, 24), (253, 236, 234)}                                                      # a file that could not be read: text, tint


@pytest.fixture(scope="module")
def server():
    s = Server().start()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome")
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chrome is not available: {e}")
        yield b
        b.close()


def new_page(browser, server, width=1280, height=800, path="/"):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    pg = ctx.new_page()
    pg.requests = []
    pg.on("request", lambda r: pg.requests.append(r.url))
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.console_errors = []
    # a request a test aborts on purpose also logs a console error: that is the test's own doing, not the page's
    noise = re.compile(r"Failed to load resource|net::ERR_|ERR_FAILED|ERR_ABORTED")
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" and not noise.search(m.text) else None)
    pg.goto(server.url + path)
    pg.ctx = ctx
    return pg


@pytest.fixture
def page(server, browser):
    pg = new_page(browser, server)
    yield pg
    assert pg.errors == [] and pg.console_errors == []
    pg.ctx.close()


# ---------------------------------------------------------------- helpers
def settled(page):
    """The landing entrance plays once on load; wait until it has handed over to the authored CSS."""
    for _ in range(50):              # polled from here: the page's CSP rightly refuses wait_for_function's string evaluation
        if page.evaluate("document.documentElement.getAttribute('data-enter')") in (None, "done"):
            return
        page.wait_for_timeout(100)
    raise AssertionError("the landing entrance never finished")


def to_upload(page):
    page.click("#landing-start")
    expect(page.locator("#view-upload")).to_be_visible()


def to_chat(page, files=DOCS):
    if page.locator("#view-upload").is_hidden():
        to_upload(page)
    page.set_input_files("#picker", [str(f) for f in files])
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)


def tab_to(page, element_id, limit=6):
    """Press Tab until the element has focus (the hidden views hold no focusable element, so this is a short walk)."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.id") == element_id:
            return True
    return False


def ask(page, text):
    page.fill("#ask", text)
    page.keyboard.press("Enter")
    expect(page.locator("#send")).to_be_disabled()
    expect(page.locator(".dots")).to_have_count(0, timeout=10000)


# ---------------------------------------------------------------- 1. the landing view
def BANNED_TAGS():   # the hero carries one heading; the metrics screen below it has its own h2 and one h3 per card
    return "header, footer, nav:not(#tools), aside, h2:not(#mx-title), h3:not(.mx-title), h4, h5, h6"


def test_the_landing_view_is_one_headline_one_button_a_sample_link_and_the_note(page):
    expect(page.locator("#view-landing")).to_be_visible()
    settled(page)
    assert page.locator(BANNED_TAGS()).count() == 0
    assert page.title() == "ARC"
    assert page.locator("h1:visible").inner_text().replace("\n", " ") == "Know where your claim stands before it's decided."
    assert page.locator("#landing-lead").inner_text().startswith("Upload your claim documents.")
    assert page.locator("#landing-note").inner_text() == "Demo. Please upload only sample documents. Amounts are estimates; your insurer's team makes the final decision."
    buttons = page.locator("#view-landing a.btn--hero")
    assert buttons.count() == 1 and buttons.first.inner_text() == "Upload your documents"
    assert page.get_by_role("link", name="Try with sample documents").count() == 1
    h1 = page.locator("#landing-title").bounding_box()
    assert abs((h1["x"] + h1["width"] / 2) - 640) < 2 and h1["y"] < 400                        # centred, in the upper half
    assert page.locator("#landing-title").evaluate("e => getComputedStyle(e).letterSpacing").startswith("-")
    assert page.locator("#viewport").bounding_box()["height"] == 800                             # the hero is exactly one screen
    assert page.locator("#metrics").bounding_box()["y"] >= 800                                   # and the metrics screen follows it
    assert page.locator("#viewport").evaluate("e => getComputedStyle(e).overflow") == "hidden"


def test_the_landing_nav_is_a_black_pill_and_the_menu_opens_on_a_phone(browser, server):
    pg = new_page(browser, server)
    settled(pg)
    nav = pg.locator("#l-nav")
    assert nav.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(11, 11, 12)"
    box = nav.bounding_box()
    assert float(nav.evaluate("e => parseFloat(getComputedStyle(e).borderTopLeftRadius)")) >= box["height"] / 2 - 1
    assert pg.locator("#nav-upload").is_visible() and pg.locator("#nav-cta").is_visible() and pg.locator("#burger").is_hidden()
    pg.ctx.close()
    pg = new_page(browser, server, 390, 844)
    settled(pg)
    assert pg.locator("#nav-upload").is_hidden() and pg.locator("#burger").is_visible()
    assert pg.locator("#burger").get_attribute("aria-expanded") == "false"
    pg.click("#burger")
    expect(pg.locator("#menu-cta")).to_be_visible()
    assert pg.locator("#burger").get_attribute("aria-expanded") == "true" and pg.locator("#burger").get_attribute("aria-label") == "Close menu"
    pg.keyboard.press("Escape")
    expect(pg.locator("#burger")).to_have_attribute("aria-expanded", "false")
    assert pg.evaluate("document.activeElement.id") == "burger"
    pg.click("#burger")
    pg.click("#menu-upload")
    expect(pg.locator("#view-upload")).to_be_visible()
    assert pg.errors == [] and pg.console_errors == []
    pg.ctx.close()


def test_the_metrics_screen_shows_three_real_figures_in_led_dots_and_explains_each(page):
    settled(page)
    page.locator("#metrics").scroll_into_view_if_needed()
    assert page.locator("#mx-title").inner_text().replace("\n", " ").split() == ["Built", "for", "Claim", "Review"]   # the dot word is an image
    assert page.locator("#mx-dots").get_attribute("aria-label") == "Intelligent" and page.locator("#mx-dots circle").count() > 60
    cards = page.locator(".mx-card")
    assert cards.count() == 3
    assert [page.locator(f"#mx-c{i}-num").get_attribute("aria-label") for i in (1, 2, 3)] == ["8", "186", "68"]
    assert [page.locator(f"#mx-c{i}-unit").inner_text() for i in (1, 2, 3)] == ["checks", "clauses", "items"]
    assert page.locator("#gaugeTicks line").count() == 23
    widths = [c.bounding_box()["width"] for c in cards.all()]
    assert max(widths) - min(widths) < 1 and all(abs(c.bounding_box()["width"] / c.bounding_box()["height"] - 429 / 554) < 0.01 for c in cards.all())
    btn = cards.nth(1).locator(".learn-more")
    expect(btn).to_be_visible(timeout=5000)
    assert btn.inner_text() == "Learn more" and btn.get_attribute("aria-expanded") == "false" and page.locator("#mx-c2-detail").is_hidden()
    btn.click()
    expect(page.locator("#mx-c2-detail")).to_be_visible()
    assert "Azure AI Search" in page.locator("#mx-c2-detail").inner_text()
    assert btn.get_attribute("aria-expanded") == "true" and btn.inner_text() == "Close"
    btn.click()
    expect(page.locator("#mx-c2-detail")).to_be_hidden()


def test_a_loading_card_shows_while_documents_are_read(page):
    to_upload(page)
    hold, holding = [], [True]
    page.route("**/documents", lambda route: hold.append(route) if holding[0] else route.continue_())
    page.set_input_files("#picker", [str(d) for d in DOCS[:3]])
    loader = page.locator("#loader")
    expect(loader).to_be_visible()
    assert page.locator("#loader-title").inner_text() == "Reading your documents" and page.locator("#loader-sub").inner_text() == "0 of 3 read"
    assert loader.get_attribute("role") == "status" and loader.evaluate("e => getComputedStyle(e).pointerEvents") == "none"
    assert "blur" in page.locator(".loader-card").evaluate("e => getComputedStyle(e).backdropFilter")
    holding[0] = False
    for r in hold:
        r.continue_()
    expect(loader).to_be_hidden(timeout=15000)


def test_the_primary_button_is_a_black_pill(page):
    settled(page)
    b = page.locator("#landing-start")
    box = b.bounding_box()
    assert b.evaluate("e => e.offsetHeight") == 44                        # in design pixels: the desktop stage is scaled to the window
    assert b.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(11, 11, 12)"
    assert b.evaluate("e => getComputedStyle(e).color") == "rgb(255, 255, 255)"
    assert float(b.evaluate("e => parseFloat(getComputedStyle(e).borderTopLeftRadius)")) >= box["height"] / 2


def test_the_logo_is_on_every_view_and_links_home(page):
    assert page.locator("#logo-word").inner_text() == "ARC"
    assert page.locator("#logo .logo-mark path").get_attribute("stroke") == "#38C6EC"
    assert round(page.locator("#logo .logo-mark").bounding_box()["height"]) >= 24
    to_upload(page)
    logo = page.locator("#app-logo")
    assert logo.is_visible() and page.locator("#appbar").evaluate("e => getComputedStyle(e).position") == "fixed"
    box = logo.bounding_box()
    assert box["x"] < 40 and box["y"] < 40                                                      # top left
    to_chat(page)
    assert page.locator("#app-logo").is_visible()
    page.click("#app-logo")
    expect(page.locator("#view-landing")).to_be_visible()
    assert page.locator("#appbar").is_hidden()


# ---------------------------------------------------------------- 2. the three paths
def test_each_view_has_its_own_path_and_the_back_button_works(page, server):
    assert page.url == server.url + "/"
    to_upload(page)
    assert page.url == server.url + "/upload" and page.title() == "ARC - your documents"
    page.go_back()
    expect(page.locator("#view-landing")).to_be_visible()
    assert page.url == server.url + "/"


def test_chat_without_a_ready_claim_redirects_to_upload(browser, server):
    pg = new_page(browser, server, path="/chat")
    expect(pg.locator("#view-upload")).to_be_visible()
    assert pg.url == server.url + "/upload"
    assert pg.errors == []
    pg.ctx.close()


def test_a_refresh_on_chat_keeps_the_conversation(page, server):
    to_chat(page)
    ask(page, "how much will be paid")
    before = page.locator(".card").all_inner_texts()
    page.reload()
    expect(page.locator("#view-chat")).to_be_visible()
    assert page.url == server.url + "/chat"
    expect(page.locator(".card")).to_have_count(len(before))
    assert page.locator(".card").all_inner_texts() == before
    assert page.locator("#tools").is_visible()


def test_the_page_is_served_from_the_path_itself(server):
    import urllib.request
    for path in ("/", "/upload", "/chat"):
        with urllib.request.urlopen(server.url + path) as r:
            assert r.status == 200 and b"<title>ARC</title>" in r.read()


# ---------------------------------------------------------------- 3. upload
def test_the_upload_view_is_one_line_and_keyboard_operable(page):
    to_upload(page)
    assert page.inner_text("#drop-line") == "Drop your documents here, or click to upload"
    assert page.locator("h1:visible").inner_text() == "Add your claim documents"
    assert page.locator("#picker").get_attribute("accept") == "application/pdf,.pdf" and page.locator("#picker").get_attribute("multiple") is not None
    box = page.locator("#drop").bounding_box()
    assert abs((box["x"] + box["width"] / 2) - 640) < 2 and box["width"] == 600
    assert tab_to(page, "drop", 8)
    assert page.locator("#drop").evaluate("e => e.matches(':focus-visible') && parseFloat(getComputedStyle(e).outlineWidth) > 0")
    for key in ("Enter", " "):
        with page.expect_file_chooser(timeout=3000) as fc:
            page.keyboard.press(key)
        assert fc.value.is_multiple()


def test_twelve_files_in_one_drop_are_all_processed_in_batches_of_five(page, tmp_path):
    files = list(DOCS)
    for i in range(2):
        f = tmp_path / f"extra{i}.pdf"
        f.write_bytes(DOCS[0].read_bytes() + b"\n%" + bytes([65 + i]))
        files.append(f)
    assert len(files) == 12
    to_chat(page, files)
    posts = [u for u in page.requests if u.endswith("/documents")]
    assert len(posts) == 3
    assert "15 files" not in page.inner_text("body") and "up to 15" not in page.inner_text("body")


def test_file_rows_and_plain_reasons_when_the_documents_need_attention(page, tmp_path):
    bad = tmp_path / "notes.pdf"
    bad.write_bytes(b"this is not a pdf at all, just some text of enough length to be read")
    to_upload(page)
    page.set_input_files("#picker", [str(bad), str(next(d for d in DOCS if d.name == "claim_form.pdf"))])
    expect(page.locator("#files li")).to_have_count(2)
    expect(page.locator("#reasons")).not_to_be_empty(timeout=15000)
    expect(page.locator("#files .fstatus.is-ready")).to_have_count(1)
    names, states = page.locator("#files .fname").all_inner_texts(), page.locator("#files .fstatus").all_inner_texts()
    assert names == ["notes.pdf", "claim_form.pdf"] and states == ["This isn't a PDF. Please upload PDF files for now.", "ready"]
    assert page.locator("#files .fstatus").first.get_attribute("class") == "fstatus is-bad"
    assert page.locator("#view-chat").is_hidden() and page.locator("#drop").is_visible()
    others = [d for d in DOCS if d.name != "claim_form.pdf"]
    page.set_input_files("#picker", [str(d) for d in others])            # the section stays active: add the rest and intake runs again
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)


def test_drag_over_tints_the_drop_section_and_dropping_uploads(page):
    to_upload(page)
    dt = drop_files(page, [(d.name, d.read_bytes()) for d in DOCS], "#drop")
    assert "over" in page.locator("#drop").get_attribute("class")
    page.wait_for_timeout(250)                                   # the tint fades in over 150 ms
    assert page.locator("#drop").evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(234, 248, 253)"
    assert page.locator("#drop").evaluate("e => getComputedStyle(e).borderTopStyle") == "solid"
    page.dispatch_event("#drop", "drop", {"dataTransfer": dt})
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)


def test_the_sample_link_on_the_upload_view_works_too(page):
    to_upload(page)
    page.click("#upload-sample")
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)
    assert page.locator(".card").first.inner_text().startswith("Rohan Verma, here's what I've read")


def test_the_sample_link_loads_a_set_through_the_same_pipeline(page):
    page.click("#landing-sample")
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)
    assert page.locator(".card").first.inner_text().startswith("Rohan Verma, here's what I've read")
    assert "10 Sep 2026" in page.locator(".card").first.inner_text()          # the on_time set


# ---------------------------------------------------------------- 4. chat
def test_the_first_message_says_what_was_read_the_dates_the_gap_and_the_report(page):
    to_chat(page)
    first = page.locator(".card").first.inner_text()
    for part in ("Rohan Verma, here's what I've read", "bill ₹1,84,500", "Your policy runs from 15 Mar 2026 to 14 Mar 2027",
                 "was in force on the admission date", "inside the 30 days", "One document is still missing (9 of 10 received)",
                 "I've also prepared a report you can download for your insurer (top right)",
                 "Amounts I give are estimates; your insurer's team makes the final decision."):
        assert part in first, part
    assert "officer" not in first.lower() and "|" not in first


def test_cards_have_no_labels_arc_carries_its_mark_and_the_customer_card_is_tinted(page):
    to_chat(page)
    ask(page, "how much will be paid")
    cards = page.locator(".card")
    assert cards.count() == 3
    first, me, reply = cards.nth(0), cards.nth(1), cards.nth(2)
    for c in (first, reply):
        assert c.locator(".who, .label, .avatar, time").count() == 0
        assert c.evaluate("e => getComputedStyle(e, '::before').backgroundColor") == "rgb(11, 11, 12)"        # ARC's mark, drawn in CSS
        assert c.get_attribute("aria-label") == "ARC"
    assert me.evaluate("e => getComputedStyle(e, '::before').content") == "none"
    assert me.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(244, 244, 245)" and me.inner_text() == "how much will be paid"
    assert me.evaluate("e => getComputedStyle(e).alignSelf") == "flex-end"
    assert page.locator("#thread").get_attribute("role") == "log" and page.locator("#thread").get_attribute("aria-live") == "polite"
    assert reply.locator("strong").first.inner_text() == "₹1,22,125"


def test_a_table_in_a_reply_is_rendered_with_right_aligned_numbers(page):
    to_chat(page)
    ask(page, "explain my claim")            # the stand-in's second reply carries a table
    for _ in range(3):
        if page.locator(".card table").count():
            break
        ask(page, "explain my claim")
    table = page.locator(".card table").first
    assert table.count() == 1
    assert table.locator("td.md-right").count() >= 3
    assert table.evaluate("e => getComputedStyle(e).fontVariantNumeric").find("tabular-nums") >= 0
    assert page.locator(".card .md-table").first.evaluate("e => getComputedStyle(e).overflowX") == "auto"


def test_the_composer_is_fixed_round_and_has_one_send_button(page):
    to_chat(page)
    for q in ("one", "two", "three", "four"):
        ask(page, q)
    assert page.evaluate("document.documentElement.scrollHeight") > 900, "the page must scroll for this test"
    bar = page.locator("#bar")
    assert bar.evaluate("e => getComputedStyle(e).position") == "fixed"
    gap = lambda: page.evaluate("innerHeight") - bar.bounding_box()["y"] - bar.bounding_box()["height"]   # noqa: E731
    page.evaluate("scrollTo(0, 0)")
    top_gap = gap()
    page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
    assert abs(top_gap - gap()) < 0.6 and abs(top_gap - 20) < 0.6
    box = bar.bounding_box()
    assert box["height"] == 60 and box["width"] == 728 and abs(box["x"] + box["width"] / 2 - 640) < 1
    assert float(bar.evaluate("e => parseFloat(getComputedStyle(e).borderTopLeftRadius)")) >= 20
    send = page.locator("#send")
    assert send.get_attribute("aria-label") == "Send" and send.inner_text().strip() == "" and send.locator("svg").count() == 1
    assert send.evaluate("e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }") == [42, 42]
    assert page.locator("#attach").get_attribute("aria-label") == "Add documents"
    assert page.evaluate("(() => { const a = document.querySelector('#bar').getBoundingClientRect(), s = document.querySelector('#send').getBoundingClientRect();"
                         " return s.right <= a.right && s.left >= a.left; })()")
    assert page.locator("#ask").evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 16      # iOS does not zoom into a 16px input


def test_starting_questions_ask_for_real_and_then_go_away(page):
    to_chat(page)
    chips = page.locator("#chips .chip")
    assert chips.all_inner_texts() == ["How much will be paid?", "What's still missing?", "Explain the deductions"]
    chips.first.click()
    expect(page.locator(".dots")).to_have_count(0, timeout=10000)
    assert page.locator(".card.me").inner_text() == "How much will be paid?"
    assert page.locator("#chips").count() == 0 and page.locator(".card").count() == 3


def test_the_send_button_shows_a_running_light_while_arc_works(page):
    to_chat(page)
    hold = []
    page.route("**/chat", lambda route: hold.append(route))
    page.fill("#ask", "hello")
    page.keyboard.press("Enter")
    expect(page.locator("#bar")).to_have_class(re.compile(r"\bis-pending\b"))
    assert page.locator("#send").evaluate("e => getComputedStyle(e, '::before').animationName") == "spin"
    hold[0].abort()
    expect(page.locator("#bar")).not_to_have_class(re.compile(r"\bis-pending\b"))


def test_send_is_disabled_when_empty_or_pending_and_focus_stays(page):
    to_chat(page)
    send = page.locator("#send")
    expect(send).to_be_disabled()
    assert send.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(228, 228, 231)"
    page.fill("#ask", "hello")
    expect(send).to_be_enabled()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "ask"
    expect(page.locator(".dots")).to_have_count(0)
    assert page.evaluate("document.activeElement.id") == "ask"


def test_the_top_of_a_reply_is_scrolled_into_view(page):
    to_chat(page)
    for q in ("one", "two", "three", "four"):
        ask(page, q)
    top = page.locator(".card").last.bounding_box()["y"]
    assert 0 <= top < 800 - 68        # visible above the bar


def test_waiting_state_is_an_ordinary_card_with_three_dots(page):
    to_chat(page)
    hold = []
    page.route("**/chat", lambda route: hold.append(route))
    page.fill("#ask", "hello")
    page.keyboard.press("Enter")
    expect(page.locator(".dots")).to_have_count(1)
    assert page.locator(".dots i").count() == 3 and page.locator(".dots").locator("xpath=..").get_attribute("class") == "card"
    assert page.locator(".dots").get_attribute("aria-label") == "Working on it"
    hold[0].abort()
    expect(page.locator(".card").last).to_have_text("That took too long. Please try again.")


def drop_files(page, names_and_bytes, target="body"):
    dt = page.evaluate_handle("""(items) => { const d = new DataTransfer();
      for (const [n, b] of items) d.items.add(new File([new Uint8Array(b)], n, {type: 'application/pdf'})); return d; }""", [[n, list(b)] for n, b in names_and_bytes])
    page.dispatch_event(target, "dragenter", {"dataTransfer": dt})
    return dt


def test_dropping_on_the_chat_view_adds_documents_with_an_overlay(page):
    to_chat(page)
    dt = drop_files(page, [(DOCS[0].name, DOCS[0].read_bytes())])
    overlay = page.locator("#overlay")
    expect(overlay).to_be_visible(timeout=5000)
    page.wait_for_timeout(100)
    assert overlay.inner_text() == "Drop to add documents" and overlay.evaluate("e => getComputedStyle(e).position") == "fixed"
    page.dispatch_event("body", "drop", {"dataTransfer": dt})
    expect(overlay).to_be_hidden()
    expect(page.locator(".card")).to_have_count(2, timeout=30000)
    assert page.locator(".card").last.inner_text().startswith("Rohan Verma, here's what I've read")
    assert "estimates; your insurer's team" not in page.locator(".card").last.inner_text()      # said once, in the first message


# ---------------------------------------------------------------- 5. the report and starting over
def test_the_tools_appear_only_on_the_chat_view_once_the_claim_is_ready(page):
    assert page.locator("#tools").is_hidden()
    to_upload(page)
    assert page.locator("#tools").is_hidden()
    to_chat(page)
    tools = page.locator("#tools")
    assert tools.is_visible() and page.locator("#appbar").evaluate("e => getComputedStyle(e).position") == "fixed"
    box = tools.bounding_box()
    assert box["y"] < 60 and box["x"] + box["width"] > 1280 - 40
    assert page.locator("#report-label").inner_text() == "Download report" and page.locator("#restart-label").inner_text() == "Start over"
    assert page.locator("#report svg").count() == 1 and page.locator("#restart svg").count() == 1


def test_the_report_downloads_as_a_pdf(page):
    to_chat(page)
    with page.expect_download(timeout=30000) as info:
        page.click("#report")
    download = info.value
    assert download.suggested_filename == "ARC-claim-report-CLM-20260910-0001.pdf"
    path = download.path()
    assert path and open(path, "rb").read(4) == b"%PDF"
    expect(page.locator("#report-label")).to_have_text("Download report")
    assert page.locator("#tool-note").inner_text() == ""


def test_a_failed_report_says_so_in_one_plain_line(page):
    to_chat(page)
    page.route("**/report.pdf", lambda route: route.abort())
    page.click("#report")
    expect(page.locator("#tool-note")).to_have_text("The report couldn't be prepared. Please try again.")
    expect(page.locator("#report-label")).to_have_text("Download report")


def test_start_over_confirms_once_then_deletes_and_returns_to_upload(page, server):
    to_chat(page)
    page.click("#restart")
    expect(page.locator("#restart-label")).to_have_text("Delete everything?")
    deleted = []
    page.on("request", lambda r: deleted.append(r.method + " " + r.url) if r.method == "DELETE" else None)
    page.click("#restart")
    expect(page.locator("#view-upload")).to_be_visible(timeout=10000)
    assert page.url == server.url + "/upload" and page.locator("#tools").is_hidden()
    assert any(d.startswith("DELETE ") for d in deleted)
    assert page.locator(".card").count() == 0
    page.reload()                                   # the session is gone, so a refresh cannot bring the chat back
    expect(page.locator("#view-upload")).to_be_visible()
    assert page.locator(".card").count() == 0 and page.locator("#tools").is_hidden()
    page.goto(server.url + "/chat")                 # and /chat sends the customer back to the upload view
    expect(page.locator("#view-upload")).to_be_visible()


# ---------------------------------------------------------------- 6. accessibility, colours, weight
AXE_RULES = {"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}}


def axe_violations(page):
    if not AXE.exists():
        pytest.skip("axe-core is not vendored in tests/helpers")
    page.wait_for_timeout(350)              # a card fades in over 200 ms: measure the colours once it has arrived
    page.evaluate(AXE.read_text(encoding="utf-8"))
    out = page.evaluate("(opts) => axe.run(document, opts).then(r => r.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length})))", AXE_RULES)
    return [v for v in out if v["impact"] in ("serious", "critical")]


def test_every_view_passes_axe_core_with_no_serious_issue(page):
    settled(page)
    assert axe_violations(page) == [], "landing"
    to_upload(page)
    assert axe_violations(page) == [], "upload"
    to_chat(page)
    ask(page, "how much will be paid")
    assert axe_violations(page) == [], "chat"


SCAN = """() => {
  const bad = [];
  const rgb = (c) => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).map(Number);
    if (p.length > 3 && p[3] === 0) return null; return p.slice(0, 3); };
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getClientRects(); const cs = getComputedStyle(e);
    if (!r.length || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const props = [['color', cs.color], ['background', cs.backgroundColor], ['fill', cs.fill], ['stroke', cs.stroke]];
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) if (parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none') props.push(['border', cs['border' + side + 'Color']]);
    for (const [k, v] of props) { const c = rgb(v); if (c) bad.push([e.tagName + '.' + e.className, k, c]); }
  }
  return bad;
}"""


def saturation(rgb):
    r, g, b = (x / 255 for x in rgb)
    return colorsys.rgb_to_hls(r, g, b)[2]


def assert_only_the_palette(page):
    for tag, kind, rgb in page.evaluate(SCAN):
        if tuple(rgb) in ALLOWED:
            continue
        assert saturation(rgb) < 0.15, (tag, kind, rgb)


def test_no_colour_beyond_the_palette_on_any_view(page):
    settled(page)
    assert_only_the_palette(page)
    to_upload(page)
    assert_only_the_palette(page)
    to_chat(page)
    ask(page, "how much will be paid")
    assert_only_the_palette(page)


def test_only_same_origin_requests_and_no_console_error(page, server):
    to_chat(page)
    ask(page, "hello")
    assert page.requests and all(u.startswith(server.url) or u.startswith("data:") or u.startswith("blob:") for u in page.requests)


def test_the_page_weight_without_the_font_is_under_300kb(page, server):
    sizes = {}
    for name in ("index.html", "static/app.css", "static/app.js", "static/md.js", "static/enter.js", "static/logo.svg"):
        sizes[name] = len((ROOT / "app" / (name if name.startswith("static") else "static/index.html")).read_bytes()) if name == "index.html" else len((ROOT / "app" / name).read_bytes())
    total = sum(sizes.values())
    assert total < 300 * 1024, sizes
    assert (ROOT / "app" / "static" / "fonts" / "InterVariable.woff2").exists()


@pytest.mark.parametrize("width,height", [(360, 740), (390, 844), (768, 1024), (1280, 800), (1920, 1080)])
def test_the_views_work_from_360_to_1920(browser, server, width, height):
    pg = new_page(browser, server, width, height)
    for step in ("landing", "upload", "chat"):
        if step == "upload":
            pg.click("#landing-start")
        if step == "chat":
            pg.set_input_files("#picker", [str(f) for f in DOCS])
            expect(pg.locator("#view-chat")).to_be_visible(timeout=20000)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (step, width)
        assert pg.locator("#logo" if step == "landing" else "#app-logo").is_visible()
        if step != "landing":                                     # nothing in the top bar runs off the screen
            assert pg.evaluate("[...document.querySelectorAll('#appbar *')].every(e => e.getBoundingClientRect().right <= innerWidth + 0.5)"), (step, width)
    box = pg.locator("#bar").bounding_box()
    assert box["x"] >= 8 and box["x"] + box["width"] <= width - 8
    assert pg.errors == [] and pg.console_errors == []
    pg.ctx.close()


def test_the_whole_flow_works_from_the_keyboard_only(browser, server):
    pg = new_page(browser, server)
    assert tab_to(pg, "logo", 2)                   # the logo is the first stop
    assert tab_to(pg, "landing-start", 5)          # then the nav links, then the primary button
    pg.keyboard.press("Enter")
    expect(pg.locator("#view-upload")).to_be_visible()
    assert tab_to(pg, "drop", 8)
    pg.set_input_files("#picker", [str(f) for f in DOCS])          # the file chooser itself is the browser's own dialog
    expect(pg.locator("#view-chat")).to_be_visible(timeout=20000)
    assert pg.evaluate("document.activeElement.id") == "ask"
    pg.keyboard.type("how much will be paid")
    pg.keyboard.press("Enter")
    expect(pg.locator(".dots")).to_have_count(0, timeout=10000)
    assert pg.locator(".card").count() == 3
    order = pg.evaluate("""() => { const ids = []; for (const e of document.querySelectorAll('a, button, input, [tabindex]')) {
        if (e.offsetParent !== null && !e.disabled) ids.push(e.id || e.tagName); } return ids; }""")
    assert order[0] == "app-logo" and "ask" in order and "report" in order and "restart" in order and "attach" in order
    assert pg.errors == [] and pg.console_errors == []
    pg.ctx.close()


def test_the_reduced_motion_preference_is_respected(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto(server.url + "/")
    pg.click("#landing-start")
    pg.set_input_files("#picker", [str(f) for f in DOCS])
    expect(pg.locator("#view-chat")).to_be_visible(timeout=20000)
    assert pg.locator(".card").first.evaluate("e => getComputedStyle(e).animationName") == "none"
    assert pg.locator("#view-chat").evaluate("e => getComputedStyle(e).transitionDuration") in ("0s", "0s, 0s")
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css and "animation: none !important" in css
    ctx.close()
