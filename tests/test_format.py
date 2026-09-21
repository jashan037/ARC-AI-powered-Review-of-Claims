"""The format pass: derived totals, the Markdown-aware number guard, the format guard, the prompt's own examples, md.js."""
import re
import shutil
import subprocess

import pytest

from app.agent.instructions import SYSTEM_PROMPT
from app.retrieval.azure_search import get_retriever
from app.tools import format_guard as FG
from app.tools.guards import allowed_numbers, check_reply, fix_reply
from app.tools.number_guard import drop_blocks, offenders
from app.tools.registry import TurnContext, call_tool, precompute
from tests.test_hardening_stage4 import customer_session

TABLE = "| Item | Amount |\n|---|---:|\n| Hospital bill | ₹1,84,500 |\n| Room cost above your plan's limit | −₹49,875 |\n| Extras your plan doesn't cover | −₹12,500 |\n| **Estimated payment** | **₹1,22,125** |"


def ctx_for(question, history=None):
    s = customer_session()
    s["history"] = history or []
    ctx = TurnContext(session=s, retriever=get_retriever(), question=question)
    precompute(ctx)
    return ctx


def kinds(text, question, history=None):
    return [k for k, _ in check_reply(text, ctx_for(question, history))]


# ---------------------------------------------------------------- stage 1: derived totals
def test_the_assessment_returns_the_totals_by_cause_computed_in_code():
    t = ctx_for("x").tool_outputs[0]["totals_by_cause"]
    assert (t["bill_total"], t["room_related_reduction"], t["non_medical_total"], t["non_medical_count"]) == (184500, 49875, 12500, 12)
    assert (t["waiting_for_a_document_total"], t["estimate_total"], t["counted_so_far"]) == (20500, 122125, 101625)
    assert t["largest_non_medical_items"] == [dict(item="Attendant food charges", amount=2800), dict(item="Surgical gloves", amount=2400), dict(item="Service charges", amount=2000)]
    assert (t["other_non_medical_count"], t["other_non_medical_total"]) == (9, 5300)
    assert (t["room_limit_per_day"], t["billed_room_rate_per_day"], t["share_paid_percent"]) == (5000, 8000, 62.5)
    assert t["bill_total"] - t["room_related_reduction"] - t["non_medical_total"] == t["estimate_total"] + 0   # the breakdown adds up


def test_the_claim_facts_carry_the_same_totals():
    from app.tools import facts
    text = facts.facts_text(customer_session())
    for line in ("Reduced because of the room limit (room plus doctor, theatre and nursing charges): ₹49,875", "Extras not payable (non-medical items): 12 items, ₹12,500",
                 "Other extras: 9 items, ₹5,300", "Estimated payment: ₹1,22,125", "Counted so far: ₹1,01,625", "Share of the room charges paid: 62.5%"):
        assert line in text, line


@pytest.mark.parametrize("text", ["**About ₹1,22,125** of your ₹1,84,500 bill looks payable.", "The room is reduced to **62.5%**.", "| Room | −₹49,875 |", "The other 9 come to ₹5,300.",
                                  "You will get 122125 rupees, that is Rs. 1,22,125.", "**₹12,500** isn't payable: 12 items."])
def test_numbers_in_bold_and_in_table_cells_are_recognised(text):
    assert offenders(text, __import__("app.tools.guards", fromlist=["x"]).allowed_numbers(ctx_for("explain my claim"))) == []


def test_a_wrong_number_inside_bold_or_a_cell_is_found():
    allowed = allowed_numbers(ctx_for("explain my claim"))
    assert offenders("**About ₹1,32,125** looks payable.", allowed) == ["₹1,32,125"] and offenders("| Room | −₹48,000 |", allowed) == ["₹48,000"]


