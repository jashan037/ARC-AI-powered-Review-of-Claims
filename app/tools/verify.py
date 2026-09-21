"""The verification layer for what a reply SAYS, beyond its numbers (all in code, none of it trusts the model).

  verdicts   G3: a rule result (policy in force, waiting period served, filing time, non-medical items, documents, the overall outlook) comes from a tool. A sentence that states or
             implies the opposite is rewritten once, then replaced by a sentence built in code from the tool result. A hypothetical ("if", "assuming", "what if") is not a verdict.
  policy     G4: a sentence that states what the policy covers, excludes or limits must be supported by text this turn: a passage a tool retrieved, the claim facts, a tool result or a
             rule table. Every duration in months or years and every distinctive word of the sentence must be found there; otherwise the sentence is dropped, and if nothing is left the
             reply says "I can't confirm that from your policy wording."
  hedging    an outcome is "likely" or "appears": never "approved" or "confirmed", and "would likely not be payable" instead of "not payable".
  repeats    a note or next step already shown earlier in this chat is not shown again.

The cheapest reliable method is used (string support, no second model call); see docs/evidence/accuracy_report.md for what it costs and what it drops.
"""
from __future__ import annotations

import json
import re

from . import claims_engine as E
from .number_guard import scan, split_sentences

CANNOT_CONFIRM = "I can't confirm that from your policy wording."

_HYPO = re.compile(r"^\W*(?:if|assuming|suppose|supposing|in case|had|were|say|for example|for instance|unless)\b|\b(?:if you|if your|if the|if it|if i|what if|would have|had you|were you)\b", re.I)


def sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        out += split_sentences(line)
    return out


# ---------------------------------------------------------------- G3: verdicts that must agree with the tools
def _assessment(ctx) -> dict | None:
    return ctx.results.get("assessment")


def _waiting_state(ctx):
    """True when every waiting period that applies is served, False when one is not yet served, None when no tool said."""
    w = ctx.results.get("waiting")
    checks = w["checks"] if w else (_assessment(ctx) or {}).get("waiting", {}).get("checks")
    if not checks:
        return None
    return not any(k["status"] == "violated" for k in checks)


