"""Guards on the model's reply text. All in code, none of them trusts the model.

check_reply() lists the problems of a reply. The runner sends them back to the model once. If the second reply still has them, fix_reply() repairs the text in code:
a number no tool returned loses its sentence (or a sentence built from the assessment takes its place), internal terms are written in plain words, decision or officer-voice
sentences are dropped. Length is soft: it is asked for once and then accepted.

  numbers    every amount, percentage, date and count must be in this turn's tool results, the claim's own data, or the customer's question
  internal   tool names, ids, chunk keys, exclusion codes, annexure letters and clause numbers used as labels
  decision   never approve, reject, deny or settle a claim, and never tell anyone to
  voice      "you" and "your claim", never "the insured" or "the claimant"
  length     at most about 120 words unless the customer asks for detail
"""
from __future__ import annotations

import re

from ..rendering.customer_labels import customerize
from ..rendering.scrub import find as find_internal, scrub
from .number_guard import allowed_from, drop_sentences, offenders, split_sentences
from .facts import facts_text
from .registry import TurnContext, fallback_reply

MAX_WORDS = 120
_DETAIL = re.compile(r"\b(?:detail\w*|in full|full list|breakdown|explain (?:everything|fully)|step by step|everything|all the items|list (?:all|every)|compare|comparison|table)\b", re.I)
_CODE = re.compile(r"\bExcl\d{2}\b|Annexure\s*[A-D]\b|(?<![\w.])[A-E]\.\d+|(?<![\w.])[A-E]\d+(?:\.\d+)*\s+(?:Def|Note)\b|\bDef\.\s*\d+", re.I)
_DECISION = re.compile(
    r"\b(?:your|the|this) claim (?:is|has been|will be|was) (?:approved|rejected|denied|declined|settled|paid in full)\b|\bI (?:have |will |shall |can |do )?(?:approve|approved|reject|rejected|deny|denied|settle|settled)\b"
    r"|\bwe (?:will|shall|have) (?:pay|paid|approve|approved|reject|rejected|settle|settled)\b|\b(?:you|they|the insurer|the officer) should (?:approve|reject|deny|decline|settle|pay)\b"
    r"|\b(?:approve|reject|deny|decline|settle) (?:this|the|your) claim\b|\bmark (?:it|the claim|this) as (?:approved|payable|rejected|non-?payable)\b", re.I)
_VOICE = re.compile(r"\bthe insured\b|\bthe claimant\b|\bthe policy ?holder\b|\binsured person\b|\bthe customer\b", re.I)
_LEAK = re.compile(r"\bAudience:", re.I)
# "will I get this claim", "is my claim going to be paid": answered with "likely" or "appears", never with a bare Yes or No
_OUTCOME_Q = re.compile(r"\b(?:will|would|can|could|do|am|is|shall)\b[^?.!]{0,30}\b(?:get|receive|be paid|be approved|be accepted|be covered|be rejected|be denied|pay|approve|approved|paid|accepted|rejected|denied)\b|\bwill (?:this|my|the) claim\b|\bwill I get\b", re.I)
_YES_NO = re.compile(r"^\W*(?:yes|no|nope|yep|yeah|sure|absolutely|definitely|certainly)\b[\s,.!:;-]*", re.I)


def words(text: str) -> int:
    return len(re.findall(r"\S+", re.sub(r"[*_`|#>-]", " ", text)))


def allowed_numbers(ctx: TurnContext) -> dict:
    """The tool results of this turn, the claim's own data (its dates, amounts, bill lines) and the figures the customer typed."""
    args = [t.get("args") for t in ctx.trace if t["tool"] in ("assess_claim", "check_waiting_period") and t.get("args")]
    claim = ctx.claim
    return allowed_from(ctx.tool_outputs + [ctx.question] + args + ([claim, facts_text(ctx.session)] if claim else []))


def _bad_sentences(text: str, pattern: re.Pattern) -> list[str]:
    out = []
    for line in text.split("\n"):
        out += [s for s in split_sentences(line) if pattern.search(s)]
    return out


def check_reply(text: str, ctx: TurnContext) -> list[tuple[str, str]]:
    """(kind, what to tell the model) for each problem of the reply."""
    problems = []
    bad = offenders(text, allowed_numbers(ctx))
    if bad:
        hint = " No tool result of this turn has them: call the tool first, or leave them out." if not ctx.tool_outputs else ""
        problems.append(("numbers", f"These numbers are not in this turn's tool results or the claim data: {bad}. State only figures the tools returned, exactly, and never calculate or round.{hint}"))
    if find_internal(text) or _CODE.search(text) or _LEAK.search(text):
        found = (find_internal(text) + _CODE.findall(text))[:3]
        problems.append(("internal", f"Remove internal terms ({found}): say what a rule is about in plain words, never its code, clause number, annexure letter, tool name or id."))
    if _DECISION.search(text):
        problems.append(("decision", "You never approve, reject, deny or settle a claim and never tell anyone to. Say what appears likely and that a claims officer decides."))
    if _OUTCOME_Q.search(ctx.question) and _YES_NO.match(text):
        problems.append(("decision", "Do not open with Yes or No on a question about whether the claim will be paid. Open with what appears likely and what it rests on, and say a claims officer decides."))
    if _VOICE.search(text):
        problems.append(("voice", "Speak to the customer: 'you' and 'your claim', never 'the insured', 'the claimant' or 'the customer'."))
    if words(text) > MAX_WORDS and not _DETAIL.search(ctx.question):
        problems.append(("length", f"The reply is {words(text)} words. Answer in at most about {MAX_WORDS}, leading with the answer, unless the customer asked for detail."))
    return problems


def fix_reply(text: str, ctx: TurnContext) -> str:
    """The second reply still has problems: repair it in code. Length is soft and stays."""
    allowed = allowed_numbers(ctx)
    if offenders(text, allowed):
        text = drop_sentences(text, allowed) or fallback_reply(ctx)
    text = customerize(scrub(text))
    text = re.sub(_LEAK, "", text)
    if _OUTCOME_Q.search(ctx.question):
        stripped = _YES_NO.sub("", text, count=1)
        if stripped != text:
            text = stripped[:1].upper() + stripped[1:]
    for pattern in (_DECISION, _VOICE):
        for s in _bad_sentences(text, pattern):
            text = text.replace(s, "")
    text = re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    return text or fallback_reply(ctx)
