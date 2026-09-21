"""Screenshots of all three views in every state, at 1280x800 and 390x844 -> docs/screenshots/final/, plus the first page of each sample report.

Synthetic documents and a stand-in agent with fixed replies; nothing here talks to Azure.

    python scripts/dev/ui_screenshots.py
"""
import datetime as dt
import os
import subprocess
import shutil
import sys
from pathlib import Path

os.environ["RETRIEVER"], os.environ["AGENT_MODE"], os.environ["CORS_ORIGINS"] = "local", "offline", ""
os.environ.setdefault("ARC_TODAY", "2026-09-21")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from tests.helpers.uiserver import Server  # noqa: E402

OUT = ROOT / "docs" / "screenshots" / "final"
DOCS = sorted((ROOT / "demo" / "samples" / "on_time").glob("*.pdf"))
SIZES = {"1280x800": (1280, 800), "390x844": (390, 844)}
QUESTIONS = ("how much will be paid", "explain my claim", "when does my policy expire", "what is my address")


def report_pages() -> None:
    """The first page of each sample report as a PNG (pdftoppm when poppler is installed)."""
    from app import intake
    from app.report import IST, report_pdf
    when = dt.datetime(2026, 9, 21, 12, 0, tzinfo=IST)
    tool = shutil.which("pdftoppm")
    for which, label in (("on_time", "on_time"), ("late_filing", "late"), ("expired", "expired")):
        s = {"id": which, "history": [], "claim": None}
        for name, data in intake.sample_files(which):
            intake.store(s, name, intake.process_file(name, data))
        intake.build(s)
        pdf = OUT / f"report_{label}.pdf"
        pdf.write_bytes(report_pdf(s, now=when))
        if tool:
            subprocess.run([tool, "-png", "-r", "110", "-f", "1", "-l", "1", str(pdf), str(OUT / f"9_report_{label}_page1")], check=True, timeout=60)
        pdf.unlink()
    print("report pages:", "written" if tool else "skipped (poppler is not installed)")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in list(OUT.glob("*.png")) + list(OUT.glob("*.pdf")):
        old.unlink()
    server = Server().start()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for label, (w, h) in SIZES.items():
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            shot = lambda name: page.screenshot(path=str(OUT / f"{name}_{label}.png"))   # noqa: E731
            page.goto(server.url + "/")
            page.wait_for_timeout(300)
            shot("1_landing")

            page.click("#landing-start")
            expect(page.locator("#view-upload")).to_be_visible()
            shot("2_upload_empty")

            held, hold = [], [True]
            page.route("**/documents", lambda route: held.append(route) if hold[0] else route.continue_())
            page.set_input_files("#picker", [str(d) for d in DOCS[:6]])
            expect(page.locator("#files li")).to_have_count(6)
            shot("3_upload_mid_processing")
            hold[0] = False
            for r in held:
                r.continue_()
            expect(page.locator("#files li").first).to_have_text(f"{DOCS[0].name}, ready", timeout=15000)
            page.wait_for_timeout(600)

            page.goto(server.url + "/upload")
            page.set_input_files("#picker", [str(next(d for d in DOCS if d.name == "claim_form.pdf"))])
            expect(page.locator("#reasons")).not_to_be_empty(timeout=15000)
            shot("4_needs_attention")

            page.set_input_files("#picker", [str(d) for d in DOCS if d.name != "claim_form.pdf"])
            expect(page.locator("#view-chat")).to_be_visible(timeout=20000)
            page.wait_for_timeout(500)
            shot("5_chat_first_message")

            for q in QUESTIONS:
                page.fill("#ask", q)
                page.keyboard.press("Enter")
                expect(page.locator(".dots")).to_have_count(0, timeout=10000)
            page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(300)
            shot("6_chat_four_exchanges_bottom")
            page.evaluate("scrollTo(0, 0)")
            page.wait_for_timeout(300)
            shot("7_chat_four_exchanges_top")

            page.route("**/report.pdf", lambda route: route.fallback())     # keep the "Preparing..." state visible for the shot
            page.click("#report")
            page.wait_for_timeout(120)
            shot("8_chat_report_downloading")
            page.wait_for_timeout(1500)

            dt_handle = page.evaluate_handle("() => { const d = new DataTransfer(); d.items.add(new File(['x'], 'a.pdf', {type: 'application/pdf'})); return d; }")
            page.dispatch_event("body", "dragenter", {"dataTransfer": dt_handle})
            page.wait_for_timeout(250)
            shot("10_chat_drag_over")
            ctx.close()
        browser.close()
    server.stop()
    report_pages()
    print("written", len(list(OUT.glob('*.png'))), "screenshots to", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
