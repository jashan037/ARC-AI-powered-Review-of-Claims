"""direct_answer: a short natural reply whose numbers must come from this turn's tool results; details become Show more sections built by existing code."""
import pytest

from app.tools.number_guard import allowed_from, drop_sentences, offenders
from tests.test_plain_questions import Script, agent, customer_session

TOOL = [{"amounts": {"estimated": 122125.0, "confirmed": 101625.0, "held": 20500}, "room_ratio": 0.625, "lines": [1] * 12,
         "admission": "2025-09-10T14:30", "note": "up to 1% per day; 24 months; 10 Sep 2025"}]
A = allowed_from(TOOL)


@pytest.mark.parametrize("text", ["Your estimated payment is ₹1,22,125 and ₹1,01,625 is confirmed today.", "62.5% of the room charge is paid.", "12 items are not payable.",
                                  "You were admitted on 10 September 2025 at 14:30.", "Admitted 10/09/2025.", "Claim CLM-20250910-0001 is ready.", "It is 24 months.", "Nothing numeric here."])
def test_numbers_found_in_tool_results_pass_in_any_format(text):
    assert offenders(text, A) == []


@pytest.mark.parametrize("text,bad", [("The bill was ₹9,999.", ["₹9,999"]), ("About 13 items are not payable.", ["13"]), ("You get 63%.", ["63%"]),
                                      ("Admitted on 11 Sep 2025.", ["11 Sep 2025"]), ("It takes 30 days.", ["30"])])
def test_numbers_no_tool_returned_are_reported(text, bad):
    assert offenders(text, A) == bad


def test_a_sentence_with_an_unverified_number_is_dropped():
    assert drop_sentences("Your estimated payment is ₹1,22,125. The bill was ₹9,999 in total. Please send the prescription.", A) == \
        "Your estimated payment is ₹1,22,125. Please send the prescription."
    assert drop_sentences("Only ₹9,999 matters.", A) == ""


def direct(reply, **kw):
    return {"answer_type": "direct_answer", "reply": reply, **kw}


GOOD = "Your estimated payment is ₹1,22,125, of which ₹1,01,625 is confirmed today."


def run(steps, question="How is my estimate made up?"):
    model = Script(steps)
    return agent(model).ask(customer_session(), question), model


def test_a_verified_reply_is_accepted_and_details_become_show_more_sections():
    res, _ = run([[("assess_claim", {})], [("final_answer", direct(GOOD, details=["room_working", "non_medical_list", "estimate_breakdown"]))]])
    assert res.answer_type == "direct_answer" and res.summary_markdown == GOOD and [t["ok"] for t in res.trace] == [True, True]
    assert [s["id"] for s in res.sections if s["id"] != "evidence"] == ["room", "nonpayable", "estimate"] and "Surgical gloves" in res.sections[1]["markdown"] and "₹12,000" in res.sections[0]["markdown"]
    assert "Estimated payment" in res.sections[2]["markdown"] and "Annexure" not in "".join(s["markdown"] for s in res.sections)     # customer wording


def test_an_invented_number_is_rejected_once_and_listed_then_the_rewrite_is_accepted():
    res, model = run([[("assess_claim", {})], [("final_answer", direct("Your payment is ₹99,999."))], [("final_answer", direct(GOOD))]])
    assert [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True] and res.summary_markdown == GOOD
    assert "₹99,999" in model.outputs[1]["problems"][0]


def test_a_number_that_survives_the_rewrite_loses_its_sentence():
    text = "The bill was ₹9,999 in total. Please send the prescription."
    res, _ = run([[("assess_claim", {})], [("final_answer", direct(text))], [("final_answer", direct(text))]])
    assert res.summary_markdown == "Please send the prescription." and "9,999" not in res.markdown


def test_when_nothing_is_left_a_sentence_is_built_in_code_from_the_tool_result():
    res, _ = run([[("assess_claim", {})], [("final_answer", direct("It is ₹9,999."))], [("final_answer", direct("It is ₹9,999."))]])
    assert res.summary_markdown == GOOD


def test_a_number_without_any_tool_result_asks_for_the_tool_first():
    res, model = run([[("final_answer", direct("You stayed 4 days."))], [("get_claim_summary", {})], [("final_answer", direct("You stayed 4 days in hospital."))]], "how many days did I stay")
    assert "call get_claim_summary" in model.outputs[0]["problems"][0] and res.summary_markdown == "You stayed 4 days in hospital."


