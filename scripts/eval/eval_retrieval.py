"""Retrieval quality on data/rag_eval_questions.json.

    python scripts/eval/eval_retrieval.py                     # local BM25 baseline (no Azure)
    RETRIEVER=azure python scripts/eval/eval_retrieval.py     # your Azure AI Search index

Reports hit@k (any expected chunk in the top k), full recall@k (all of them), MRR, and the top score
for unanswerable questions so you can choose an abstention threshold.
"""
import argparse, json, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.config import settings
from app.retrieval.azure_search import get_retriever

ap = argparse.ArgumentParser(); ap.add_argument("--k", type=int, default=5); ap.add_argument("--verbose", action="store_true")
args = ap.parse_args()
qs = json.load(open(settings.data_dir / "rag_eval_questions.json"))["questions"]
r = get_retriever()
hit = full = 0; rr = []; ans_scores = []; unans_scores = []; n = 0
for q in qs:
    res = r.search(q["question"], uin=settings.default_uin, top_k=args.k)
    ids = [c.chunk_id for c in res]
    top = res[0].score if res else 0.0
    exp = q["expected_chunk_ids"]
    if not exp:
        unans_scores.append(top)
        if args.verbose: print(f"[unanswerable] {q['id']} top score {top:.2f}  {q['question']}")
        continue
    n += 1
    got = [i for i in exp if i in ids]
    hit += bool(got); full += len(got) == len(exp)
    rank = next((ids.index(i) + 1 for i in exp if i in ids), None)
    rr.append(1 / rank if rank else 0)
    ans_scores.append(top)
    if args.verbose or not got:
        print(f"{'ok  ' if got else 'MISS'} {q['id']} expected {exp} got {ids[:args.k]}  | {q['question']}")
print(f"\nretriever={settings.retriever} k={args.k} answerable={n}")
print(f"hit@{args.k}: {hit}/{n} = {hit/n:.0%}   full recall@{args.k}: {full}/{n} = {full/n:.0%}   MRR: {statistics.mean(rr):.2f}")
print(f"top score, answerable: median {statistics.median(ans_scores):.2f}   unanswerable: max {max(unans_scores):.2f} (n={len(unans_scores)})")
