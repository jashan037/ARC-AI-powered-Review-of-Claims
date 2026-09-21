"""The customer page in a real browser (Playwright with the installed Chrome; skipped without them). Offline: a stand-in agent with fixed replies, no Azure."""
import colorsys

import pytest

from app.config import ROOT
from app.tools.canned import NOT_READY  # noqa: F401  (imported so the module fails loudly if the fixed message moves)

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from tests.helpers.uiserver import Server  # noqa: E402

DOCS = sorted((ROOT / "demo" / "samples" / "on_time").glob("*.pdf"))
TINT, FOCUS, TEXT = (238, 243, 246), (201, 214, 221), (31, 41, 51)   # the allowed non-neutral colours: --tint, the focus border, and the --text token


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


@pytest.fixture
def page(server, browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.requests = []
    pg.on("request", lambda r: pg.requests.append(r.url))
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(server.url + "/")
    yield pg
    assert pg.errors == []
    ctx.close()


def to_chat(page, files=DOCS):
    page.set_input_files("#picker", [str(f) for f in files])
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)


def ask(page, text):
    page.fill("#ask", text)
    page.keyboard.press("Enter")
    expect(page.locator("#send")).to_be_disabled()
    expect(page.locator(".dots")).to_have_count(0, timeout=10000)


# ---------------------------------------------------------------- structure
BANNED_TAGS = "header, footer, nav, aside, h1, h2, h3, h4, h5, h6"
BANNED_TEXT = ["Show more", "Add a document", "Demo:", "claims officer", "tool", "trace", "chunk"]


def test_upload_view_is_the_wordmark_and_one_line(page):
    assert page.locator(BANNED_TAGS).count() == 0
    assert page.inner_text("body").split() == ["ARC"] + "Drop your documents here, or click to upload".split()
    assert page.locator("#sample:visible").count() == 0 and page.locator("#picker").get_attribute("accept") == "application/pdf,.pdf" and page.locator("#picker").get_attribute("multiple") is not None
    wm = page.locator(".wordmark")
    assert wm.evaluate("e => getComputedStyle(e).position") == "absolute" and wm.evaluate("e => getComputedStyle(e).top") == "20px" and wm.evaluate("e => getComputedStyle(e).left") == "24px"
    box = page.locator("#drop").bounding_box()
    assert abs((box["x"] + box["width"] / 2) - 640) < 2 and abs((box["y"] + box["height"] / 2) - 400) < 60   # centred


def test_chat_view_has_no_chrome_and_no_banned_text(page):
    to_chat(page)
    ask(page, "how much will be paid")
    assert page.locator(BANNED_TAGS).count() == 0
    body = page.inner_text("body")
    for word in BANNED_TEXT:
        assert word.lower() not in body.lower(), word
    assert "Amounts I give are estimates; your insurer's team makes the final decision." in body
    assert body.count("estimates; your insurer's team") == 1


def test_only_same_origin_requests(page, server):
    to_chat(page)
    ask(page, "hello")
    assert page.requests and all(u.startswith(server.url) or u.startswith("data:") for u in page.requests)


def test_sample_link_only_with_the_query(page, server):
    page.goto(server.url + "/?sample=1")
    link = page.get_by_role("link", name="Use sample documents")
    expect(link).to_be_visible()
    link.click()
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)
    page.goto(server.url + "/")
    assert page.get_by_role("link", name="Use sample documents").count() == 0 or not page.get_by_role("link", name="Use sample documents").is_visible()


# ---------------------------------------------------------------- no colours
SCAN = """() => {
  const bad = [];
  const sat = (c) => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).map(Number);
    if (p.length > 3 && p[3] === 0) return null; return p.slice(0, 3); };
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getClientRects(); const cs = getComputedStyle(e);
    if (!r.length || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const props = [['color', cs.color], ['background', cs.backgroundColor]];
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) if (parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none') props.push(['border', cs['border' + side + 'Color']]);
    for (const [k, v] of props) { const rgb = sat(v); if (rgb) bad.push([e.tagName + '.' + e.className, k, rgb]); }
  }
  return bad;
}"""


def saturation(rgb):
    r, g, b = (x / 255 for x in rgb)
    return colorsys.rgb_to_hls(r, g, b)[2]


def assert_no_colours(page):
    for tag, kind, rgb in page.evaluate(SCAN):
        if tuple(rgb) in (TINT, FOCUS, TEXT):
            continue
        assert saturation(rgb) < 0.15, (tag, kind, rgb)


def test_no_colours_on_the_upload_view(page):
    assert_no_colours(page)
    page.locator("#drop").focus()
    assert_no_colours(page)


def test_no_colours_on_the_chat_view(page):
    to_chat(page)
    ask(page, "how much will be paid")
    page.locator("#ask").focus()
    assert_no_colours(page)


def test_no_colours_when_files_need_attention(page):
    page.set_input_files("#picker", [str(next(d for d in DOCS if d.name == "claim_form.pdf"))])
    expect(page.locator("#reasons")).not_to_be_empty(timeout=15000)
    assert_no_colours(page)


# ---------------------------------------------------------------- composer and cards
def test_composer_is_fixed_round_and_has_one_send_button(page):
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
    assert box["height"] == 48 and box["width"] == 560 and abs(box["x"] + box["width"] / 2 - 640) < 1
    assert float(bar.evaluate("e => parseFloat(getComputedStyle(e).borderTopLeftRadius)")) >= box["height"] / 2
    buttons = page.locator("button:visible")
    assert buttons.count() == 1
    b = buttons.first
    assert b.get_attribute("aria-label") == "Send" and b.inner_text().strip() == "" and b.locator("svg").count() == 1
    assert b.evaluate("e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }") == [36, 36]
    inside = page.evaluate("(() => { const a = document.querySelector('#bar').getBoundingClientRect(), s = document.querySelector('#send').getBoundingClientRect(); return s.right <= a.right && s.left >= a.left; })()")
    assert inside