# ---------------------------------------------------------------- the repair works on blocks
def test_the_repair_drops_the_bad_list_item_or_table_row_not_a_fragment():
    allowed = allowed_numbers(ctx_for("x"))
    out = drop_blocks("The largest are:\n\n- Attendant food charges ₹2,800\n- Surgical gloves ₹9,999\n- Service charges ₹2,000", allowed)
    assert out == "The largest are:\n\n- Attendant food charges ₹2,800\n- Service charges ₹2,000"
    out = drop_blocks(TABLE.replace("−₹12,500", "−₹99,999"), allowed, "FALLBACK")
    assert out.count("\n") == TABLE.count("\n") - 1 and "99,999" not in out and "| **Estimated payment** | **₹1,22,125** |" in out
    quote = drop_blocks("Fine.\n\n> Your refund of ₹7,777 is on its way.\n\nMore.", allowed)
    assert quote == "Fine.\n\nMore."


def test_a_table_left_with_fewer_than_two_data_rows_becomes_a_sentence_and_never_broken_markdown():
    allowed = allowed_numbers(ctx_for("x"))
    bad = "| Item | Amount |\n|---|---:|\n| Bill | ₹1,84,500 |\n| Room | ₹11,111 |\n| Extras | ₹22,222 |"
    out = drop_blocks(bad, allowed, "Your estimated payment is ₹1,22,125.")
    assert out == "Your estimated payment is ₹1,22,125." and "|" not in out
    cut = drop_blocks("**About ₹1,22,125** looks payable. **Also ₹8,888 more** is due.", allowed)
    assert cut == "**About ₹1,22,125** looks payable." and cut.count("**") % 2 == 0
    assert drop_blocks("This **bold ₹1,22,125 and it is cut", allowed).count("**") == 0


def test_fix_reply_uses_the_block_repair_end_to_end():
    ctx = ctx_for("explain my claim")
    out = fix_reply("Your claim looks likely to be paid.\n\n" + TABLE.replace("−₹12,500", "−₹99,999") + "\n\n> You can drop the prescription anywhere on this page.", ctx)
    assert "99,999" not in out and "Estimated payment" in out and "> You can drop" in out and "\n\n\n" not in out


# ---------------------------------------------------------------- stage 3: the format guard
def test_question_kinds():
    k = FG.question_kind
    assert k("which hospital was I in?") == "fact" and k("when does my policy expire") == "fact" and k("what is my sum insured") == "fact"
    assert k("what if the room rent was 5000") == "topic" and k("how much will be paid") == "topic" and k("is cataract surgery covered") == "topic"
    assert k("explain my claim") == "explain" and k("why is my payment lower") == "explain" and k("list all non-medical items") == "explain" and k("which items are not payable") == "explain"


def test_no_table_for_a_fact_a_yes_no_or_a_what_if():
    for q in ("which hospital was I in", "was my policy active on 10 Sep 2025", "what if the room rent was 5000", "is cataract surgery covered"):
        assert any("table" in m.lower() for m in FG.problems(TABLE, q, [])), q
    assert FG.problems(TABLE, "explain my claim", []) == []                                   # a breakdown is fine when asked for
    assert FG.problems(TABLE, "which items are not payable", []) == []


def test_a_table_needs_three_data_rows():
    two = "| Item | Amount |\n|---|---:|\n| Bill | ₹1,84,500 |\n| Estimate | ₹1,22,125 |"
    assert any("data rows" in m for m in FG.problems(two, "explain my claim", []))


def test_a_repeated_table_is_a_problem_and_is_replaced_by_a_sentence():
    hist = [dict(user="explain my claim", reply="Your claim looks likely.\n\n" + TABLE)]
    assert any("repeats" in m for m in FG.problems(TABLE, "why is my payment lower", hist))
    fixed = FG.repair("Again:\n\n" + TABLE + "\n\nDone.", "why is my payment lower", hist)
    assert "|" not in fixed and "I've already shown that breakdown above." in fixed and fixed.endswith("Done.")
    other = "| Item | Amount |\n|---|---:|\n| Gloves | ₹2,400 |\n| Masks | ₹800 |\n| Kit | ₹900 |"
    assert not any("repeats" in m for m in FG.problems(other, "list all items", hist))


