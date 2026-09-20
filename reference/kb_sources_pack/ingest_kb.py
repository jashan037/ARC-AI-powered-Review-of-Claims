"""Chunk every downloaded document and merge into kb/chunks/all_chunks.jsonl.
- The HDFHLIP25041V062425 wording uses the clause-aware chunker (tools/chunk_policy.py).
- Everything else, including the 2026 wording, uses the generic chunker (tools/chunk_generic.py).
- Product lists and forms are skipped here: parse them into tables instead.
"""
import json, os, subprocess, sys

m = json.load(open("kb_manifest.json", encoding="utf-8"))
os.makedirs("kb/chunks", exist_ok=True)
outs = []
for d in m["documents"]:
    path = os.path.join("kb", d["folder"], d["filename"])
    if not os.path.exists(path) or d["use"] in ("table", "schema"):
        continue
    out = os.path.join("kb", "chunks", d["id"] + ".jsonl")
    if d["uin"] == "HDFHLIP25041V062425":
        cmd = [sys.executable, "tools/chunk_policy.py", path, "--out", out, "--uin", d["uin"], "--doc-id", d["id"]]
        if d["effective_from"]: cmd += ["--effective-from", d["effective_from"]]
    else:
        cmd = [sys.executable, "tools/chunk_generic.py", path, "--doc-id", d["id"], "--doc-type", d["doc_type"],
               "--authority", d["authority"], "--out", out]
        if d["url"]: cmd += ["--source-url", d["url"]]
        if d["uin"]: cmd += ["--uin", d["uin"]]
        if d["effective_from"]: cmd += ["--effective-from", d["effective_from"]]
    subprocess.run(cmd, check=True); outs.append(out)

n = 0
with open("kb/chunks/all_chunks.jsonl", "w", encoding="utf-8") as f:
    for o in outs:
        for line in open(o, encoding="utf-8"):
            rec = json.loads(line)
            rec.setdefault("authority", "primary")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); n += 1
print(f"{n} chunks from {len(outs)} documents -> kb/chunks/all_chunks.jsonl")