def _rules(ctx):
    """(name, truth, says_true, says_false, correct sentence) for every rule result a tool has given this turn."""
    res, out = _assessment(ctx), []
    if res and res.get("policy_in_force"):
        r = res["policy_in_force"]
        out.append(("policy_in_force", r["in_force"],
                    re.compile(r"\b(?:policy|cover|plan)\b[^.?!]*\b(?:was|is|were) (?:in force|active|valid|live)\b|\bwithin (?:the|your) (?:current )?policy period\b|\bstill (?:in force|active|valid)\b", re.I),
                    re.compile(r"\b(?:policy|cover|plan)\b[^.?!]*\b(?:was|is|were|had) (?:not in force|not active|not valid|inactive|expired|lapsed|ended)\b|\b(?:expired|lapsed) (?:before|by|on)\b|\bafter the (?:end of the )?policy period\b|\boutside (?:the|your) (?:current )?policy period\b|\bwas ?n't in force\b|\bwas ?n't active\b", re.I),
                    E.policy_in_force_text(r)))
    if res and res.get("filing"):
        f = res["filing"]
        out.append(("filing", not f["late"],
                    re.compile(r"\b(?:on time|not late|within the (?:30[- ]day )?(?:time )?limit|inside the (?:30 days|time limit)|in time)\b", re.I),
                    re.compile(r"\b(?:late|past the (?:time )?(?:limit|deadline)|after the (?:30[- ]day )?(?:time )?(?:limit|deadline)|missed the (?:deadline|limit)|too late|delayed)\b", re.I),
                    E.filing_text(f)))
    ws = _waiting_state(ctx)
    if ws is not None:
        w = ctx.results.get("waiting")
        checks = w["checks"] if w else res["waiting"]["checks"]
        rows = "; ".join(f"{k['name']}: {'already met' if k['status'] == 'satisfied' else 'not yet met' if k['status'] == 'violated' else 'does not apply'}" for k in checks)
        out.append(("waiting_period", ws,
                    re.compile(r"\bwaiting period\b[^.?!;,]*\b(?:is|has been|was|have been|are) (?:over|served|met|complete[d]?|passed|finished|done|satisfied|already met)\b|\bno longer (?:applies|a (?:problem|issue))\b|\b(?:has|have) (?:now )?(?:passed|ended|finished)\b", re.I),
                    re.compile(r"\bwaiting period\b[^.?!;,]*\b(?:is|has|was|have) (?:not (?:yet )?(?:been )?|n't (?:yet )?(?:been )?)(?:over|served|met|complete[d]?|passed|finished|done|satisfied|ended)\b|\bwaiting period\b[^.?!;,]*\bstill (?:applies|running|on|to run)\b|\bnot yet (?:served|over|met|completed)\b|\b(?:isn't|hasn't|wasn't) (?:over|served|met|passed) yet\b", re.I),
                    f"Waiting periods on your claim: {rows}."))
    if res and res.get("bill"):
        protect = bool(res["bill"]["protect_benefit_in_force"])
        out.append(("non_medical", protect,
                    re.compile(r"\bnon[- ]?medical (?:items|expenses|charges)\b[^.?!]*\b(?:are|is|were) (?!not\b)(?:payable|paid|covered|reimbursed)\b|\bextras\b[^.?!]*\b(?:are|is) (?!not\b)(?:payable|paid|covered)\b", re.I),
                    re.compile(r"\bnon[- ]?medical (?:items|expenses|charges)\b[^.?!]*\b(?:are|is|were) (?:not|never)\b|\bnon[- ]?medical (?:items|expenses|charges)\b[^.?!]*\b(?:aren't|isn't|won't be|would not be)\b|\bextras\b[^.?!]*\b(?:aren't|are not|isn't)\b", re.I),
                    "Extras such as gloves and masks are not paid unless the add-on Protect Benefit applies, and it is not on your policy." if not protect else "Extras such as gloves and masks are paid because your plan includes the Protect Benefit."))
    if res and res.get("documents"):
        missing = [d["name"] for d in res["documents"]["missing"] + res["documents"]["incomplete"]]
        out.append(("documents", not missing,
                    re.compile(r"\b(?:all|every) (?:of )?(?:your |the )?documents\b[^.?!]*\b(?:are|have been|were) (?:received|here|in|sent|complete)\b|\bnothing (?:is|else is) (?:missing|needed|outstanding)\b|\bno (?:documents?|paperwork) (?:is|are) (?:missing|needed|outstanding)\b|\bdocuments look complete\b", re.I),
                    re.compile(r"\b(?:is|are) (?:still )?(?:missing|outstanding|not (?:yet )?(?:received|sent|attached))\b|\bstill (?:need|needed|waiting for)\b|\bwaiting for (?:a|the|your) (?:document|prescription)\b", re.I),
                    ("Still missing: " + "; ".join(missing) + ".") if missing else "No document is missing."))
    if res:
        rec = res["recommendation"]
        out.append(("outlook", rec != "likely_not_payable",
                    re.compile(r"\byour claim\b[^.?!]*\b(?:looks|appears|is|seems) likely to be (?:paid|payable|eligible|covered)\b|\byour claim (?:will|should) be paid\b", re.I),
                    re.compile(r"\byour claim\b[^.?!]*\b(?:would likely not|is likely not|looks (?:unlikely|likely not)|is not|isn't|won't be|will not be|would not be) (?:to be )?(?:payable|paid|covered|eligible)\b|\byour claim\b[^.?!]*\bunlikely to be paid\b", re.I),
                    "Your claim looks likely not payable, and a claims officer decides." if rec == "likely_not_payable" else ""))
    return out