@pytest.mark.parametrize("bad,expect", [("# Your claim\n\nText.", "heading"), ("Text.\n\n---\n\nMore.", "horizontal rule"), ("```\ncode\n```", "code block"), ("All good \U0001F600.", "emoji"),
                                        ("- a\n  - nested\n- b", "nested"), ("**a** **b** **c** **d**", "bold"), ("> one\n\ntext\n\n> two", "quoted")])
def test_forbidden_constructs_are_problems(bad, expect):
    assert any(expect in m for m in FG.problems(bad, "how much will be paid", [])), bad


def test_length_caps_by_kind():
    w = lambda n: " ".join(["word"] * n)   # noqa: E731
    assert FG.problems(w(30), "which hospital was I in", []) == [] and FG.problems(w(31), "which hospital was I in", [])
    assert FG.problems(w(90), "how much will be paid", []) == [] and FG.problems(w(91), "how much will be paid", [])
    assert FG.problems(w(180), "explain my claim", []) == [] and FG.problems(w(181), "explain my claim", []) and any("hard limit" in m for m in FG.problems(w(201), "explain my claim", []))


def test_the_bold_total_row_of_a_table_does_not_count_as_bold_spans():
    assert FG.bold_spans("**a** and **b** and **c**\n" + TABLE) == 3 and FG.problems("Your claim looks likely.\n\n" + TABLE, "explain my claim", []) == []


def test_repair_on_the_second_failure():
    text = "# Title\n\n**a** **b** **c** **d**. \U0001F600\n\n---\n\n- one\n  - two\n\n> q1\n\ntext\n\n> q2"
    out = FG.repair(text, "how much will be paid", [])
    assert not FG.problems(out, "how much will be paid", []), out
    assert "#" not in out and out.count("**") == 6 and "- two" in out and "  - two" not in out
    flat = FG.repair("Sure:\n\n" + TABLE, "which hospital was I in", [])
    assert "|" not in flat and "- Hospital bill: ₹1,84,500" in flat and "- Estimated payment: ₹1,22,125" in flat
    long = "\n\n".join([" ".join(["word"] * 80)] * 4)
    assert FG.words(FG.repair(long, "explain my claim", [])) <= FG.HARD_CAP


def test_the_guard_rejects_once_then_repairs_in_code():
    ctx = ctx_for("which hospital was I in")
    bad = "## Hospital\n\nYou were treated at Riverside Multispeciality Hospital (DEMO)."
    assert "format" in [k for k, _ in check_reply(bad, ctx)]
    assert fix_reply(bad, ctx) == "Hospital\n\nYou were treated at Riverside Multispeciality Hospital (DEMO)."


def test_confirmed_is_never_used_for_an_estimate():
    assert "decision" in kinds("₹1,01,625 is confirmed.", "how much will be paid")
    assert fix_reply("₹1,01,625 is confirmed today.", ctx_for("which items are not payable")) == "₹1,01,625 is counted so far."


# ---------------------------------------------------------------- the prompt's own examples pass every guard
def examples():
    body = SYSTEM_PROMPT.split("EXAMPLES", 1)[1].split("\n", 1)[1]
    return [(m[0].strip(), m[1].strip()) for m in re.findall(r"Customer: (.*?)\nYou: (.*?)(?=\n\nCustomer: |\Z)", body, re.S)]


def test_the_prompt_is_within_8k_and_has_the_seven_examples():
    assert len(SYSTEM_PROMPT) <= 9500
    ex = examples()
    assert [q for q, _ in ex] == ["which hospital was I in?", "how much will be paid?", "explain my claim", "which items are not payable?", "what if the room rent was 5000?",
                                  "is cataract surgery covered?", "what's the weather like?"]


def test_every_example_passes_the_guards_with_the_sample_claims_tool_results():
    for q, reply in examples():
        ctx = ctx_for(q)
        if "5000" in q:
            call_tool("assess_claim", {"what_if": {"room_rate_per_day": 5000}}, ctx)
        if "cataract" in q:
            call_tool("check_waiting_period", {"first_policy_inception": "2024-03-15", "admission_date": "2025-09-10", "procedure": "cataract surgery"}, ctx)
        assert check_reply(reply, ctx) == [], (q, check_reply(reply, ctx))


