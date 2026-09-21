"""The format guard: the model chooses the formatting, this keeps it quiet and readable. All in code.

problems() lists what to fix in a reply (sent back once, saying exactly what); repair() fixes the text itself when the second reply still has them:
headings and emoji stripped, lists flattened, a table that is not allowed turned into plain lines, a repeated table replaced by one sentence, extra bold and
quotes removed, a reply over the hard cap cut at a block boundary.

Question kinds decide what is allowed:
  fact      a simple fact or a yes/no about the documents      30 words   no table
  topic     one topic (also a what-if)                          90 words   no table
  explain   "explain my claim", "why is my payment lower", a list the customer asked for   180 words   a table of at least 3 data rows
  hard cap 200 words for everything.
"""
from __future__ import annotations

import re

FACT_CAP, TOPIC_CAP, EXPLAIN_CAP, HARD_CAP = 30, 90, 180, 200
MAX_BOLD = 3
MIN_TABLE_ROWS = 3

_WHAT_IF = re.compile(r"\bwhat if\b|\bwhat would happen\b|\bsuppose\b|\bif (?:the|my|i|it)\b.*\b(?:was|were|had|paid|cost)\b|\bwould (?:it|my|the)\b.*\bif\b", re.I)
_EXPLAIN = re.compile(r"\bexplain\b|\bwhy (?:is|was|are|were) (?:my|the) (?:payment|claim|amount|estimate)\b|\bwhy (?:is|was) (?:it|my payment) (?:lower|less|reduced)\b|\bbreak ?down\b|\bwalk me through\b"
                      r"|\bsummar(?:y|ise|ize)\b|\bhow (?:is|was|did) (?:it|my (?:claim|payment|estimate)|the (?:payment|estimate)) (?:calculated|worked|come)\b|\bhow was (?:this|that) (?:worked|calculated)\b", re.I)
_LIST = re.compile(r"\b(?:list|show)\b.*\b(?:all|every|full)\b|\bfull list\b|\bwhich (?:items|extras|charges) (?:are|aren't|are not)\b|\ball (?:the )?(?:non-?medical|items|extras)\b|\bwhat (?:items|extras) (?:are|aren't)\b", re.I)
_FACT = re.compile(r"^(?:what(?:'s| is| was| are)|when|where|which|who|how (?:old|many|long)|do i|does my|did i|was my|is my|am i|are my|can i)\b", re.I)
# only a question about a fact written in the customer's documents is a "fact" question (the 30-word cap); documents, coverage and the rest are one-topic questions
_FACT_NOUN = re.compile(r"\b(?:name|hospital|policy (?:number|start|end|period|expir\w*)|expir\w*|valid (?:till|until)|renewal|end date|start date|sum insured|cover amount|plan|age|old|address|diagnosis|procedure|"
                        r"days|admitted|admission|discharged?|discharge date|room rent limit|room limit|icu|deductible|co-?pay\w*|premium|bonus|claim (?:number|id)|policy in force|active|amount claimed|bill total)\b", re.I)
_MONEY = re.compile(r"\b(?:paid|payment|pay|payable|reduced|deduct\w*|estimate|claim|covered|cover|eligible|get|receive)\b", re.I)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍←-⇿⌀-⏿]")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_HR = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.M)
_FENCE = re.compile(r"^\s*```", re.M)
_NESTED = re.compile(r"^(?: {2,}|\t+)(?:[-*+]|\d+[.)])\s+", re.M)
_BOLD = re.compile(r"\*\*[^*\n]+?\*\*")
_QUOTE = re.compile(r"^\s*>\s?", re.M)
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def question_kind(question: str) -> str:
    q = (question or "").strip()
    if _WHAT_IF.search(q):
        return "topic"
    if _EXPLAIN.search(q) or _LIST.search(q):
        return "explain"
    if _FACT.match(q) and _FACT_NOUN.search(q) and len(q.split()) <= 12 and not _MONEY.search(q.replace("co-pay", "copayment")):
        return "fact"
    return "topic"


def cap(kind: str) -> int:
    return {"fact": FACT_CAP, "topic": TOPIC_CAP, "explain": EXPLAIN_CAP}[kind]


def words(text: str) -> int:
    return len(re.findall(r"[^\s|]+", re.sub(r"[*_`>#]|^\s*[-+]\s|:?-{3,}:?", " ", text, flags=re.M)))