def _date_truth(sentence: str, ctx):
    """The policy-in-force verdict for a DIFFERENT date named in the sentence (not the admission date): (truth, sentence built in code), or None when the sentence names no other full date."""
    res = _assessment(ctx)
    if not res or not res.get("policy_in_force"):
        return None
    r = res["policy_in_force"]
    skip = {tuple(int(x) for x in r[k].split("-")) for k in ("admission_date", "period_start", "period_end")}     # the admission date and the period's own ends are not "another date"
    asked = {k for k, _ in scan(ctx.question)["dates"] if k[0]}                                                   # only a date the CUSTOMER asked about is judged (not the first inception or a renewal start)
    for (y, m, d), _raw in scan(sentence)["dates"]:
        if y and (y, m, d) not in skip and (y, m, d) in asked and 1990 < y < 2100:
            try:
                other = E.policy_in_force(dict(policy_period=[r["period_start"], r["period_end"]], admission_datetime=f"{y:04d}-{m:02d}-{d:02d}T00:00"))
            except ValueError:
                return None
            return other["in_force"], E.policy_in_force_text(other).replace("on the admission date", "on that date").replace("the admission date", "that date")
    return None


def verdict_problems(text: str, ctx) -> list[tuple[str, str, str]]:
    """(rule, the offending sentence, the sentence built in code) for each sentence that contradicts a tool result."""
    found = []
    for name, truth0, says_true, says_false, correct0 in _rules(ctx):
        if not correct0:
            continue
        for s in sentences(text):
            truth, correct = truth0, correct0
            if name == "policy_in_force" and (other := _date_truth(s, ctx)):     # a sentence about another date is judged against that date
                truth, correct = other
            wrong = says_false if truth else says_true
            wm = wrong.search(s)
            if wm and not _HYPO.search(s):
                right = (says_true if truth else says_false).search(s)
                # the right verdict elsewhere in the sentence ("not late ... late") leaves it alone; the wrong phrase itself containing the right words ("nothing IS MISSING") does not
                if not right or (wm.start() <= right.start() and right.end() <= wm.end()):
                    found.append((name, s, correct))
    return found


def verdict_message(items) -> str:
    rules = ", ".join(dict.fromkeys(n.replace("_", " ") for n, _, _ in items))
    return f"A sentence contradicts what the tools returned ({rules}): {items[0][1]!r}. State the tool's result: {items[0][2]!r}"


def fix_verdicts(text: str, ctx) -> str:
    """Replace each contradicting sentence with the sentence built from the tool result (once per rule)."""
    done = set()
    for name, s, correct in verdict_problems(text, ctx):
        if name in done:
            text = text.replace(s, "")
        else:
            text, done = text.replace(s, correct, 1), done | {name}
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


# ---------------------------------------------------------------- hedging
_APPROVE = re.compile(r"\b(?:approved|approve[sd]?)\b", re.I)
_NEG_APPROVE = re.compile(r"\b(?:can't|cannot|can not|don't|do not|won't|never|not|unable to|isn't able to|am not able to)\s+(?:\w+\s+)?approve\b|\bapprov\w* (?:is|will be) (?:decided|made) by\b|\bfor approval\b|\bapproval\b", re.I)
_UNHEDGED = re.compile(r"\b(?:is|are|isn't|aren't|is not|are not|will not be|won't be|would not be|wouldn't be)\s+(not\s+)?(payable|paid|covered|eligible|reimbursed)\b", re.I)
_HEDGE_WORDS = re.compile(r"\b(?:likely|appears|looks|seems|unlikely|may|might|could|expected|probably|usually|typically|if|assuming|when)\b", re.I)


def hedge_problems(text: str) -> list[str]:
    out = []
    for s in sentences(text):
        if _APPROVE.search(s) and not _NEG_APPROVE.search(s):
            out.append(f"Do not say 'approved': say what looks likely and that a claims officer decides. ({s[:60]!r})")
        m = _UNHEDGED.search(s)
        if m and (m.group(1) or re.search(r"n't|not|won't|wouldn't", m.group(0), re.I)) and not _HEDGE_WORDS.search(s):
            out.append(f"Hedge an outcome that is not payable: say 'would likely not be payable' or 'appears not to be covered'. ({s[:60]!r})")
            break
    return out


