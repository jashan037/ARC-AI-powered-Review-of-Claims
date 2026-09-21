"""Talk to the agent from the terminal, using exactly the code path the API uses.

    python scripts/dev/chat_cli.py --claim TC07
    AGENT_MODE=foundry RETRIEVER=azure python scripts/dev/chat_cli.py --claim TC07
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.agent.runner import get_agent
from app.config import settings

ap = argparse.ArgumentParser(); ap.add_argument("--claim", help="sample id such as TC07"); args = ap.parse_args()
samples = json.load(open(settings.data_dir / "sample_claims.json"))
session = dict(uin=settings.default_uin, claim=samples[args.claim]["claim"] if args.claim else None, history=[])
print(f"agent_mode={settings.agent_mode} retriever={settings.retriever} claim={args.claim or 'none'}  (Ctrl+C to quit)\n")
while True:
    try:
        q = input("you> ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not q:
        continue
    r = get_agent().ask(session, q)
    session["history"].append(dict(user=q, reply=r.reply))
    src = "; ".join(x["title"] for x in r.sources)
    print("\n" + r.reply + (f"\n\nSources: {src}" if src else "") + "\n" + f"[tools: {' > '.join(t['tool'] for t in r.trace) or 'none'}; model calls: {r.guards.get('model_calls', 0)}]\n")
