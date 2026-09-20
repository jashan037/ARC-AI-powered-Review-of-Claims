"""Download every auto item in kb_manifest.json into kb/<folder>/. Run on your own machine (needs internet)."""
import json, os, time, argparse
import requests

ap = argparse.ArgumentParser()
ap.add_argument("--max-priority", type=int, default=3, help="1 = only must-haves")
ap.add_argument("--skip-secondary", action="store_true")
args = ap.parse_args()

m = json.load(open("kb_manifest.json", encoding="utf-8"))
UA = {"User-Agent": "Mozilla/5.0 (kb-downloader for an academic project)"}
manual, failed, ok = [], [], 0
for d in m["documents"]:
    if d["priority"] > args.max_priority or (args.skip_secondary and d["authority"] == "secondary"):
        continue
    path = os.path.join("kb", d["folder"], d["filename"])
    if d["download"] != "auto" or not d["url"]:
        if not os.path.exists(path):
            manual.append((d, path))
        continue
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        print("have   ", path); ok += 1; continue
    try:
        r = requests.get(d["url"], headers=UA, timeout=60)
        r.raise_for_status()
        if b"%PDF" not in r.content[:1024]:
            raise ValueError("not a PDF (got %s)" % r.headers.get("content-type"))
        open(path, "wb").write(r.content); ok += 1
        print("saved  ", path, f"{len(r.content) // 1024} KB")
    except Exception as e:
        failed.append((d, str(e))); print("FAILED ", d["id"], e)
    time.sleep(1)

print(f"\n{ok} ready, {len(failed)} failed, {len(manual)} to fetch manually")
for d, path in manual:
    print(f"  MANUAL  {d['title']}\n          from: {d['url'] or 'irdai.gov.in (search the reference in the title)'}\n          save as: {path}")
