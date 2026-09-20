"""The demo web UI: served at "/", same origin as the API, static files only. The trace shown to officers is sanitized. The JS renderer is safe."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import ROOT
from app.tools.registry import TOOL_NAMES, trace_summary

STATIC = ROOT / "app" / "static"
client = TestClient(main.app, raise_server_exceptions=False)
NODE = shutil.which("node")


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- the page
def test_root_serves_the_customer_page():
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>ARC: AI-powered Review of Claims</title>" in r.text and "Upload your claim documents" in r.text and "/static/customer.js" in r.text
    assert "<svg" in r.text and 'class="mark"' in r.text                             # the arc mark, drawn in SVG next to the word ARC
    assert client.get("/officer").status_code == 404                                 # the earlier officer console has been removed


def test_the_page_sets_a_strict_content_security_policy():
    h = client.get("/").headers
    csp = h["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and h["x-content-type-options"] == "nosniff"


def test_the_page_is_csp_clean_and_uses_no_outside_resources():
    html = text("index.html")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)                           # no inline scripts
    assert not re.search(r"\sstyle\s*=", html) and not re.search(r"\son[a-z]+\s*=", html)   # no inline styles or handlers
    for name in ("index.html", "customer.css", "customer.js", "dev.js", "dev.css", "md.js"):
        urls = [u for u in re.findall(r"https?://[^\s\"')]+", text(name)) if u != "http://www.w3.org/2000/svg"]
        assert urls == [], (name, urls)                                              # nothing from a CDN, nothing external at all


def test_the_static_assets_are_served_with_the_right_types():
    for name, kind in (("md.js", "javascript"), ("customer.css", "text/css"), ("customer.js", "javascript"), ("dev.js", "javascript"), ("dev.css", "text/css")):
        r = client.get(f"/static/{name}")
        assert r.status_code == 200 and kind in r.headers["content-type"] and r.text == text(name)
    assert client.get("/static/nope.js").status_code == 404
    assert client.get("/static/%2e%2e/main.py").status_code == 404                   # no path traversal out of the static folder


def test_the_brand_colours_are_used():
    css = text("customer.css").lower()
    for colour in ("#0b2545", "#0fa3b1", "#f2a541"):
        assert colour in css, colour
    assert "#0fa3b1" in text("index.html").lower() and "#f2a541" in text("index.html").lower()


def test_the_api_still_answers_json_errors_next_to_the_page():
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/nope").json()["error"]["code"] == "http_404"


# ---------------------------------------------------------------- the sanitized trace
def debug_on(monkeypatch):
    import dataclasses
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, debug_trace=True))


def _chat(message):
    sid = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{sid}/claim", json={"sample_id": "TC07"})
    return client.post(f"/sessions/{sid}/chat", json={"message": message}).json()


def test_with_debug_trace_the_chat_response_has_a_trace_summary_with_only_tool_ok_and_ms(monkeypatch):
    debug_on(monkeypatch)
    body = _chat("Please assess this claim")
    steps = body["trace_summary"]
    assert steps and all(set(s) == {"tool", "ok", "ms"} for s in steps)
    assert all(s["tool"] in TOOL_NAMES and isinstance(s["ok"], bool) and isinstance(s["ms"], int) for s in steps)
    assert steps[0]["tool"] == "assess_claim"


def test_trace_summary_never_contains_argument_values(monkeypatch):
    debug_on(monkeypatch)
    body = _chat("Assess it, what if the room rent was 5,000 per day")
    raw = json.dumps(body["tool_trace"])
    assert "room_rate_per_day" in raw and "5000" in raw                              # the raw developer trace does carry the arguments ...
    shown = json.dumps(body["trace_summary"])
    for value in ("room_rate_per_day", "5000", "what_if", "args", "arguments"):
        assert value not in shown, value                                             # ... and the sanitized field never does


def test_trace_summary_drops_everything_but_the_three_fields_even_for_hostile_input():
    hostile = [dict(tool="assess_claim", args={"diagnosis": "SECRET-DIAGNOSIS"}, ok=True, ms=812, problems=["SECRET-PROBLEM"], result="SECRET-RESULT"),
               dict(tool="evil<script>alert(1)</script>", args={"x": "SECRET-ARG"}, ok=False, ms="812"),
               dict(tool="final_answer", ok=1, ms=True), dict(tool="search_policy", ok=False), {}]
    out = trace_summary(hostile)
    assert out == [dict(tool="assess_claim", ok=True, ms=812), dict(tool="other", ok=False, ms=None), dict(tool="final_answer", ok=True, ms=None),
                   dict(tool="search_policy", ok=False, ms=None), dict(tool="other", ok=False, ms=None)]
    assert "SECRET" not in json.dumps(out) and "script" not in json.dumps(out)
    assert trace_summary(None) == [] and trace_summary([]) == []


# ---------------------------------------------------------------- the markdown renderer (run under Node)
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def render_all(cases):
    script = "const md=require(process.argv[1]);const c=JSON.parse(require('fs').readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(c.map(x=>md.render(x))));"
    p = subprocess.run([NODE, "-e", script, str(STATIC / "md.js")], input=json.dumps(cases), capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@needs_node
def test_the_javascript_files_have_no_syntax_errors():
    for name in ("customer.js", "dev.js", "md.js"):
        p = subprocess.run([NODE, "--check", str(STATIC / name)], capture_output=True, text=True, timeout=30)
        assert p.returncode == 0, p.stderr


@needs_node
def test_markdown_blocks_and_inline_formatting():
    out = render_all([
        "## Title\n### Sub", "**bold** and *italic* and `code` and *(Policy C.1.b, p.28)*", "- one\n- two **b**", "1. first\n2. second", "line one\nline two  \nline three",
        "| Item | Billed | Note |\n|---|---:|:---:|\n| Gloves | ₹2,400 | a \\| b |", "> **Note** quoted\n> second", "---",
        "```text\nHospital bill      ₹1,84,500\n<b>not bold</b>\n```", "🟢 **Satisfied** ✅ ⚠️ 🔴", "* star bullet\n* second"])
    assert out[0] == "<h2>Title</h2>\n<h3>Sub</h3>"
    assert "<strong>bold</strong>" in out[1] and "<em>italic</em>" in out[1] and "<code>code</code>" in out[1] and "<em>(Policy C.1.b, p.28)</em>" in out[1]
    assert out[2] == "<ul><li>one</li><li>two <strong>b</strong></li></ul>" and out[3] == "<ol><li>first</li><li>second</li></ol>"
    assert out[4] == "<p>line one<br>line two<br>line three</p>"
    assert "<table>" in out[5] and len(re.findall(r"<th[ >]", out[5])) == 3 and '<td class="md-right">₹2,400</td>' in out[5] and '<th class="md-center">Note</th>' in out[5] and "a | b" in out[5]
    assert out[6].startswith("<blockquote><p><strong>Note</strong> quoted<br>second</p></blockquote>") and out[7] == "<hr>"
    assert out[8] == '<pre class="md-code"><code>Hospital bill      ₹1,84,500\n&lt;b&gt;not bold&lt;/b&gt;</code></pre>'      # spaces kept, HTML shown as text
    assert out[9] == "<p>🟢 <strong>Satisfied</strong> ✅ ⚠️ 🔴</p>" and out[10] == "<ul><li>star bullet</li><li>second</li></ul>"


ALLOWED_TAGS = {"p", "br", "strong", "em", "code", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "div",
                "blockquote", "hr"}


class _Audit(HTMLParser):
    """Records every element and attribute the renderer emitted. Escaped text (&lt;script&gt;) is text, not an element, so it never shows up here."""

    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += attrs


@needs_node
def test_markdown_cannot_inject_markup():
    attacks = ["<script>alert(1)</script>", "[click](javascript:alert(1)) ![x](https://evil.example/p.png)", '<img src=x onerror="alert(1)">',
               "**<b onmouseover=alert(1)>x</b>**", "`<script>`", "| <i>h</i> |\n|---|\n| <svg onload=alert(1)> |", "> <iframe src=//evil>", "```\n</pre><script>alert(1)</script>\n```",
               "# <script>x</script>", "- <a href=javascript:x>l</a>", "\u0000\u0001 raw control chars \u0000", '"><script>alert(1)</script>', "&lt;script&gt; &amp;"]
    for src, html in zip(attacks, render_all(attacks)):
        audit = _Audit()
        audit.feed(html)
        assert set(audit.tags) <= ALLOWED_TAGS, (src, audit.tags)                 # only elements we generate ourselves
        assert all(name == "class" and value.startswith("md-") for name, value in audit.attrs), (src, audit.attrs)   # and no attribute but our own classes
        assert "\u0000" not in html
    escaped = render_all(["<script>alert(1)</script>"])[0]
    assert escaped == "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"             # shown to the reader as text
    assert render_all(["&lt;b&gt;"])[0] == "<p>&amp;lt;b&amp;gt;</p>"             # already-escaped input is escaped again, never decoded


@needs_node
def test_every_saved_example_answer_renders_cleanly():
    files = sorted((ROOT / "demo" / "examples").glob("*.md"))
    assert len(files) == 19
    for f, html in zip(files, render_all([p.read_text(encoding="utf-8") for p in files])):
        assert "<script" not in html and "```" not in html, f.name                       # fences are consumed
        assert "**" not in re.sub(r"<pre.*?</pre>", "", html, flags=re.S), f.name        # no unconverted bold left outside code blocks
        assert html.startswith("<h2>"), f.name
    claim = render_all([(ROOT / "demo" / "examples" / "TC07_demo_claim_prescription_missing.md").read_text(encoding="utf-8")])[0]
    assert '<pre class="md-code">' in claim and "₹1,22,125" in claim and "₹1,01,625" in claim and "<table>" in claim and "<blockquote>" in claim
    assert "🟢" in claim and "<h3>Recommendation</h3>" in claim


def test_health_says_whether_the_real_agent_is_answering(monkeypatch):
    import dataclasses
    assert client.get("/health").json()["live"] is False                             # the test environment is offline on purpose
    for retriever, mode, live in (("azure", "foundry", True), ("azure", "offline", False), ("local", "foundry", False), ("local", "offline", False)):
        monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, retriever=retriever, agent_mode=mode))
        body = client.get("/health").json()
        assert body["live"] is live and body["retriever"] == retriever and body["agent_mode"] == mode and body["status"] == "ok"