@pytest.mark.parametrize("reply,needle", [("One. Two. Three. Four. Five.", "too long"), (" ".join(["word"] * 90), "too long"), ("- a\n- b", "list only for 3")])
def test_length_and_list_rules(reply, needle):
    _, model = run([[("final_answer", direct(reply))], [("final_answer", direct("Your name on this claim is Rohan Verma."))]], "hello")
    assert needle in model.outputs[0]["problems"][0]


def test_a_list_of_three_is_allowed():
    res, _ = run([[("final_answer", direct("These need action:\n- Prescription\n- Signed form\n- Cheque"))]], "hello")
    assert res.status == "ok" and res.summary_markdown.startswith("These need action")


def test_details_need_the_tool_result_they_are_built_from():
    _, model = run([[("final_answer", direct("Here it is.", details=["room_working"]))], [("assess_claim", {})], [("final_answer", direct("Your room rent was reduced by ₹12,000.", details=["room_working"]))]], "why was my room rent reduced")
    assert "needs assess_claim" in model.outputs[0]["problems"][0]
    _, model = run([[("final_answer", direct("Here it is.", details=["policy_reference"]))], [("final_answer", direct("Here it is."))]], "hello")
    assert "needs citations" in model.outputs[0]["problems"][0]
    _, model = run([[("final_answer", direct("Here it is.", details=["bogus"]))], [("final_answer", direct("Here it is."))]], "hello")
    assert "Unknown details" in model.outputs[0]["problems"][0]


def test_the_waiting_period_detail_comes_from_check_waiting_period():
    args = {"first_policy_inception": "2025-03-01", "admission_date": "2026-07-15", "procedure": "cataract surgery"}
    res, _ = run([[("check_waiting_period", args)], [("final_answer", lambda m: direct("Your waiting period has not been served yet.", details=["waiting_period"]))]], "has my waiting period ended")
    assert res.answer_type == "direct_answer" and [s["id"] for s in res.sections if s["id"] != "evidence"] == ["waiting"] and "1 Mar 2027" in res.sections[0]["markdown"]


def test_policy_citations_become_reference_chips_and_the_section_is_marked():
    res, _ = run([[("get_clause", {"clause_ref": "C.1.c"})], [("final_answer", lambda m: direct("The first 30 days have a waiting period, except for accidents.",
                                                                                                 citations=["optima-secure-v062425:C1-c"], details=["policy_reference"]))]], "is there a waiting period at the start")
    assert res.citations and res.citations[0]["label"].startswith("Policy rule") and "chunk_key" in res.citations[0]


def test_a_direct_answer_never_carries_internal_terms():
    res, _ = run([[("assess_claim", {})], [("final_answer", direct("I used assess_claim and got ₹1,22,125."))], [("final_answer", direct("I used assess_claim and got ₹1,22,125."))]])
    assert "assess_claim" not in res.markdown


def test_a_plain_fact_can_be_a_direct_answer():
    res, _ = run([[("get_claim_summary", {})], [("final_answer", direct("Your name on this claim is Rohan Verma."))]], "what's my name")
    assert res.answer_type == "direct_answer" and res.status == "ok" and [t["ok"] for t in res.trace] == [True, True]


# ---------------------------------------------------------------- formatting, the audience line, and the reminder step
def test_bare_amounts_are_written_as_rupees_without_changing_the_value():
    from app.tools.number_guard import reformat_amounts
    assert reformat_amounts("The deduction is 12000.0 and 37,875 is held, 20500 too.") == "The deduction is ₹12,000 and ₹37,875 is held, ₹20,500 too."
    assert reformat_amounts("Admitted 10 Sep 2025 at 14:30, policy year 2025, 62.5% of ₹5,000, CLM-20250910-0001, 12 items") == \
        "Admitted 10 Sep 2025 at 14:30, policy year 2025, 62.5% of ₹5,000, CLM-20250910-0001, 12 items"
    assert reformat_amounts("total 184500") == "total ₹1,84,500"
    assert reformat_amounts("you would get ₹129333.33; room ₹5333.33, fees ₹16833.34, items ₹12500.0 and ₹20500") == \
        "you would get ₹1,29,333; room ₹5,333, fees ₹16,833, items ₹12,500 and ₹20,500"
    assert reformat_amounts("₹1,22,125 and 62.5% of ₹5,000 stay as they are") == "₹1,22,125 and 62.5% of ₹5,000 stay as they are"


