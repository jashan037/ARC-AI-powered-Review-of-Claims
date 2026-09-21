"""The number guard for direct answers: every amount, percentage, date and count in a reply must come from this turn's tool results.

A direct answer is free text written by the model, so the numbers in it are checked against what the tools returned in the same turn. Formats are
normalised before comparing (₹1,22,125 = 122125 = 122125.0; 62.5% = 0.625; "10 Sep 2025" = 2025-09-10 = 10/09/2025). Counts of items may be the length
of a list a tool returned. A number that is not found is reported once for a rewrite; when it is still there, the sentence that holds it is dropped,
or, when nothing is left, a sentence built in code from the tool result takes its place (registry._final).
"""
from __future__ import annotations

import re

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_SLASH = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)")
_DMY = re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON},?\s+(\d{{4}})(?!\d)", re.I)
_MDY = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?!\d)", re.I)
_DM = re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}(?!\s*,?\s*\d{{4}})", re.I)
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_NUM = re.compile(r"(?<![\w.\-])(?:₹|Rs\.?\s*)?(\d[\d,]*(?:\.\d+)?)(?:\s*(?:%|percent|per cent))?(?![\w\-]|\.\d)")
_LIST_MARK = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+", re.M)
_ABBR = {"e.g", "i.e", "def", "rs", "no", "vs", "p", "pp", "approx", "sec", "cl", "mr", "mrs", "dr", "st", "fig", "cf", "etc"}


def _r(v: float) -> float:
    return round(float(v), 2)


def scan(text: str) -> dict:
    """The dates, times and plain numbers in a text, as normalised values, each with the raw words it came from: {'dates': [(key, raw)], 'times': [...], 'nums': [...]}."""
    t = _LIST_MARK.sub("", text or "")
    dates, times, nums = [], [], []

    def take(pattern, build):
        nonlocal t
        for m in pattern.finditer(t):
            try:
                dates.append((build(m), m.group(0)))
            except ValueError:
                pass
        t = pattern.sub(" ", t)

    take(_ISO, lambda m: (int(m[1]), int(m[2]), int(m[3])))
    take(_SLASH, lambda m: (int(m[3]), int(m[2]), int(m[1])))
    take(_DMY, lambda m: (int(m[3]), _MONTHS[m[2].lower()], int(m[1])))
    take(_MDY, lambda m: (int(m[3]), _MONTHS[m[1].lower()], int(m[2])))
    take(_DM, lambda m: (None, _MONTHS[m[2].lower()], int(m[1])))
    for m in _TIME.finditer(t):
        times.append((f"{int(m[1]):02d}:{m[2]}", m.group(0)))
    t = _TIME.sub(" ", t)
    for m in _NUM.finditer(t):
        nums.append((_r(m[1].replace(",", "")), m.group(0).strip()))
    return dict(dates=dates, times=times, nums=nums)


def allowed_from(outputs: list) -> dict:
    """Everything the tools returned this turn, normalised: numbers (with the percent form of a ratio and list lengths), dates, times."""
    nums, dates, times = set(), set(), set()

    def add_text(s: str):
        found = scan(s)
        nums.update(v for v, _ in found["nums"])
        times.update(v for v, _ in found["times"])
        for (y, mo, d), _raw in found["dates"]:
            dates.add((y, mo, d))
            dates.add((None, mo, d))
            if y:
                nums.add(float(y))

    def walk(o):
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            nums.add(_r(o))
            if isinstance(o, float):
                nums.add(float(round(o)))                  # the assessment shows whole rupees: 129333.33 is shown as ₹1,29,333
            if isinstance(o, float) and 0 < o <= 1:
                nums.add(_r(o * 100))
        elif isinstance(o, str):
            add_text(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            nums.add(float(len(o)))
            for v in o:
                walk(v)

    for o in outputs:
        walk(o)
    return dict(nums=nums, dates=dates, times=times)


def offenders(text: str, allowed: dict) -> list[str]:
    """The raw numbers, dates and times of text that no tool returned this turn, in order and without repeats."""
    found = scan(text)
    # a date with a year must match exactly (a right day and month in the wrong year is wrong); a date without a year matches on day and month
    bad = [raw for (key, raw) in found["dates"] if key not in allowed["dates"] and (key[0] is not None or (None, key[1], key[2]) not in allowed["dates"])]
    bad += [raw for (v, raw) in found["times"] if v not in allowed["times"]]
    bad += [raw for (v, raw) in found["nums"] if v not in allowed["nums"]]
    return list(dict.fromkeys(bad))


# A rupee amount written without the rupee sign or Indian grouping ("12000.0", "20500", "37,875"). Years and dates are taken out first.
_BARE = re.compile(r"(?<![\w.,\-])(₹\s?)?(\d{1,3}(?:,\d{2,3})+|\d{4,})(\.\d+)?(?!\d|,\d|\.\d|\s*%)")


def _indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail, parts = s[:-3], s[-3:], []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + parts + [tail])


def reformat_amounts(text: str) -> str:
    """Rupee amounts as ₹1,22,125. Presentation only: the value is unchanged, so the number guard still sees the same number."""
    def one(m):
        raw, sign, dec = m.group(2).replace(",", ""), m.group(1), m.group(3)
        if not sign and not dec and "," not in m.group(2) and 1900 <= int(raw) <= 2100:
            return m.group(0)                                              # a year
        return "₹" + _indian(int(round(float(raw + (dec or "")))))         # whole rupees, as the assessment shows them
    keep = {}
    def hold(m):
        keep[f"\x00{len(keep)}\x00"] = m.group(0)
        return f"\x00{len(keep) - 1}\x00"
    t = text or ""
    for pat in (_ISO, _SLASH, _DMY, _MDY, _TIME):
        t = pat.sub(hold, t)
    t = _BARE.sub(one, t)
    for i, v in enumerate(keep.values()):
        t = t.replace(f"\x00{i}\x00", v)
    return t


def split_sentences(line: str) -> list[str]:
    parts, start = [], 0
    for m in re.finditer(r"([A-Za-z.]*)([.!?])\s+(?=[A-Z0-9₹\"“'(])", line):
        if m.group(2) == "." and (m.group(1).lower().rstrip(".") in _ABBR or (len(m.group(1)) == 1 and m.group(1).isupper())):
            continue
        parts.append(line[start:m.end()].strip())
        start = m.end()
    parts.append(line[start:].strip())
    return [p for p in parts if p]


def drop_sentences(text: str, allowed: dict) -> str:
    """text without the sentences (or list lines) that hold a number no tool returned. May be empty."""
    kept = []
    for line in text.split("\n"):
        if _LIST_MARK.match(line):
            if not offenders(line, allowed):
                kept.append(line)
            continue
        kept.append(" ".join(s for s in split_sentences(line) if not offenders(s, allowed)))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
