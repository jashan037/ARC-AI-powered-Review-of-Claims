"""Plain names for the policy's code labels, for the customer view only.

The officer wording (Excl03, Annexure B, C.1.c, Def. 5, "Estimated insurer payment") is locked by the golden files and stays as it is.
For a customer session `registry.render_final` passes the finished answer through `customerize_rendered`, so that the summary, every
"Show more" section, the full answer and the reference popups (label, clause, citation, excerpt) say what a clause is about instead of
where it sits in the wording. Text is only relabelled: no number, date or amount changes, and chunk keys are not touched here.
"""
from __future__ import annotations

import re

from .render import Rendered

# What each clause is about. Anything not listed falls back to its section (see _SECTION_NAMES).
_CLAUSES = {
    "A.1.2 Def. 5": "Policy definition: associated medical expenses",
    "A.1.2 Def. 30": "Policy definition: waiting period",
    "A.1.1 Def. 35": "Policy definition: pre-existing disease",
    "B.1.1": "Policy section: hospitalization cover",
    "B.1.1.1 Note iii": "Policy rule: room rent",
    "B.2.3": "Policy section: Protect Benefit",
    "C.1.a": "Policy rule: waiting period for pre-existing diseases",
    "C.1.b": "Policy rule: waiting period for specified illnesses and procedures",
    "C.1.b.vi": "Policy rule: waiting period for specified illnesses and procedures",
    "C.1.c": "Policy rule: 30-day waiting period",
    "C.3.k": "Policy rule: non-medical items",
    "E.1.6": "Policy section: time limit for sending documents",
    "E.1.7": "Policy section: documents needed for a claim",
}
_SECTION_NAMES = {"A": "definitions", "B": "benefits", "C": "exclusions", "D": "general conditions", "E": "claims"}
_ANNEXURES = {"B": "Your policy's list of non-medical items", "C": "Your plan's benefit chart"}
_EXCL = {"01": "pre-existing disease rule", "02": "specified illnesses and procedures rule", "03": "30-day waiting period rule"}

# A clause reference as it appears in the text: "C.1.b", "B.1.1.1 Note iii", "A.1.2 Def. 5", and the un-dotted spelling of the clause field ("A1.2 Def. 5").
# The dot after the letter (or a Def./Note suffix) is required, so an ICD-10 code such as E11.9 or C34.1 is never mistaken for a clause.
_REF = (r"(?:[A-E]\.\d+(?:\.\d+)*(?:\.[a-z]{1,4})*(?:\s+Note\s+[ivx]+|\s+Def\.\s*\d+)?"
        r"|[A-E]\d+(?:\.\d+)*(?:\s+Note\s+[ivx]+|\s+Def\.\s*\d+))")
_CITED = re.compile(rf"(?<![\w.])(?:(?i:Policy)\s+)?(?P<ref>{_REF})(?![\w])|(?<![\w.])(?:(?i:Policy)\s+)?(?i:Annexure)\s+(?P<annex>[A-Da-d])\b")


def clause_name(ref: str) -> str:
    """'C.1.c' or 'C1.c' -> 'Policy rule: 30-day waiting period'. Unlisted clauses are named after their section."""
    key = re.sub(r"^([A-Ea-e])(\d)", r"\1.\2", ref.strip())
    key = re.sub(r"\s+", " ", re.sub(r"Def\.\s*(\d+)", r"Def. \1", key))
    key = key[0].upper() + key[1:]
    if key in _CLAUSES:
        return _CLAUSES[key]
    if "Def." in key:
        return "Policy definition"
    if key.startswith("C.1"):
        return "Policy section: waiting periods"
    return f"Policy section: {_SECTION_NAMES.get(key[0], 'wording')}"


def _lower_first(name: str) -> str:
    return name[0].lower() + name[1:]


def _placed(text: str, start: int, name: str) -> str:
    """Capitalised where the name starts a phrase (start of text or line, after ';', '|', '**', '—'), lower case inside a sentence."""
    before = text[:start].rstrip(" ")
    return _lower_first(name) if before and (before[-1].islower() or before[-1] == ",") else name


def _rules(code: bool):
    """The rewrites in the order they apply. In a code block the removed width is padded back so the amounts stay in their column."""
    def cut(m):
        return " " * len(m.group(0)) if code else ""

    def named(m, text):
        if m.group("annex"):
            name = _ANNEXURES.get(m.group("annex").upper(), "Policy annexure")
        else:
            name = clause_name(m.group("ref"))
        return _placed(text, m.start(), name)

    def cited(text):
        out, last = [], 0
        for m in _CITED.finditer(text):
            new = named(m, text)
            old = m.group(0)
            out += [text[last:m.start()], new + (" " * max(len(old) - len(new), 0) if code else "")]
            last = m.end()
        return "".join(out) + text[last:]

    def pad(new):
        return lambda m: new + (" " * max(len(m.group(0)) - len(new), 0) if code else "")

    return [
        (re.compile(r"Code\s*[–-]\s*Excl\d{2}\s*"), cut),                                       # "Code – Excl03 i. Expenses ..." in a quoted clause
        (re.compile(r",\s*Excl\d{2}\)"), lambda m: pad(")")(m)),                                 # "(24-month waiting period, Excl02)" keeps its words
        (re.compile(r"\s*\(Excl\d{2}\)"), cut),                                                  # "(Excl03)"
        (re.compile(r"\|\s*Annexure B\s*\|"), lambda m: pad("| List no. |")(m)),                # the table column of the non-medical items
        (re.compile(r"\s*\(Annexure [A-D]\)"), cut),                                            # "Non-payable items (Annexure B)"
        (re.compile(r"\blisted in Annexure B as non-medical items", re.I), pad("on your policy's list of non-medical items")),
        (re.compile(r"Estimated insurer payment"), pad("Estimated payment")),
        (re.compile(r"What to check next"), pad("What to do next")),
        (re.compile(r"For the claims officer to review"), pad("A claims officer will look at this")),
        (re.compile(r"\bExcl(\d{2})\b"), lambda m: pad(_EXCL.get(m.group(1), "exclusion rule"))(m)),
        ("CITED", cited),
        (re.compile(r"\bDef\.\s*\d+"), pad("policy definition")),                              # a definition number left in a quoted passage
    ]


def _one(text: str, code: bool) -> str:
    for rule, how in _rules(code):
        text = how(text) if rule == "CITED" else rule.sub(how, text)
    return text


def customerize(text: str) -> str:
    """The customer wording of one text. Fenced code blocks keep their column alignment."""
    if not text:
        return text
    parts = text.split("```")
    return "```".join(_one(p, code=i % 2 == 1) for i, p in enumerate(parts))


def customerize_rendered(r: Rendered) -> Rendered:
    """A copy of a finished answer in customer wording. chunk_key, ids and statuses are left alone."""
    return Rendered(
        markdown=customerize(r.markdown),
        summary_markdown=customerize(r.summary_markdown),
        sections=[dict(s, **{k: customerize(s[k]) for k in ("title", "markdown") if isinstance(s.get(k), str)}) for s in r.sections],
        citations=[dict(c, **{k: customerize(c[k]) for k in ("citation", "label", "clause", "excerpt") if isinstance(c.get(k), str)}) for c in r.citations])