def tables(text: str) -> list[dict]:
    """Every table: {'start', 'end' (line indexes), 'header': [cells], 'rows': [[cells]]}."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and _SEP.match(lines[i + 1]) and "|" in lines[i + 1] + lines[i]:
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]   # noqa: E731
            out.append(dict(start=i, end=j, header=cells(lines[i]), rows=[cells(l) for l in lines[i + 2:j]]))
            i = j
        else:
            i += 1
    return out


def _labels(t: dict) -> set[str]:
    return {re.sub(r"[\W_]+", " ", r[0]).strip().lower() for r in t["rows"] if r and r[0]}


def repeats(t: dict, history: list[dict]) -> bool:
    """The table repeats one shown earlier in this chat: the same header, or mostly the same row labels."""
    mine, head = _labels(t), [re.sub(r"\W+", " ", h).strip().lower() for h in t["header"]]
    for turn in history:
        for old in tables(turn.get("reply", "")):
            if head == [re.sub(r"\W+", " ", h).strip().lower() for h in old["header"]] and mine == _labels(old):
                return True
            theirs = _labels(old)
            if mine and theirs and len(mine & theirs) / len(mine | theirs) >= 0.6:
                return True
    return False


def _amount(cell: str):
    """A signed rupee amount in a cell: '−₹49,875' -> -49875, '₹1,84,500' -> 184500, None when it is not one."""
    c = cell.replace("**", "").strip()
    m = re.fullmatch(r"([−\-–]?)\s*(?:₹|Rs\.?)?\s*([\d,]+(?:\.\d+)?)", c)
    return None if not m else (-1 if m.group(1) else 1) * float(m.group(2).replace(",", ""))


def adds_up(t: dict) -> bool:
    """A breakdown must add up: the amounts above a bold total row (the first is the start, minus signs subtract) sum to that total. A table without such a row is not checked."""
    rows = [(r[0], _amount(r[-1]), all(re.fullmatch(r"\*\*.+\*\*", c.strip()) for c in r if c.strip())) for r in t["rows"] if r]
    if len(rows) < 3 or not rows[-1][2] or rows[-1][1] is None:
        return True
    parts = [a for _, a, _ in rows[:-1]]
    return None in parts or abs(sum(parts) - rows[-1][1]) < 1


def blockquotes(text: str) -> int:
    n, inside = 0, False
    for line in text.split("\n"):
        q = bool(_QUOTE.match(line))
        n += q and not inside
        inside = q
    return n


def bold_spans(text: str) -> int:
    """Bold spans outside tables (the bold total row of a breakdown is part of the table)."""
    return len(_BOLD.findall("\n".join(l for l in text.split("\n") if "|" not in l)))


def problems(text: str, question: str, history: list[dict]) -> list[str]:
    kind, out = question_kind(question), []
    ts = tables(text)
    for t in ts:
        if kind != "explain":
            out.append("Do not use a table for this question: answer in a sentence or two (a table only for a payment breakdown or a list the customer asked for).")
            break
        if len(t["rows"]) < MIN_TABLE_ROWS:
            out.append(f"The table has {len(t['rows'])} data rows: use a table only with at least {MIN_TABLE_ROWS} data rows, otherwise write a sentence.")
            break
    if any(not adds_up(t) for t in ts):
        out.append("The table does not add up: the amounts above the total must sum to it (start from the bill, subtract only what is really taken off; amounts that are waiting for a document are not subtracted). Fix the rows or write a sentence.")
    if any(repeats(t, history) for t in ts):
        out.append("This table repeats one already shown earlier in this chat: refer to it in words instead.")
    if _HEADING.search(text):
        out.append("Remove the heading: write a paragraph.")
    if _HR.search(text):
        out.append("Remove the horizontal rule.")
    if _FENCE.search(text):
        out.append("Remove the code block: write plain text.")
    if _EMOJI.search(text):
        out.append("Remove the emoji and symbols.")
    if _NESTED.search(text):
        out.append("Remove the nested list: one level only.")
    if bold_spans(text) > MAX_BOLD:
        out.append(f"Use at most {MAX_BOLD} bold spans: bold only the key figure or fact.")
    if blockquotes(text) > 1:
        out.append("Use at most one quoted line (>), for the single next step or one caution.")
    n = words(text)
    limit = cap(kind)
    if n > HARD_CAP:
        out.append(f"The reply is {n} words: the hard limit is {HARD_CAP}. Cut it.")
    elif n > limit:
        out.append(f"The reply is {n} words: for this kind of question the limit is {limit}. Lead with the answer and cut the rest.")
    return out


def _table_as_lines(lines: list[str], t: dict) -> list[str]:
    def clean(c):
        return c.replace("**", "").strip()
    return [f"- {clean(r[0])}: {', '.join(clean(c) for c in r[1:] if c.strip())}" for r in t["rows"] if r and r[0].strip()]


def repair(text: str, question: str, history: list[dict]) -> str:
    kind = question_kind(question)
    text = _EMOJI.sub("", text)
    text = _HEADING.sub("", text)
    text = _HR.sub("", text)
    text = _FENCE.sub("", text)
    text = _NESTED.sub("- ", text)
    lines = text.split("\n")
    for t in reversed(tables("\n".join(lines))):
        block = lines[t["start"]:t["end"]]
        if repeats(t, history):
            lines[t["start"]:t["end"]] = ["I've already shown that breakdown above."]
        elif not adds_up(t):
            keep = [k for k in range(len(t["rows"]) - 1) if adds_up(dict(t, rows=[r for j, r in enumerate(t["rows"]) if j != k]))]
            if keep and len(t["rows"]) - 1 >= MIN_TABLE_ROWS:                     # dropping one row makes it add up: that row did not belong
                lines[t["start"] + 2 + keep[-1]] = None
                lines = [ln for ln in lines if ln is not None]
            else:
                lines[t["start"]:t["end"]] = _table_as_lines(block, t)
        elif kind != "explain" or len(t["rows"]) < MIN_TABLE_ROWS:
            lines[t["start"]:t["end"]] = _table_as_lines(block, t)
    text = "\n".join(lines)
    seen = 0

    def keep_first(m):
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= MAX_BOLD else m.group(0)[2:-2]
    text = "\n".join(l if "|" in l else _BOLD.sub(keep_first, l) for l in text.split("\n"))
    quotes = 0
    out, inside = [], False
    for line in text.split("\n"):
        q = bool(_QUOTE.match(line))
        quotes += q and not inside
        inside = q
        out.append(_QUOTE.sub("", line) if q and quotes > 1 else line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    blocks = re.split(r"\n{2,}", text)
    while len(blocks) > 1 and words("\n\n".join(blocks)) > HARD_CAP:
        blocks.pop()
    return "\n\n".join(blocks).strip()
