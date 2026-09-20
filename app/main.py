from __future__ import annotations

import json
import re
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent.runner import get_agent
from .config import settings
from .retrieval.azure_search import get_retriever
from .rendering.render import render_claim_assessment
from .tools import claims_engine as E

app = FastAPI(title="Claims Adjudication Assistant API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SESSIONS: dict[str, dict] = {}
SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))


class ClaimIn(BaseModel):
    sample_id: str | None = Field(None, description="One of GET /samples, for example TC07")
    claim: dict | None = Field(None, description="A full claim object (same shape as data/sample_claims.json)")


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


def _session(sid: str) -> dict:
    if sid not in SESSIONS:
        raise HTTPException(404, "Unknown session. Create one with POST /sessions.")
    return SESSIONS[sid]


def _summary(c: dict) -> dict:
    return dict(claim_id=c["claim_id"], insured=c.get("insured_name"), plan=c["plan"], diagnosis=c.get("diagnosis"), procedure=c.get("procedure"),
                admission=c["admission_datetime"], discharge=c["discharge_datetime"], claimed_amount=c.get("claimed_amount"), policy_uin=c.get("policy_uin"))


@app.get("/health")
def health():
    return dict(status="ok", retriever=settings.retriever, agent_mode=settings.agent_mode, default_uin=settings.default_uin)


@app.get("/samples")
def samples():
    return [dict(id=k, title=v["title"], purpose=v["purpose"]) for k, v in SAMPLES.items()]


@app.post("/sessions")
def create_session():
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = dict(id=sid, uin=settings.default_uin, claim=None, history=[])
    return dict(session_id=sid)


@app.post("/sessions/{sid}/claim")
def load_claim(sid: str, body: ClaimIn):
    s = _session(sid)
    if body.sample_id:
        if body.sample_id not in SAMPLES:
            raise HTTPException(404, f"Unknown sample {body.sample_id}")
        claim = SAMPLES[body.sample_id]["claim"]
    elif body.claim:
        claim = body.claim
    else:
        raise HTTPException(422, "Provide sample_id or claim.")
    missing = [k for k in ("claim_id", "plan", "base_si_lakh", "first_policy_inception", "admission_datetime", "discharge_datetime", "bill_lines") if k not in claim]
    if missing:
        raise HTTPException(422, f"Claim is missing required fields: {missing}")
    if claim["plan"] not in E.PLANS:
        raise HTTPException(422, f"Unknown plan '{claim['plan']}'. Known plans: {list(E.PLANS)}")
    s["claim"] = claim
    s["uin"] = claim.get("policy_uin") or s["uin"]
    return dict(loaded=True, claim=_summary(claim))


@app.post("/sessions/{sid}/chat")
def chat(sid: str, body: ChatIn):
    s = _session(sid)
    result = get_agent().ask(s, body.message)
    headline = (result.final or {}).get("headline") or next((l.strip("# ").strip() for l in result.markdown.splitlines() if l.strip()), "")
    s["history"].append(dict(user=body.message, answer_type=result.answer_type, headline=headline))
    return dict(session_id=sid, answer_type=result.answer_type, answer_markdown=result.markdown, citations=result.citations, tool_trace=result.trace)


@app.post("/assess")
def assess_stateless(body: ClaimIn):
    """Deterministic assessment with the standard format. No LLM involved."""
    if body.sample_id:
        claim = SAMPLES[body.sample_id]["claim"]
    elif body.claim:
        claim = body.claim
    else:
        raise HTTPException(422, "Provide sample_id or claim.")
    res = E.assess(claim)
    rendered = render_claim_assessment(res, get_retriever())
    return dict(recommendation=res["recommendation"], amounts=res["amounts"], answer_markdown=rendered.markdown, citations=rendered.citations)
