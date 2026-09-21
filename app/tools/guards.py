"""Guards on the model's reply text. All in code, none of them trusts the model.

check_reply() lists the problems of a reply. The runner sends them back to the model once. If the second reply still has them, fix_reply() repairs the text in code:
a number no tool returned loses its sentence (or a sentence built from the assessment takes its place), internal terms are written in plain words, decision or officer-voice
sentences are dropped. Length is soft: it is asked for once and then accepted.

  numbers    every amount, percentage, date and count must be in this turn's tool results, the claim's own data, or the customer's question
  internal   tool names, ids, chunk keys, exclusion codes, annexure letters and clause numbers used as labels
  decision   never approve, reject, deny or settle a claim, and never tell anyone to
  voice      "you" and "your claim", never "the insured" or "the claimant"
  format     tables, headings, emoji, bold, quotes and length by question kind (format_guard.py)
"""
from __future__ import annotations

import re

from ..rendering.customer_labels import customerize
from ..rendering.scrub import find as find_internal, scrub
from . import focus, format_guard, timing, verify
from .number_guard import allowed_from, drop_blocks, offenders, split_sentences
from .facts import facts_text
from .fmt import inr
from .registry import TurnContext, fallback_reply

_CODE = re.compile(r"\bExcl\d{2}\b|Annexure\s*[A-D]\b|(?<![\w.])[A-E]\.\d+|(?<![\w.])[A-E]\d+(?:\.\d+)*\s+(?:Def|Note)\b|\bDef\.\s*\d+", re.I)
_DECISION = re.compile(
    r"\b(?:your|the|this) claim (?:is|has been|will be|was) (?:approved|rejected|denied|declined|settled|paid in full)\b|\bI (?:have |will |shall |can |do )?(?:approve|approved|reject|rejected|deny|denied|settle|settled)\b"
    r"|\bwe (?:will|shall|have) (?:pay|paid|approve|approved|reject|rejected|settle|settled)\b|\b(?:you|they|the insurer|the officer) should (?:approve|reject|deny|decline|settle|pay)\b"
    r"|\b(?:approve|reject|deny|decline|settle) (?:this|the|your) claim\b|\bmark (?:it|the claim|this) as (?:approved|payable|rejected|non-?payable)\b", re.I)
_CONFIRMED = re.compile(r"\bconfirmed\b", re.I)   # an estimate is never "confirmed": it is "counted so far"
_VOICE = re.compile(r"\bthe insured\b|\bthe claimant\b|\bthe policy ?holder\b|\binsured person\b|\bthe customer\b", re.I)
# the customer deals with their insurer, not with a named role inside it: "your insurer's team", never "a claims officer"
_OFFICER = re.compile(r"\b(?:claims?\s+)?officers?(?:'s)?\b", re.I)
_LEAK = re.compile(r"\bAudience:", re.I)
_FACTS_WORDS = re.compile(r"\b(?:your |the )?claim facts\b|totals_by_cause|\bthe assessment\b", re.I)   # the model's own inputs are not something the customer knows about
# "will I get this claim", "is my claim going to be paid": answered with "likely" or "appears", never with a bare Yes or No
_OUTCOME_Q = re.compile(r"\b(?:will|would|can|could|do|am|is|shall)\b[^?.!]{0,30}\b(?:get|receive|be paid|be approved|be accepted|be covered|be rejected|be denied|pay|approve|approved|paid|accepted|rejected|denied)\b|\bwill (?:this|my|the) claim\b|\bwill I get\b", re.I)
_YES_NO = re.compile(r"^\W*(?:yes|no|nope|yep|yeah|sure|absolutely|definitely|certainly)\b[\s,.!:;-]*", re.I)


def allowed_numbers(ctx: TurnContext) -> dict:
    """The tool results of this turn, the claim's own data (its dates, amounts, bill lines) and the figures the customer typed."""
    args = [t.get("args") for t in ctx.trace if t["tool"] in ("assess_claim", "check_waiting_period") and t.get("args")]
    claim = ctx.claim
    return allowed_from(ctx.tool_outputs + [ctx.question] + args + ([claim, facts_text(ctx.session)] if claim else []))


_PAYMENT_Q = re.compile(r"\bhow much\b.*\b(?:paid|pay|get|receive|payment|claim)\b|\bwill (?:i|it|this|my|the)\b.*\b(?:paid|get|receive)\b|\bpayment\b|\bestimate\w*\b|\bwhat (?:will|would) i (?:get|receive)\b", re.I)


