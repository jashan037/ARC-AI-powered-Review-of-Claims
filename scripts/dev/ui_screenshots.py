"""Screenshots of the customer page in every state, at 1280x800 and 390x844 -> docs/screenshots/ui_final/. Synthetic documents and a stand-in agent with fixed replies; no Azure.

    python scripts/dev/ui_screenshots.py
"""
import os
import sys
from pathlib import Path

os.environ["RETRIEVER"], os.environ["AGENT_MODE"], os.environ["CORS_ORIGINS"] = "local", "offline", ""
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from tests.helpers.uiserver import Server  # noqa: E402

OUT = ROOT / "docs" / "screenshots" / "ui_final"
DOCS = sorted((ROOT / "demo" / "samples" / "on_time").glob("*.pdf"))
SIZES = {"1280x800": (1280, 800), "390x844": (390, 844)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    server = Server().start()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for label, (w, h) in SIZES.items():
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            shot = lambda name: page.screenshot(path=str(OUT / f"{name}_{label}.png"))   # noqa: E731
            page.goto(server.url + "/")
            shot("1_upload_empty")

            held, hold = [], [True]
            page.route("**/documents", lambda route: held.append(route) if hold[0] else route.continue_())
            page.set_input_files("#picker", [str(d) for d in DOCS[:6]])
            expect(page.locator("#files li")).to_have_count(6)
            shot("2_upload_processing")
            hold[0] = False
            for r in held:
                r.continue_()
            expect(page.locator("#files li").first).to_have_text(f"{DOCS[0].name}, ready", timeout=15000)   # ready or the plain reason
            page.wait_for_timeout(600)

            page.reload()
            page.set_input_files("#picker", [str(next(d for d in DOCS if d.name == "claim_form.pdf"))])
            expect(page.locator("#reasons")).not_to_be_empty(timeout=15000)
            shot("3_needs_attention")

            page.set_input_files("#picker", [str(d) for d in DOCS if d.name != "claim_form.pdf"])
            expect(page.locator("#view-chat")).to_be_visible(timeout=20000)
            page.wait_for_timeout(400)
            shot("4_chat_first_message")

            for q in ("how much will be paid", "why was my room rent reduced", "when does my policy expire", "what is my address"):
                page.fill("#ask", q)
                page.keyboard.press("Enter")
                expect(page.locator(".dots")).to_have_count(0, timeout=10000)
            page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(200)
            shot("5_chat_four_exchanges_bottom")
            page.evaluate("scrollTo(0, 0)")
            page.wait_for_timeout(200)
            shot("6_chat_four_exchanges_top")

            dt = page.evaluate_handle("() => { const d = new DataTransfer(); d.items.add(new File(['x'], 'a.pdf', {type: 'application/pdf'})); return d; }")
            page.dispatch_event("body", "dragenter", {"dataTransfer": dt})
            page.wait_for_timeout(200)
            shot("7_chat_drag_over")
            ctx.close()
        browser.close()
    server.stop()
    print("written", len(list(OUT.glob('*.png'))), "screenshots to", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
