#!/usr/bin/env python3
"""Playwright screenshots of the customer page: both screens, in a real browser (the Chrome that is already installed).

    scripts/run_demo.sh                                       # one terminal: the app on port 8765, real agent only
    python scripts/take_screenshots.py                        # another: writes demo/screenshots/01-...png
    python scripts/take_screenshots.py --base http://127.0.0.1:8790 --out demo/screenshots

It walks the flow a customer would: empty upload screen, the documents read, a problem with a document, the first chat message, an answer with its
suggestions, the same answer with "Show more" open, and a phone-sized view. Like the demo script it refuses the offline stand-in unless you pass
--allow-offline (for testing this script), because a screenshot of the stand-in would misrepresent ARC.
Needs:  pip install -r requirements-dev.txt
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.pdfmaker import edit  # noqa: E402

from playwright.sync_api import expect, sync_playwright  # noqa: E402

DOCS = ROOT / "demo" / "documents"
ALL = sorted(str(p) for p in DOCS.glob("*.pdf"))
WAIT_MS = 100000      # the real agent needs 15 to 25 seconds per answer; expect() has its own (short) default timeout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default=str(ROOT / "demo" / "screenshots"))
    ap.add_argument("--allow-offline", action="store_true")
    a = ap.parse_args()
    base, out = a.base.rstrip("/"), Path(a.out)
    try:
        health = json.load(urllib.request.urlopen(base + "/health", timeout=5))
    except Exception:  # noqa: BLE001
        sys.exit(f"Cannot reach {base}. Start it first with scripts/run_demo.sh")
    if not health.get("live") and not a.allow_offline:
        sys.exit("The server is the OFFLINE stand-in, not the real agent. Start it with scripts/run_demo.sh (or pass --allow-offline to test this script).")
    out.mkdir(parents=True, exist_ok=True)
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="arc-shots-"))                                    # the bill "in another name" for the problem screen; removed at the end
    wrong_bill = tmp / "hospital_bill.pdf"
    wrong_bill.write_bytes(edit((DOCS / "hospital_bill.pdf").read_bytes(), {"Rohan Verma": "Rohan Varma"}))
    saved = []

    def see(locator, text):
        expect(locator).to_contain_text(text, timeout=WAIT_MS)

    def shot(page, name, full=True):
        page.wait_for_timeout(350)
        path = out / name
        page.screenshot(path=str(path), full_page=full)
        saved.append(path)
        print("saved", path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1100, "height": 800})
        page = ctx.new_page()
        page.set_default_timeout(100000)
        try:
            # 1. the upload screen, empty
            page.goto(base + "/")
            expect(page.locator("#checklist li")).to_have_count(10)
            shot(page, "01-upload.png")

            # 2. after "Use sample documents": ticks, the missing prescription, the way in
            page.click("#use-sample")
            see(page.locator("#ready-text"), "We recognised 9 of 10 documents")
            shot(page, "02-upload-documents-read.png")

            # 3. a document that does not fit (a bill in another name): plain reasons and a way to replace it
            page.goto(base + "/")
            page.set_input_files("#file-input", [f for f in ALL if not f.endswith("hospital_bill.pdf")] + [str(wrong_bill)])
            expect(page.locator("#attention")).to_be_visible()
            shot(page, "03-upload-needs-attention.png")

            # 4. the chat: the first message is the claim summary, with three suggestions
            page.goto(base + "/")
            page.click("#use-sample")
            expect(page.locator("#ready")).to_be_visible()
            page.click("#continue")
            expect(page.locator("#thread .chip")).to_have_count(3)
            shot(page, "04-chat-first-message.png")

            # 5. an answer: a short summary, and three new suggestions under it
            page.get_by_role("button", name="How much will be paid?").click()
            see(page.locator("#thread .msg-arc").nth(1), "₹1,22,125")
            expect(page.locator("#thread .chip")).to_have_count(3, timeout=WAIT_MS)
            shot(page, "05-chat-answer.png")

            # 6. the same answer with "Show more" open and one policy reference expanded
            answer = page.locator("#thread .msg-arc").nth(1)
            answer.get_by_role("button", name="Show more").click()
            answer.locator(".ref-chip").nth(2).click()
            shot(page, "06-chat-show-more.png")
            answer.get_by_role("button", name="Show less").click()

            # 7. a follow-up from a suggestion
            page.get_by_role("button", name="Why was my room rent reduced?").click()
            see(page.locator("#thread .msg-arc").nth(2), "62.5%")
            shot(page, "07-chat-room-rent.png")
        finally:
            ctx.close()

        # 8. a phone
        phone = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        pp = phone.new_page()
        pp.set_default_timeout(100000)
        pp.goto(base + "/")
        pp.click("#use-sample")
        expect(pp.locator("#ready")).to_be_visible()
        pp.click("#continue")
        pp.get_by_role("button", name="How much will be paid?").click()
        see(pp.locator("#thread .msg-arc").nth(1), "₹1,22,125")
        shot(pp, "08-phone-chat.png", full=False)
        phone.close()
        browser.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nSaved {len(saved)} screenshots in {out}" + ("" if health.get("live") else "  (OFFLINE stand-in: test only, do not publish)"))


if __name__ == "__main__":
    main()