# ---------------------------------------------------------------- md.js
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def render(*cases):
    import json
    from app.config import ROOT
    script = "const md=require(process.argv[1]);const c=JSON.parse(require('fs').readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(c.map(x=>md.render(x))));"
    p = subprocess.run([NODE, "-e", script, str(ROOT / "app" / "static" / "md.js")], input=json.dumps(list(cases)), capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@needs_node
def test_md_renders_the_breakdown_table_with_alignment_a_total_row_and_a_quote():
    out = render(TABLE + "\n\nYour room was ₹8,000.\n\n> You can drop it **here**.")[0]
    assert '<th class="md-right">Amount</th>' in out and '<td class="md-right">−₹49,875</td>' in out and '<tr class="md-total"><td><strong>Estimated payment</strong></td><td class="md-right"><strong>₹1,22,125</strong></td></tr>' in out
    assert out.count("<tr") == 5 and "<p>Your room was ₹8,000.</p>" in out and "<blockquote><p>You can drop it <strong>here</strong>.</p></blockquote>" in out
    aligned = render("| a | b | c |\n|:---|:---:|---:|\n| 1 | 2 | 3 |")[0]
    assert '<th class="md-left">a</th>' in aligned and '<th class="md-center">b</th>' in aligned and '<th class="md-right">c</th>' in aligned


@needs_node
def test_md_links_are_http_or_https_only_and_never_images_or_script():
    out = render("[the page](https://example.com/a?b=1&c=2)", "[x](javascript:alert(1))", "![img](https://evil.example/p.png)", "[y](data:text/html;base64,AAAA)", '[z](https://a.com" onmouseover="alert(1))',
                 "[w](http://a.com) and [v](//a.com)")
    assert out[0] == '<p><a href="https://example.com/a?b=1&amp;c=2" rel="noopener noreferrer" target="_blank">the page</a></p>'
    assert "<a" not in out[1] and "javascript:alert(1)" in out[1] and "<img" not in out[2] and "<a" not in out[3] and "onmouseover" not in re.sub(r"&quot;", "", "".join(re.findall(r"<[^>]*>", out[4])))
    assert out[5].count("<a ") == 1 and 'href="http://a.com"' in out[5]


@needs_node
def test_md_shows_malformed_markdown_as_plain_text_and_escapes_everything():
    out = render("**unclosed bold and | a stray pipe", "| a | b |\nno separator row", "<script>alert(1)</script> **<b>x</b>**", "1. one\n2. two\n\n- x", "`<img src=x>`")
    assert out[0] == "<p>**unclosed bold and | a stray pipe</p>" and "<table" not in out[1] and "<script" not in out[2] and "&lt;script&gt;" in out[2] and "<b>" not in out[2]
    assert out[3] == "<ol><li>one</li><li>two</li></ol>\n<ul><li>x</li></ul>" and out[4] == "<p><code>&lt;img src=x&gt;</code></p>"


def test_the_models_own_inputs_are_never_named_to_the_customer():
    ctx = ctx_for("which documents have I sent")
    assert "internal" in [k for k, _ in check_reply("These are listed under Documents received in your claim facts.", ctx)]
    assert fix_reply("These are listed in your claim facts.", ctx) == "These are listed in your documents."


def test_a_breakdown_that_does_not_add_up_is_a_problem_and_the_extra_row_is_dropped():
    bad = TABLE.replace("| **Estimated payment**", "| Waiting for a document | −₹20,500 |\n| **Estimated payment**")
    assert FG.adds_up(FG.tables(TABLE)[0]) and not FG.adds_up(FG.tables(bad)[0])
    assert any("does not add up" in m for m in FG.problems(bad, "explain my claim", []))
    fixed = FG.repair(bad, "explain my claim", [])
    assert "Waiting for a document" not in fixed and FG.adds_up(FG.tables(fixed)[0]) and not FG.problems(fixed, "explain my claim", [])
    assert FG.adds_up(FG.tables("| Item | Amount |\n|---|---:|\n| Gloves | ₹2,400 |\n| Masks | ₹800 |\n| Kit | ₹900 |")[0])       # a list of items has no total row: not checked
