"""Embed and upload chunk files.

    python scripts/upload_chunks.py                                   # data/policy_clauses.jsonl
    python scripts/upload_chunks.py path/to/other_chunks.jsonl ...    # e.g. kb/chunks/all_chunks.jsonl from the KB pack
"""
import hashlib, json, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

from app.config import settings
from app.retrieval.azure_search import embed

paths = [Path(p) for p in sys.argv[1:]] or [settings.data_dir / "policy_clauses.jsonl"]
records = [json.loads(l) for p in paths for l in open(p, encoding="utf-8")]
client = SearchClient(settings.search_endpoint, settings.search_index, AzureKeyCredential(settings.search_key))

seen, docs = set(), []
for r in records:
    key = re.sub(r"[^A-Za-z0-9_\-=]", "_", r["chunk_key"])
    if key in seen:
        key += "_" + hashlib.md5(r["chunk_key"].encode()).hexdigest()[:6]
    seen.add(key)
    docs.append(dict(id=key, chunk_key=r["chunk_key"], chunk_id=r["chunk_id"], doc_id=r["doc_id"], uin=r.get("uin"),
                     effective_from=r.get("effective_from"), authority=r.get("authority") or "primary",
                     doc_type=r.get("doc_type") or "policy_wording", chunk_type=r.get("chunk_type"), clause=r.get("clause") or "",
                     title=r.get("title") or "", text=r["text"], citation=r.get("citation") or r["chunk_id"], page_start=r.get("page_start")))

BATCH = 16
for i in range(0, len(docs), BATCH):
    batch = docs[i:i + BATCH]
    inputs = [f"{d['title']}\n{d['clause']}\n{d['text']}"[:12000] for d in batch]
    for attempt in range(5):
        try:
            vecs = embed(inputs); break
        except Exception as e:                      # rate limit or transient error
            wait = 2 ** attempt * 2
            print(f"embedding retry in {wait}s: {e}"); time.sleep(wait)
    else:
        raise SystemExit("Embedding failed repeatedly. Check the deployment name and quota.")
    for d, v in zip(batch, vecs):
        d["embedding"] = v
    res = client.upload_documents(batch)
    failed = [x for x in res if not x.succeeded]
    if failed:
        raise SystemExit(f"Upload failed for {len(failed)} documents, first: {failed[0].key} {failed[0].error_message}")
    print(f"uploaded {min(i + BATCH, len(docs))}/{len(docs)}")
print("Done. Give the index a few seconds, then run: RETRIEVER=azure python scripts/eval_retrieval.py")
