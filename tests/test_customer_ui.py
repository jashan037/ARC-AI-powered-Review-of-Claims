"""The customer page: two screens, no developer vocabulary outside ?dev=1, one list of expected documents shared with the server."""
import json
import re
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from app import intake, main
from app.config import ROOT

STATIC = ROOT / "app" / "static"
client = TestClient(main.app, raise_server_exceptions=False)
CUSTOMER_FILES = ("index.html", "customer.js", "customer.css", "md.js")          # everything a customer's browser loads without ?dev=1
# whole words a customer must never meet: developer vocabulary and the names of the assistant's internals
FORBIDDEN = re.compile(r"\b(tools?|traces?|tracing|chunks?|chunk_keys?|chunk_ids?|result_ids?|tool_trace|trace_summary|assess_claim|search_policy|get_clause|check_waiting_period|"
                       r"lookup_non_medical_item|get_claim_summary|final_answer|function_call|stack ?trace)\b", re.I)


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


class Outline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.ids, self.scripts, self.links = [], [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "script" and "src" in a:
            self.scripts.append(a["src"])
        if tag == "link" and "href" in a:
            self.links.append(a["href"])


def outline():
    o = Outline()
    o.feed(client.get("/").text)
    return o


# ---------------------------------------------------------------- what the page says
def test_no_developer_words_in_anything_a_customer_loads():
    for name in CUSTOMER_FILES:
        hits = sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text(name))})
        assert hits == [], f"{name} contains {hits}"
    served = client.get("/").text                                                     # and the page exactly as served
    assert FORBIDDEN.search(served) is None


def test_developer_words_are_only_in_the_dev_files_which_load_only_with_dev_1():
    dev = text("dev.js") + text("dev.css")
    assert FORBIDDEN.search(dev)                                                      # the trace panel lives here, and only here
    o = outline()
    assert o.scripts == ["/static/md.js", "/static/customer.js"] and "dev" not in " ".join(o.scripts + o.links)
    js = text("customer.js")
    assert 'get("dev") === "1"' in js and '"/static/dev.js"' in js and "/health" not in js and "/static/dev.css" not in js
    assert "/static/dev.css" in text("dev.js") and '"/health"' in text("dev.js")     # the badge, the banner and the trace panel are dev.js's job
    assert client.get("/static/dev.js").status_code == 200                            # still served, just never requested by a normal visit


def test_the_page_says_the_agreed_things():
    html = client.get("/").text
    assert "<title>ARC: AI-powered Review of Claims</title>" in html and "Upload your claim documents" in html
    assert 'id="use-sample"' in html and ">Use sample documents<" in html and "Demo: please upload only sample documents." in html
    assert "This is an AI-assisted estimate, not a claim decision. A claims officer makes the final decision." in html
    assert ">Add a document<" in html and ">ARC<" in html and "AI-powered Review of Claims</p>" in html
    assert "<svg" in html and 'class="mark"' in html and "#0FA3B1" in html and "#F2A541" in html      # the arc mark, drawn in SVG next to the word ARC


def test_there_are_two_screens_and_none_of_the_old_console():
    o = outline()
    assert [i for i in o.ids if i.startswith("screen-")] == ["screen-upload", "screen-chat"]
    assert "aside" not in o.tags and "select" not in o.tags and "nav" not in o.tags       # no sidebar, no claim picker, no dropdowns
    assert o.tags.count("textarea") == 1 and o.tags.count("input") == 2                   # one question box; two hidden file pickers
    html = client.get("/").text
    for old in ("claim-select", "quick", "Expand all", "live-badge", "offline-banner", "How ARC got this answer", "Citations ("):
        assert old not in html and old not in text("customer.js"), old
    assert 'id="screen-chat" class="screen screen-chat" aria-labelledby="chat-title" hidden' in html    # the chat starts hidden, the upload screen is first


def test_the_customer_page_keeps_the_strict_content_security_policy():
    h = client.get("/").headers
    assert "script-src 'self'" in h["content-security-policy"] and "unsafe-inline" not in h["content-security-policy"]
    assert not re.search(r"<script(?![^>]*\bsrc=)", text("index.html")) and not re.search(r"\sstyle\s*=|\son[a-z]+\s*=", text("index.html"))


# ---------------------------------------------------------------- one list of documents for page and server
def test_the_checklist_in_the_page_is_the_servers_list():
    js = text("customer.js")
    block = re.search(r"var EXPECTED = (\[[\s\S]*?\n  \]);", js).group(1)
    pairs = re.findall(r'\{ id: "([a-z_]+)", label: "((?:[^"\\]|\\.)*)" \}', block)
    from_js = [(i, json.loads('"' + l + '"')) for i, l in pairs]
    assert from_js == [(d["id"], d["label"]) for d in intake.EXPECTED]


# ---------------------------------------------------------------- behaviour the page relies on
def test_the_page_shows_at_most_three_suggestions_and_never_the_citations_outside_show_more():
    js = text("customer.js")
    assert "slice(0, 3)" in js                                                        # three suggested questions under the latest answer
    assert "s.id !== \"evidence\"" in js and "Policy references" in js and "Show more" in js and "Show less" in js
    show_more = js.split("function moreBlock")[1].split("function arcMessage")[0]
    assert "refsBlock" in show_more                                                   # the policy reference chips are built only for the "Show more" block
    assert js.count("refsBlock(") == 2                                                # its definition and that single use


def test_the_page_only_uses_documented_endpoints():
    js = text("customer.js")
    assert '"/sessions"' in js and '"/documents"' in js and '"/documents/sample"' in js and '"/intake"' in js and '"/chat"' in js
    assert "/samples" not in js and "/assess" not in js