def test_a_reply_with_a_raw_amount_is_shown_as_rupees_and_still_passes_the_guard():
    res, _ = run([[("assess_claim", {})], [("final_answer", direct("Your estimated payment is 122125.0."))]])
    assert res.summary_markdown == "Your estimated payment is ₹1,22,125." and [t["ok"] for t in res.trace] == [True, True]


def test_the_audience_line_is_never_echoed_to_the_customer():
    from app.rendering.scrub import find
    assert find("Audience: customer. I can't fetch live weather.") and find("audience: officer") and not find("Our audience is wide")
    res, _ = run([[("final_answer", direct("Audience: customer. I can only help with your claim."))], [("final_answer", direct("Audience: customer. I can only help with your claim."))]], "what's the weather")
    assert "Audience" not in res.markdown and "I can only help with your claim." in res.markdown


def test_after_a_text_reply_the_next_call_is_forced_to_final_answer_and_no_second_tool_runs():
    import json
    from types import SimpleNamespace as NS
    from app.agent.runner import FoundryAgent
    calls = []

    class M:
        def __init__(self):
            self.conversations = NS(create=lambda **_: NS(id="c"), delete=lambda conversation_id, timeout=None: None)
            self.responses = NS(create=self.create)

        def create(self, input, conversation, extra_body, timeout=None, **kw):
            calls.append((input, kw))
            n = len(calls)
            if n == 1:
                return NS(output=[NS(type="function_call", name="get_claim_summary", arguments="{}", call_id="a")])
            if n == 2:
                return NS(output=[NS(type="message")])                                   # plain text instead of final_answer
            assert kw.get("tool_choice") == {"type": "function", "name": "final_answer"}
            return NS(output=[NS(type="function_call", name="final_answer", arguments=json.dumps(direct("Your name on this claim is Rohan Verma.")), call_id="b")])
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = M(), {"agent_reference": {"name": "t", "type": "agent_reference"}}
    res = a.ask(customer_session(), "what's my name")
    assert res.status == "ok" and [t["tool"] for t in res.trace] == ["get_claim_summary", "final_answer"]
    assert "tool_choice" not in calls[0][1] and "tool_choice" not in calls[1][1] and "already above" in calls[2][0]


@pytest.mark.parametrize("version,expected", [("", {"name": "claims-adjudication-agent-v2", "type": "agent_reference"}),
                                              ("7", {"name": "claims-adjudication-agent-v2", "type": "agent_reference", "version": "7"})])
def test_agent_version_pins_the_agent_reference_for_a_rollback(monkeypatch, version, expected):
    import dataclasses
    from types import SimpleNamespace as NS
    import azure.ai.projects as projects
    import azure.identity as identity
    import app.agent.runner as runner
    fake_client = NS(get_openai_client=lambda: NS(with_options=lambda **_: "openai"))
    monkeypatch.setattr(projects, "AIProjectClient", lambda **_: fake_client)
    monkeypatch.setattr(identity, "DefaultAzureCredential", lambda: None)
    monkeypatch.setattr(runner, "settings", dataclasses.replace(runner.settings, agent_version=version))
    assert runner.FoundryAgent().ref == {"agent_reference": expected}


def test_a_payment_answer_without_any_figure_is_sent_back_once_for_the_actual_amounts():
    vague = direct("Your room rent was reduced because of a proportional rule.")
    res, model = run([[("assess_claim", {})], [("final_answer", vague)], [("final_answer", direct("Your room rent was reduced by ₹12,000 because of the room limit."))]],
                     "Why was my room rent reduced?")
    assert "Answer with the figures" in model.outputs[1]["problems"][0] and "₹12,000" in res.summary_markdown
    res, _ = run([[("assess_claim", {})], [("final_answer", vague)], [("final_answer", vague)]], "Why was my room rent reduced?")
    assert [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]          # asked once, then accepted


def test_the_figures_rule_does_not_apply_without_a_claim_result():
    res, _ = run([[("final_answer", direct("Hello, how can I help with your claim?"))]], "hello")
    assert res.status == "ok" and [t["ok"] for t in res.trace] == [True]
