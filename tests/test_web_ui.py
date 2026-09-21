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

STATIC = ROOT / "app" / "static"
client = TestClient(main.app, raise_server_exceptions=False)
NODE = shutil.which("node")


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- the page
def test_root_serves_the_page_with_the_security_headers():
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "/static/app.js" in r.text and "/static/app.css" in r.text and "/static/md.js" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "style-src 'self'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"


def test_the_old_ui_is_gone():
    for name in ("customer.js", "customer.css", "dev.js", "dev.css"):
        assert client.get(f"/static/{name}").status_code == 404 and not (STATIC / name).exists()
    assert client.get("/officer").status_code == 404 and client.get("/health").status_code == 404


def test_the_page_is_csp_clean_and_uses_no_outside_resources():
    html = text("index.html")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)                           # no inline scripts
    assert not re.search(r"\sstyle\s*=", html) and not re.search(r"\son[a-z]+\s*=", html)   # no inline styles or handlers
    assert "<style" not in html
    js = text("app.js")
    assert "setAttribute(\"style\"" not in js and "cssText" not in js and "eval(" not in js and "new Function" not in js
    for name in ("index.html", "app.css", "app.js", "md.js"):
        urls = [u for u in re.findall(r"https?://[^\s\"')]+", text(name)) if u != "http://www.w3.org/2000/svg"]
        assert urls == [], (name, urls)                                              # nothing from a CDN, nothing external at all
    assert "@import" not in text("app.css") and "url(" not in text("app.css")


def test_the_static_assets_are_served_with_the_right_types():
    for name, kind in (("md.js", "javascript"), ("app.css", "text/css"), ("app.js", "javascript")):
        r = client.get(f"/static/{name}")
        assert r.status_code == 200 and kind in r.headers["content-type"] and r.text == text(name)
    assert client.get("/static/nope.js").status_code == 404
    assert client.get("/static/%2e%2e/main.py").status_code == 404                   # no path traversal out of the static folder


def test_the_design_tokens_are_defined_once_at_the_top_of_the_stylesheet():
    css = text("app.css")
    root = css[css.index(":root"):css.index("}", css.index(":root"))]
    for token, value in (("--bg", "#F7F7F5"), ("--card", "#FFFFFF"), ("--line", "#E7E7E3"), ("--text", "#1F2933"), ("--muted", "#6B7580"), ("--tint", "#EEF3F6")):
        assert f"{token}: {value};" in root
    assert "linear-gradient" not in css.replace("linear-gradient(to bottom, transparent, var(--bg) 24px)", "")   # the only gradient is the fade above the bar


def test_the_api_still_answers_json_errors_next_to_the_page():
    assert client.get("/nope").json()["error"]["code"] == "http_404"


# ---------------------------------------------------------------- the markdown renderer (run under Node)
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def render_all(cases):
    script = "const md=require(process.argv[1]);const c=JSON.parse(require('fs').readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(c.map(x=>md.render(x))));"
    p = subprocess.run([NODE, "-e", script, str(STATIC / "md.js")], input=json.dumps(cases), capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@needs_node
def test_the_javascript_files_have_no_syntax_errors():
    for name in ("app.js", "md.js"):
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