def test_send_is_disabled_when_empty_or_pending_and_focus_stays(page):
    to_chat(page)
    send = page.locator("#send")
    expect(send).to_be_disabled()
    page.fill("#ask", "hello")
    expect(send).to_be_enabled()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "ask"
    expect(page.locator(".dots")).to_have_count(0)
    assert page.evaluate("document.activeElement.id") == "ask"


def test_cards_have_no_labels_and_the_customer_card_is_tinted(page):
    to_chat(page)
    ask(page, "how much will be paid")
    cards = page.locator(".card")
    assert cards.count() == 3
    first, me, reply = cards.nth(0), cards.nth(1), cards.nth(2)
    assert first.inner_text().startswith("Rohan Verma, here's what I've read") and reply.inner_text().startswith("Your claim looks likely")
    for c in (first, reply):
        assert c.locator(".who, .label, .avatar, button, time").count() == 0
        assert c.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(255, 255, 255)"
    assert me.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(238, 243, 246)" and me.inner_text() == "how much will be paid"
    assert me.evaluate("e => getComputedStyle(e).alignSelf") == "flex-end"
    assert reply.locator("strong").inner_text() == "₹1,22,125"


def test_the_top_of_a_reply_is_scrolled_into_view(page):
    to_chat(page)
    for q in ("one", "two", "three", "four"):
        ask(page, q)
    top = page.locator(".card").last.bounding_box()["y"]
    assert 0 <= top < 800 - 68        # visible above the bar (a short reply at the very end of the page cannot be scrolled higher)


def test_waiting_state_is_an_ordinary_card_with_three_dots(page, server):
    to_chat(page)
    hold = []
    page.route("**/chat", lambda route: hold.append(route))
    page.fill("#ask", "hello")
    page.keyboard.press("Enter")
    expect(page.locator(".dots")).to_have_count(1)
    assert page.locator(".dots i").count() == 3 and page.locator(".dots").locator("xpath=..").get_attribute("class") == "card"
    hold[0].abort()
    expect(page.locator(".card").last).to_have_text("That took too long. Please try again.")


# ---------------------------------------------------------------- upload
def test_twelve_files_in_one_drop_are_all_processed_in_batches_of_five(page, server, tmp_path):
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


def test_file_rows_and_reasons_in_the_upload_view(page, tmp_path):
    bad = tmp_path / "notes.pdf"
    bad.write_bytes(b"this is not a pdf at all, just some text of enough length to be read")
    page.set_input_files("#picker", [str(bad), str(next(d for d in DOCS if d.name == "claim_form.pdf"))])
    expect(page.locator("#files li")).to_have_count(2)
    expect(page.locator("#reasons")).not_to_be_empty(timeout=15000)
    rows = page.locator("#files li").all_inner_texts()
    assert rows[0] == "notes.pdf, This isn't a PDF. Please upload PDF files for now." and rows[1] == "claim_form.pdf, ready"
    assert page.locator("#view-chat").is_hidden() and page.locator("#drop").is_visible()
    others = [d for d in DOCS if d.name != "claim_form.pdf"]
    page.set_input_files("#picker", [str(d) for d in others])            # the section is still active: add the rest and intake runs again
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)


def test_the_drop_section_works_from_the_keyboard(page):
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "drop"
    assert page.locator("#drop").evaluate("e => e.matches(':focus-visible') && parseFloat(getComputedStyle(e).outlineWidth) > 0")
    for key in ("Enter", " "):
        with page.expect_file_chooser(timeout=3000) as fc:
            page.keyboard.press(key)
        assert fc.value.is_multiple()


def drop_files(page, names_and_bytes, target="body"):
    dt = page.evaluate_handle("""(items) => { const d = new DataTransfer();
      for (const [n, b] of items) d.items.add(new File([new Uint8Array(b)], n, {type: 'application/pdf'})); return d; }""", [[n, list(b)] for n, b in names_and_bytes])
    page.dispatch_event(target, "dragenter", {"dataTransfer": dt})
    return dt


def test_drag_over_highlights_the_drop_section_and_drop_uploads(page):
    dt = drop_files(page, [(d.name, d.read_bytes()) for d in DOCS], "#drop")
    assert "over" in page.locator("#drop").get_attribute("class")
    assert page.locator("#drop").evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(238, 243, 246)" and page.locator("#drop").evaluate("e => getComputedStyle(e).borderTopStyle") == "solid"
    page.dispatch_event("#drop", "drop", {"dataTransfer": dt})
    expect(page.locator("#view-chat")).to_be_visible(timeout=20000)


def test_dropping_on_the_chat_view_adds_documents_with_an_overlay(page):
    to_chat(page)
    dt = drop_files(page, [(DOCS[0].name, DOCS[0].read_bytes())])
    overlay = page.locator("#overlay")
    expect(overlay).to_be_visible()
    assert overlay.inner_text() == "Drop to add documents" and overlay.evaluate("e => getComputedStyle(e).position") == "fixed"
    page.dispatch_event("body", "drop", {"dataTransfer": dt})
    expect(overlay).to_be_hidden()
    expect(page.locator(".card")).to_have_count(2, timeout=15000)
    assert page.locator(".card").last.inner_text().startswith("Rohan Verma, here's what I've read")
    assert "estimates; your insurer's team" not in page.locator(".card").last.inner_text()
