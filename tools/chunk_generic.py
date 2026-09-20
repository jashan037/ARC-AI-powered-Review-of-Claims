"""Fallback chunker for documents that are not laid out like the Optima Secure wording
(add-on wordings, IRDAI circulars, prospectuses, CIS, forms).

Strategy: split each page's text on numbered/lettered headings when present, otherwise on paragraphs,
then pack into ~1,200 character chunks with a small overlap. Every chunk keeps its page number.
Usage: python chunk_generic.py doc.pdf --doc-id irdai-master-circular-2024 --doc-type regulation \
         --source-url https://... --authority primary --out irdai.jsonl
"""
import argparse, json, re, subprocess

ap = argparse.ArgumentParser()
ap.add_argument("pdf")
ap.add_argument("--doc-id", required=True)
ap.add_argument("--doc-type", default="policy_wording")
ap.add_argument("--uin", default=None)
ap.add_argument("--effective-from", default=None)
ap.add_argument("--source-url", default=None)
ap.add_argument("--authority", default="primary", help="primary | secondary")
ap.add_argument("--max-chars", type=int, default=1200)
ap.add_argument("--overlap", type=int, default=150)
ap.add_argument("--out", required=True)
a = ap.parse_args()

raw = subprocess.run(["pdftotext", "-layout", a.pdf, "-"], capture_output=True, text=True).stdout
pages = raw.split("\f")
HEAD = re.compile(r"^\s*((?:\d{1,2}(?:\.\d{1,2}){0,3}|[A-Z]\.|Annexure [A-Z0-9]+|SECTION [A-Z]\.?|Chapter [IVX0-9]+)\b.*)$")

# drop running headers/footers: lines that repeat on at least half of the pages
from collections import Counter
_seen = Counter()
for ptxt in pages:
    for ln in {re.sub(r"\d+", "#", l.strip()) for l in ptxt.split("\n") if l.strip()}:
        _seen[ln] += 1
NOISE = {k for k, v in _seen.items() if len(pages) >= 4 and v >= len(pages) * 0.5}

blocks = []                      # (page, heading, text)
heading = None
for pno, ptxt in enumerate(pages, start=1):
    buf = []
    for ln in ptxt.split("\n"):
        s = ln.strip()
        if not s:
            buf.append("")
            continue
        if re.sub(r"\d+", "#", s) in NOISE:
            continue
        m = HEAD.match(s)
        if m and len(s) < 110 and not s.endswith(","):
            if "".join(buf).strip():
                blocks.append((pno, heading, "\n".join(buf))); buf = []
            heading = s
        buf.append(s)
    if "".join(buf).strip():
        blocks.append((pno, heading, "\n".join(buf)))

chunks, n = [], 0
for pno, head, text in blocks:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    start = 0
    while start < len(text):
        end = min(len(text), start + a.max_chars)
        if end < len(text):                       # break at a sentence/paragraph boundary
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + a.max_chars // 2:
                end = cut + 1
        piece = text[start:end].strip()
        if len(piece) > 40:
            n += 1
            cid = f"{a.doc_id}-p{pno}-{n:04d}"
            chunks.append(dict(chunk_id=cid, chunk_key=f"{a.doc_id}:{cid}", doc_id=a.doc_id, doc_type=a.doc_type,
                               uin=a.uin, effective_from=a.effective_from, authority=a.authority,
                               source_url=a.source_url, section_title=head, clause=head, title=(head or "")[:90],
                               page_start=pno, page_end=pno, chunk_type="passage", text=piece,
                               citation=f"{a.doc_id}, p.{pno}" + (f" ({head[:50]})" if head else ""), char_count=len(piece)))
        if end >= len(text):
            break
        start = max(end - a.overlap, start + 1)

with open(a.out, "w", encoding="utf-8") as f:
    for c in chunks:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
print(f"{len(chunks)} chunks from {len(pages)} pages -> {a.out}")