def hedge_fix(text: str) -> str:
    text = re.sub(r"\b(?:isn't|is not|aren't|are not|won't be|will not be|wouldn't be|would not be)\s+(payable|paid|covered|eligible|reimbursed)\b",
                  lambda m: "would likely not be " + m.group(1), text, flags=re.I)
    text = re.sub(r"\bapproved\b", "looks likely to be accepted", text, flags=re.I)
    return text


# ---------------------------------------------------------------- G4: policy statements need support this turn
_POLICY_MARKER = re.compile(r"\b(?:covers?|covered|cover for|excludes?|excluded|exclusion|waiting period|limit(?:ed|s)?|maximum|at most|up to|only after|not payable|payable|eligible|entitled|"
                            r"must|required|requires?|benefit|applies|apply|conditions?|except(?:ion)?|permitted|allowed|not allowed|reimburse\w*|admissible|includes?|days? of|months? of|per (?:day|year))\b", re.I)
_DURATION = re.compile(r"(\d+)\s*[- ]?\s*(month|year)s?\b", re.I)
_WORD = re.compile(r"[a-z]{5,}")
# What a statement about the policy can get WRONG is a subject: a condition, a treatment, a benefit or an exclusion. Those distinctive terms must be found in this turn's text.
# (Ordinary vocabulary such as "proportion" or "withheld" is not checked: the figures are checked by the number guard and the verdicts by the rules above.)
_DOMAIN = set("""maternity pregnancy childbirth delivery dental cosmetic obesity bariatric ayush ayurveda ayurvedic homeopathy homeopathic unani siddha naturopathy organ transplant donor ambulance psychiatric mental infertility
fertility hiv aids terrorism war nuclear alcohol intoxication drug drugs substance sports adventure experimental unproven prosthesis prosthetic hearing spectacles lenses lasik refractive vaccination vaccine dialysis
chemotherapy radiotherapy robotic stem cell sleep apnoea apnea cataract hernia hysterectomy fibroid calculi stone stones tonsillectomy sinusitis rhinitis glaucoma pancreatitis cirrhosis varicose septum meniscal ligament
tendon knee hip joint replacement piles fissure fistula hydrocele thyroid prostate endometriosis gout osteoporosis arthritis spondylosis cancer tumour tumor diabetes hypertension asthma epilepsy stroke
angioplasty bypass stent pacemaker cesarean caesarean abortion contraception circumcision gender sex reassignment weight loss vitamins supplements tonic cosmetics wig pigmentation acne hair
domiciliary daycare day-care icu ventilator opd outpatient consultation restore bonus deductible copay co-payment sublimit sub-limit cashless network portability grace moratorium""".split())
_MEDICAL = re.compile(r"\b[a-z]{4,}(?:ectomy|plasty|otomy|ostomy|oscopy|itis|osis|emia|aemia|pathy|therapy|graphy|lithiasis|megaly|oma)\b")


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{4,}", text.lower())}


def corpus_chunks(ctx, rules: bool = True, question: bool = True) -> list[str]:
    """Everything this turn can support a statement with, one chunk per source: each retrieved passage, the claim facts, each tool result, the rule tables (when rules), the customer's question."""
    from .facts import facts_text
    parts = [json.dumps(o, ensure_ascii=False) for o in ctx.tool_outputs] + ([ctx.question or ""] if question else [])   # the customer's own words support a figure, never a policy statement
    parts += [c.text for c in ctx.seen.values()]
    if ctx.claim:
        parts.append(facts_text(ctx.session))
    if rules:
        for name in ("plan_config.json", "claim_documents.json"):
            p = E.settings.data_dir / "rules" / name
            if p.exists():
                parts.append(p.read_text(encoding="utf-8"))
    parts += _derived_durations(ctx.tool_outputs)
    return parts


def corpus(ctx, rules: bool = True) -> str:
    return "\n".join(corpus_chunks(ctx, rules))


