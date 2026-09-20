from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path


def best_window(text: str, query: str, n: int) -> str:
    """The text itself if it fits in n characters; otherwise the n-character window that best matches the query.

    Long clauses (a list of 45 conditions and procedures, a table of limits) used to be cut at their first n characters, so the model never saw
    the entry it needed. Query words are matched by their first five letters. A word that occurs once in the text counts more than one that
    occurs everywhere, and two query words that sit next to each other in the text in the same order count extra (a phrase match).
    With no matching word the head of the text is returned, as before.
    """
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= n:
        return t
    q_words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2 and w not in _STOP]
    stems = {w[:5] for w in q_words}
    q_pairs = {(a[:5], b[:5]) for a, b in zip(q_words, q_words[1:])}
    words = [(m.start(), m.group()) for m in re.finditer(r"[a-z0-9]+", t.lower())]
    stem_at = [next((st for st in stems if w.startswith(st)), None) for _, w in words]
    hits = [(words[i][0], stem_at[i]) for i in range(len(words)) if stem_at[i]]
    if not hits:
        return t[:n].rsplit(" ", 1)[0] + " ..."
    count = Counter(st for _, st in hits)
    phrase_at = [(words[i][0], (stem_at[i], stem_at[i + 1])) for i in range(len(words) - 1) if stem_at[i] and (stem_at[i], stem_at[i + 1]) in q_pairs]
    best_start, best_score = 0, -1.0
    for pos in [p for p, _ in hits] + [p for p, _ in phrase_at]:
        start = max(0, min(pos - n // 3, len(t) - n))
        score = (sum(1 / count[st] for st in {st for p, st in hits if start <= p < start + n})
                 + len({pair for p, pair in phrase_at if start <= p < start + n}))      # each distinct phrase counts once, however often it repeats
        if score > best_score + 1e-9:
            best_start, best_score = start, score
    inside = [(p, 1 / count[st]) for p, st in hits if best_start <= p < best_start + n]
    if inside:   # centre the window on the distinctive matches (weighted by rarity) instead of leaving them at its edge
        centre = int(sum(p * w for p, w in inside) / sum(w for _, w in inside)) + 8
        best_start = max(0, min(centre - n // 2, len(t) - n))
    start = 0 if best_start == 0 else t.find(" ", best_start) + 1
    end = min(len(t), start + n)
    window = t[start:end] if end == len(t) else t[start:end].rsplit(" ", 1)[0]
    return ("… " if start else "") + window + (" …" if start + len(window) < len(t) else "")


@dataclass
class Chunk:
    chunk_key: str
    chunk_id: str
    doc_id: str
    uin: str | None
    clause: str
    title: str
    citation: str
    page_start: int | None
    text: str
    score: float = 0.0

    def short(self, n: int = 700, query: str | None = None) -> dict:
        """The tool-result form of a chunk. With a query, a long chunk is shown as the window that best matches it instead of its first n characters."""
        t = re.sub(r"\s+", " ", self.text).strip()
        excerpt = best_window(t, query, n) if query else (t if len(t) <= n else t[: n].rsplit(" ", 1)[0] + " ...")
        return dict(chunk_key=self.chunk_key, citation=self.citation, clause=self.clause, title=self.title,
                    excerpt=excerpt, score=round(self.score, 3))

    def as_dict(self) -> dict:
        return asdict(self)


def chunk_from_record(r: dict, score: float = 0.0) -> Chunk:
    return Chunk(chunk_key=r["chunk_key"], chunk_id=r["chunk_id"], doc_id=r["doc_id"], uin=r.get("uin"),
                 clause=r.get("clause") or "", title=r.get("title") or "", citation=r.get("citation") or r["chunk_id"],
                 page_start=r.get("page_start"), text=r["text"], score=score)


class Retriever(ABC):
    @abstractmethod
    def search(self, query: str, uin: str | None = None, top_k: int = 5) -> list[Chunk]: ...

    @abstractmethod
    def get_by_chunk_id(self, chunk_id: str, uin: str | None = None) -> Chunk | None: ...

    @abstractmethod
    def get_by_key(self, chunk_key: str) -> Chunk | None: ...


# ---------------------------------------------------------------------------------------------
# Local BM25 (development, tests, and the "keyword-only" baseline for retrieval evaluation)
# ---------------------------------------------------------------------------------------------
_STOP = set("a an and are as at be by can do does for from has have how i if in is it its of on or that the this to was what when where which who will with would my me we you your not any".split())
_SYNONYMS = {  # tiny hand-made map so the keyword baseline is not hopeless; Azure hybrid search does not need it
    "knee replacement": "joint replacement surgeries", "hip replacement": "joint replacement surgeries",
    "piles": "haemorrhoids", "gallstone": "gall bladder cholecystectomy", "gallstones": "gall bladder cholecystectomy",
    "lasik": "refractive error dioptres", "spectacles": "refractive error", "ivf": "infertility sterility",
    "pregnancy": "maternity childbirth", "delivery": "maternity childbirth", "cosmetic": "cosmetic plastic surgery",
    "gloves": "non-medical annexure", "masks": "non-medical annexure", "waiting": "waiting period",
    "preexisting": "pre-existing", "ped": "pre-existing disease",
}


def _tok(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _STOP and len(t) > 1]


class LocalRetriever(Retriever):
    def __init__(self, path: Path):
        self.records = [json.loads(l) for l in open(path, encoding="utf-8")]
        self.by_key = {r["chunk_key"]: r for r in self.records}
        self._docs = []
        for r in self.records:
            toks = _tok(r.get("title", "")) * 3 + _tok(r.get("clause", "")) * 2 + _tok(r["text"])
            self._docs.append(Counter(toks))
        self._len = [sum(c.values()) for c in self._docs]
        self._avg = sum(self._len) / max(1, len(self._len))
        df = Counter()
        for c in self._docs:
            df.update(c.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, uin: str | None = None, top_k: int = 5) -> list[Chunk]:
        q = query.lower()
        for k, v in _SYNONYMS.items():
            if k in q:
                q += " " + v
        qt = _tok(q)
        scored = []
        for i, r in enumerate(self.records):
            if uin and r.get("uin") and r["uin"] != uin:
                continue
            tf, dl, s = self._docs[i], self._len[i], 0.0
            for t in qt:
                f = tf.get(t, 0)
                if f:
                    s += self._idf.get(t, 0) * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * dl / self._avg))
            if s > 0:
                scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        return [chunk_from_record(r, s) for s, r in scored[:top_k]]

    def get_by_chunk_id(self, chunk_id: str, uin: str | None = None) -> Chunk | None:
        for r in self.records:
            if r["chunk_id"] == chunk_id and (not uin or not r.get("uin") or r["uin"] == uin):
                return chunk_from_record(r)
        return None

    def get_by_key(self, chunk_key: str) -> Chunk | None:
        r = self.by_key.get(chunk_key)
        return chunk_from_record(r) if r else None
