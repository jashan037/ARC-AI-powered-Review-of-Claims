"""Render every sample claim into demo/examples/<id>.md (no Azure needed). Optional argument: another output folder."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools.claims_engine import assess
from app.rendering.render import render_claim_assessment

samples = json.load(open(settings.data_dir / "sample_claims.json"))
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "demo" / "examples"
out.mkdir(parents=True, exist_ok=True)
r = get_retriever()
for sid, s in samples.items():
    md = render_claim_assessment(assess(s["claim"]), r).markdown
    (out / f"{sid}_{s['title'].lower().replace(' ', '_').replace(':','').replace(',','')[:40]}.md").write_text(md, encoding="utf-8")
    print("rendered", sid)