def _derived_durations(obj) -> list[str]:
    """'months_since_the_policy_started: 17' in a tool result is the duration '17 months'."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                for unit in ("month", "year"):
                    if unit in k:
                        out.append(f"{int(v)} {unit}s")
            out += _derived_durations(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _derived_durations(v)
    return out


def _pairs(text: str) -> set[tuple[int, str]]:
    return {(int(n), u.lower()) for n, u in _DURATION.findall(text)}


def unsupported(sentence: str, chunks: list[str], stems: set[str]) -> list[str]:
    """What in a policy statement this turn's text does not support: a condition, treatment or benefit that appears nowhere, and a duration in months or years that no source ABOUT THE SAME THING
    states (the source must hold one of the sentence's subject terms, so a "12 months" about something else cannot vouch for a waiting period)."""
    low = sentence.lower()
    terms = sorted({w for w in _WORD.findall(low) if w in _DOMAIN} | set(_MEDICAL.findall(low)))
    words = [w for w in terms if w[:6] not in stems]
    bad = []
    for n, u in _pairs(sentence):
        about = [c for c in chunks if not terms or any(w[:5] in c.lower() for w in terms)]
        if not any((n, u) in _pairs(c) for c in about):
            bad.append(f"{n} {u}s")
    return bad + words[:4]


def policy_problems(text: str, ctx) -> list[tuple[str, list[str]]]:
    """[(sentence, what is unsupported)] for policy statements this turn's text does not support."""
    chunks = corpus_chunks(ctx, question=False)
    stems = _stems("\n".join(chunks))
    out = []
    for s in sentences(text):
        if _POLICY_MARKER.search(s) and not s.strip().endswith("?"):
            miss = unsupported(s, chunks, stems)
            if miss:
                out.append((s, miss))
    return out


def policy_message(items) -> str:
    return ("These statements about the policy are not supported by the policy passages or the customer's documents you were given this turn: "
            + "; ".join(f"{s[:80]!r} (not found: {', '.join(m)})" for s, m in items[:3])
            + ". Search the policy wording first, or leave them out, or say the wording does not say.")


def drop_policy(text: str, ctx) -> str:
    for s, _ in policy_problems(text, ctx):
        text = text.replace(s, "")
    text = re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    return text or CANNOT_CONFIRM


# ---------------------------------------------------------------- names: plans and hospitals must be the ones in the customer's documents
_HOSPITAL = re.compile(r"\b([A-Z][\w'&.-]*(?:\s+(?:of\s+)?[A-Z][\w'&.-]*){0,4}\s+(?:Hospital|Hospitals|Clinic|Nursing Home|Medical Centre|Medical Center))\b")


def entity_problems(text: str, ctx) -> list[str]:
    support = corpus(ctx, rules=False).lower()           # the rule tables name every plan: only this customer's documents and this turn's results count
    out = [p for p in E.PLANS if re.search(rf"\b{re.escape(p)}\b", text, re.I) and p.lower() not in support]
    out += [m.group(1) for m in _HOSPITAL.finditer(text) if m.group(1).lower() not in support and not m.group(1).lower().startswith(("the ", "a ", "your "))]
    return out


def drop_entities(text: str, ctx) -> str:
    bad = entity_problems(text, ctx)
    for s in sentences(text):
        if any(b.lower() in s.lower() for b in bad):
            text = text.replace(s, "")
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


# ---------------------------------------------------------------- repeats
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


def suppress_repeats(text: str, history: list[dict]) -> str:
    """Drops a note or next step (a sentence of five words or more without figures) that an earlier reply in this chat already had. If nothing would be left, the reply stays as it is."""
    seen = {_norm(s) for turn in history for s in sentences(turn.get("reply", ""))}
    kept, changed = [], False
    for line in text.split("\n"):
        stripped = re.sub(r"^\s*>\s?", "", line)
        parts = sentences(stripped) if stripped.strip() else [stripped]
        keep = [s for s in parts if not (len(s.split()) >= 5 and not re.search(r"\d", s) and _norm(s) in seen)]
        if len(keep) != len(parts):
            changed = True
        new = " ".join(keep)
        kept.append(("> " + new if line.lstrip().startswith(">") and new else new) if keep or not stripped.strip() else "")
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out if changed and out else text