def _required(ctx: TurnContext) -> list[tuple[float, str]]:
    """Figures a reply must state, each with the sentence built in code that supplies it: both payment figures when something is held; the changed bill of a what-if; the cover left."""
    res, out = ctx.results.get("assessment"), []
    if res and (_PAYMENT_Q.search(ctx.question) or res.get("what_if")) and res["amounts"]["held_pending"]:
        est, now = res["amounts"]["estimated_payable_if_docs_supplied"], res["amounts"]["payable_confirmed_now"]
        both = f"About {inr(est)} once your documents arrive; {inr(now)} is counted so far."
        out += [(est, both), (now, both)]
    if res and res.get("what_if") and res.get("baseline"):
        before, after = res["baseline"]["estimated"], res["amounts"]["estimated_payable_if_docs_supplied"]
        if abs(before - after) >= 1:
            change = f"Your estimated payment would change from {inr(before)} to {inr(after)}."
            out += [(before, change), (after, change)]
    if res and res.get("what_if") and ctx.claim:
        was = sum(l["amount"] for l in ctx.claim["bill_lines"])
        now_bill = res["amounts"]["gross_billed"]
        if abs(now_bill - was) >= 1:
            out.append((now_bill, f"In that case your bill would be {inr(now_bill)}."))
    left = next((o for o in ctx.tool_outputs if isinstance(o, dict) and "cover_left" in o and "cover_amount" in o), None)
    if left is not None and _COVER_LEFT.search(ctx.question):
        out.append((left["cover_left"], f"About {inr(left['cover_left'])} of your {inr(left['cover_amount'])} cover would be left."
                    + (" The other claims you mention are assumed paid in full; I can't verify them." if left["other_claims_you_mentioned"] else "")))
    return out


_COVER_LEFT = re.compile(r"\b(?:cover|sum insured|balance|limit)\b[^?.!]{0,40}\b(?:left|remain\w*|available|balance)\b|\b(?:left|remain\w*)\b[^?.!]{0,30}\b(?:cover|sum insured)\b|\bhow much (?:cover|of my cover)\b", re.I)
_WAITING_Q = re.compile(r"\bwaiting\b|\bcataract\b|\bspecified (?:illness|disease|procedure)s?\b", re.I)
_NOT_ACCIDENT_RULE = re.compile(r"pre-?existing|before the policy|\bPED\b|\b36\b", re.I)
_STATED_ENDED = re.compile(r"\b(?:expired|ended|lapsed|ran out|has expired|is over)\b", re.I)


def _mentions(ctx: TurnContext) -> list[tuple]:
    """(does the reply contain it?, what to tell the model, the sentence built in code)."""
    out = []
    if _WAITING_Q.search(ctx.question) and not _NOT_ACCIDENT_RULE.search(ctx.question):
        out.append((lambda t: bool(re.search(r"accident", t, re.I)), "A waiting-period answer must say the exception for an accident, as a condition ('unless it was caused by an accident').", "This waiting period wouldn't apply if the condition was caused by an accident."))
    period = (ctx.claim or {}).get("policy_period")
    if period and all(period) and _STATED_ENDED.search(ctx.question) and "policy" in ctx.question.lower():
        from .fmt import d_fmt
        from .number_guard import scan
        y, m, d = (int(x) for x in period[1].split("-"))
        end = d_fmt(period[1])
        out.append((lambda t: any(k == (y, m, d) for k, _ in scan(t)["dates"]), f"The customer says the policy has ended: say first what the documents show, that the policy period ends on {end}.",
                    f"Your documents show the policy period ending on {end}."))
    return out


def _both_figures(ctx: TurnContext):
    """Kept for callers that want the two payment figures: (estimate, counted) or None."""
    res = ctx.results.get("assessment")
    if not res or not _PAYMENT_Q.search(ctx.question) or not res["amounts"]["held_pending"]:
        return None
    return res["amounts"]["estimated_payable_if_docs_supplied"], res["amounts"]["payable_confirmed_now"]


def _has_number(text: str, value: float) -> bool:
    from .number_guard import scan
    return any(abs(v - value) < 1 for v, _ in scan(text)["nums"])


def _bad_sentences(text: str, pattern: re.Pattern) -> list[str]:
    out = []
    for line in text.split("\n"):
        out += [s for s in split_sentences(line) if pattern.search(s)]
    return out


def _officer_free(text: str) -> str:
    """"a claims officer decides" -> "your insurer's team decides", without leaving "your your" behind."""
    text = re.sub(r"\b(?:a|an|the)\s+(?:claims?\s+)?officers?(?:'s)?\b", "your insurer's team", text, flags=re.I)
    text = _OFFICER.sub("your insurer's team", text)
    return re.sub(r"\b(your|the|a)\s+your\b", "your", text, flags=re.I)


