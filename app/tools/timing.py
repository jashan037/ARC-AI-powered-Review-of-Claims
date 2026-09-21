"""Timing guard: a waiting period and the policy period are judged on the date of TREATMENT, and nobody can wait for a treatment that already happened.

Two things the model gets wrong when it reasons about dates by itself:

  * it tells the customer to "wait until 15 Mar 2026" for a treatment that took place in 2025. Waiting is not something the customer
    can do about a past date; the answer must say whether that date fell before or after the day the waiting period was served.
  * it states the accident exception as settled ("accidents are exempt, so you're fine") although the documents record an illness.
    The exception is a condition: "unless it was caused by an accident".

Both are decided here from the engine's dates, never from the model's arithmetic.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re

from . import claims_engine as E
from .fmt import d_fmt
from .number_guard import scan

_WAIT_ADVICE = re.compile(
    r"\byou(?:'ll| will)?\s+(?:need|have|has)\s+to\s+wait\b|\byou\s+(?:must|should|can(?:not|'t)?|could|would)\s+wait\b|\bwait(?:ing)?\s+(?:until|till|for|another|a further)\b"
    r"|\byou(?:'d| would)?\s+(?:then\s+)?(?:have|need)\s+to\s+wait\b|\bwait\s+out\b", re.I)
_MONTH_YEAR = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?,?\s+(\d{4})\b", re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
# the claim says this was an illness, and the reply says the opposite
_SETTLED_ACCIDENT = re.compile(
    r"\b(?:your|this|the)\s+(?:claim|treatment|admission|surgery|operation|condition|illness|hospitali[sz]ation)\b[^.?!]{0,40}\b(?:was|is)\b[^.?!]{0,25}\ban accident\b"
    r"|\b(?:since|because|as)\s+(?:it|this|yours|your claim)\s+(?:was|is)\s+an accident\b|\byour accident\b", re.I)
_CONDITION_WORD = re.compile(r"\b(?:if|unless|whether|would|wouldn't|in case|suppose|had it|were it|any accident)\b", re.I)
_EXEMPT = re.compile(r"\baccidents?\s+(?:are|is)\s+(?:exempt(?:\s+from\s+(?:this|the)\s+waiting\s+period)?|an?\s+exception|excluded from this)\b", re.I)
_CONDITIONAL = "this waiting period wouldn't apply if the condition was caused by an accident"


def treatment_dates(ctx) -> list[dt.date]:
    """The treatment dates this question is about: the full dates and months the customer named, or the admission date on the claim."""
    out: list[dt.date] = []
    q = ctx.question or ""
    for (y, m, d), _raw in scan(q)["dates"]:
        if y and 1990 < y < 2100:
            try:
                out.append(dt.date(y, m, d))
            except ValueError:
                continue
    stripped = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}\b|\b[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b", " ", q)
    for mon, yr in _MONTH_YEAR.findall(stripped):
        mi, y = _MONTHS.index(mon[:3].lower()) + 1, int(yr)
        out.append(dt.date(y, mi, calendar.monthrange(y, mi)[1]))     # the last day of the month: if even that is past, the whole month is
    if not out:
        claim = ctx.claim or {}
        if claim.get("admission_datetime"):
            out.append(dt.date.fromisoformat(claim["admission_datetime"][:10]))
    return out


def all_past(ctx) -> dt.date | None:
    """The latest treatment date this question is about, when every one of them is already in the past. None otherwise."""
    dates = treatment_dates(ctx)
    if not dates or any(d >= E.today() for d in dates):
        return None
    return max(dates)


def _served_dates(ctx) -> list[str]:
    """The days on which a not-yet-served waiting period is served, as the tools returned them this turn."""
    out = []
    for src in (ctx.results.get("waiting"), (ctx.results.get("assessment") or {}).get("waiting")):
        for k in (src or {}).get("checks", []):
            if k.get("eligible_from"):
                out.append(k["eligible_from"])
    return list(dict.fromkeys(out))


def _sentence(ctx, when: dt.date) -> str:
    served = _served_dates(ctx)
    if served:
        return (f"Your documents put that treatment on {d_fmt(when.isoformat())}, before {d_fmt(served[0])}, the day that waiting period was served, "
                "so waiting is not something you can do about it now.")
    return f"Your documents put that treatment on {d_fmt(when.isoformat())}, which has already passed, so there is nothing left to wait for."


def problems(text: str, ctx) -> list[str]:
    out = []
    when = all_past(ctx)
    if when and _WAIT_ADVICE.search(text or ""):
        out.append(f"That treatment date ({d_fmt(when.isoformat())}) has already passed: never tell the customer to wait. "
                   "Say whether the date falls before or after the day the waiting period was served.")
    if _EXEMPT.search(text or ""):
        out.append(f"Do not state the accident exception as settled: write it as a condition, '{_CONDITIONAL}'.")
    if (ctx.claim or {}).get("is_accident") is False and _settled(text):
        out.append("Your documents record this as an illness, not an accident: never say it was an accident. The exception is a condition, 'unless it was caused by an accident'.")
    return out


def _settled(text: str) -> bool:
    """The reply asserts that THIS claim was an accident, and not as a condition ("if it was caused by an accident")."""
    from .number_guard import split_sentences
    return any(_SETTLED_ACCIDENT.search(s) and not _CONDITION_WORD.search(s) for line in (text or "").split("\n") for s in split_sentences(line))


def _drop(text: str, pattern: re.Pattern, keep: re.Pattern | None = None) -> str:
    """text without the sentences that match pattern (a sentence that also matches `keep` stays)."""
    from .number_guard import split_sentences
    gone = lambda s: bool(pattern.search(s)) and not (keep is not None and keep.search(s))   # noqa: E731
    kept = [" ".join(s for s in split_sentences(line) if not gone(s)) for line in (text or "").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def repair(text: str, ctx) -> str:
    """Drop the sentence that told the customer to wait for something already past, or that made the accident exception settled, and say it in code instead."""
    when = all_past(ctx)
    if when and _WAIT_ADVICE.search(text or ""):
        text = _drop(text, _WAIT_ADVICE)
        text = f"{text}\n\n{_sentence(ctx, when)}".strip()
    text = _EXEMPT.sub(_CONDITIONAL, text or "")
    if (ctx.claim or {}).get("is_accident") is False and _settled(text):
        text = _drop(text, _SETTLED_ACCIDENT, keep=_CONDITION_WORD)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