def check_reply(text: str, ctx: TurnContext) -> list[tuple[str, str]]:
    """(kind, what to tell the model) for each problem of the reply."""
    problems = []
    bad = offenders(text, allowed_numbers(ctx))
    if bad:
        hint = " No tool result of this turn has them: call the tool first, or leave them out." if not ctx.tool_outputs else ""
        problems.append(("numbers", f"These numbers are not in this turn's tool results or the claim data: {bad}. State only figures the tools returned, exactly, and never calculate or round.{hint}"))
    if find_internal(text) or _CODE.search(text) or _LEAK.search(text) or _FACTS_WORDS.search(text):
        found = (find_internal(text) + _CODE.findall(text) + [m.group(0) for m in _FACTS_WORDS.finditer(text)])[:3]
        problems.append(("internal", f"Remove internal terms ({found}): say what a rule is about in plain words, never its code, clause number, annexure letter, tool name or id."))
    if _DECISION.search(text):
        problems.append(("decision", "You never approve, reject, deny or settle a claim and never tell anyone to. Say what appears likely and that the insurer's team decides."))
    if _CONFIRMED.search(text):
        problems.append(("decision", "Do not say 'confirmed': say 'counted so far' or 'looks likely'."))
    if _OUTCOME_Q.search(ctx.question) and _YES_NO.match(text):
        problems.append(("decision", "Do not open with Yes or No on a question about whether the claim will be paid. Open with what appears likely and what it rests on, and say the insurer's team decides."))
    if _VOICE.search(text):
        problems.append(("voice", "Speak to the customer: 'you' and 'your claim', never 'the insured', 'the claimant' or 'the customer'."))
    if _OFFICER.search(text):
        problems.append(("voice", "Never name a role inside the insurer: write \"your insurer's team\", never \"a claims officer\"."))
    missing = [v for v, _ in _required(ctx) if not _has_number(text, v)]
    if missing:
        problems.append(("figures", "The answer must state these figures from the tools: " + ", ".join(inr(v) for v in missing)
                         + " (both payment figures when something is waiting for a document; the changed bill of a what-if; the cover left)."))
    for has, msg, _ in _mentions(ctx):
        if not has(text):
            problems.append(("figures", msg))
    items = verify.verdict_problems(text, ctx)
    if items:
        problems.append(("verdict", verify.verdict_message(items)))
    for m in verify.hedge_problems(text):
        problems.append(("decision", m))
    names = verify.entity_problems(text, ctx)
    if names:
        problems.append(("names", f"These names are not in the customer's documents or the tool results: {names}. Use the plan, hospital and document names exactly as the documents give them."))
    items = verify.policy_problems(text, ctx)
    if items:
        problems.append(("policy", verify.policy_message(items)))
    items = verify.payment_problems(text, ctx)
    if items:
        problems.append(("amounts", verify.payment_message(items)))
    problems += [("timing", m) for m in timing.problems(text, ctx)]
    problems += [("focus", m) for m in focus.problems(text, ctx)]
    problems += [("format", m) for m in format_guard.problems(text, ctx.question, ctx.session.get("history", []))]
    return problems


def fix_reply(text: str, ctx: TurnContext) -> str:
    """The second reply still has problems: repair it in code. Length is soft and stays."""
    allowed = allowed_numbers(ctx)
    if offenders(text, allowed):
        text = drop_blocks(text, allowed, fallback_reply(ctx)) or fallback_reply(ctx)
    text = customerize(scrub(text))
    text = re.sub(_LEAK, "", text)
    text = _FACTS_WORDS.sub(lambda m: "your documents" if "facts" in m.group(0).lower() else "your claim", text)
    if _OUTCOME_Q.search(ctx.question):
        stripped = _YES_NO.sub("", text, count=1)
        if stripped != text:
            text = stripped[:1].upper() + stripped[1:]
    text = _CONFIRMED.sub("counted so far", re.sub(r"\bis confirmed today\b", "is counted so far", text))
    text = _officer_free(text)
    for pattern in (_DECISION, _VOICE):
        for s in _bad_sentences(text, pattern):
            text = text.replace(s, "")
    text = verify.fix_verdicts(text, ctx)
    text = verify.hedge_fix(text)
    text = verify.drop_entities(text, ctx)
    text = verify.drop_policy(text, ctx)
    text = verify.drop_payment_claims(text, ctx)
    text = timing.repair(text, ctx)
    for v, sentence in _required(ctx):
        if sentence and not _has_number(text, v) and sentence not in text:
            text = f"{text}\n\n{sentence}".strip()
    for has, _, sentence in _mentions(ctx):
        if not has(text):
            text = f"{text}\n\n{sentence}".strip()
    text = focus.repair(text, ctx)            # after the figures above: the lead is judged on the text the customer would read
    text = format_guard.repair(text, ctx.question, ctx.session.get("history", []))
    text = re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    return text or fallback_reply(ctx)
