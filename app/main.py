from __future__ import annotations

import json
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent.runner import get_agent
from .config import settings
from .observability import configure_logging
from .retrieval.azure_search import get_retriever
from .rendering.render import render_claim_assessment
from .tools import claims_engine as E

configure_logging()
log = logging.getLogger("claims.api")

app = FastAPI(title="Claims Adjudication Assistant API", version="0.1.0")


class BodyLimitMiddleware:
    """Rejects request bodies over max_bytes with 413, whether or not the client sent a Content-Length."""

    class _TooLarge(BaseException):
        # not an Exception on purpose: FastAPI turns any Exception raised while reading the body into a generic 400
        pass

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            return await self._reject(send)
        received, started = 0, False

        async def limited_receive():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > self.max_bytes:
                    raise self._TooLarge()
            return msg

        async def tracking_send(msg):
            nonlocal started
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except self._TooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send):
        body = json.dumps(_error_body("request_too_large", f"The request is larger than the {self.max_bytes // 1024} KB limit.")).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def harden(target: FastAPI, *, cors_origins, max_bytes: int) -> None:
    target.add_middleware(BodyLimitMiddleware, max_bytes=max_bytes)
    if cors_origins:   # CORS_ORIGINS="http://localhost:5173,https://app.example.com"; unset means no cross-origin access
        target.add_middleware(CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


harden(app, cors_origins=settings.cors_origins, max_bytes=settings.max_request_bytes)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse(_error_body(f"http_{exc.status_code}", str(exc.detail)), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    # field names and reasons only: the submitted values (which may be claim data) are never echoed back
    problems = [f"{'.'.join(str(p) for p in e['loc'] if p != 'body') or 'body'}: {e['msg']}" for e in exc.errors()][:5]
    return JSONResponse(_error_body("invalid_request", "; ".join(problems)), status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.error("unhandled_error", extra={"fields": dict(path=request.url.path, error=type(exc).__name__)})   # class name only, no trace, no data
    return JSONResponse(_error_body("internal_error", "Something went wrong on our side. Please try again; if it keeps happening, contact your administrator."), status_code=500)

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


def _check_claim(claim: dict) -> None:
    missing = [k for k in ("claim_id", "plan", "base_si_lakh", "first_policy_inception", "admission_datetime", "discharge_datetime", "bill_lines") if k not in claim]
    if missing:
        raise HTTPException(422, f"Claim is missing required fields: {missing}")
    if claim["plan"] not in E.PLANS:
        raise HTTPException(422, f"Unknown plan '{claim['plan']}'. Known plans: {list(E.PLANS)}")


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
    _check_claim(claim)
    s["claim"] = claim
    s["uin"] = claim.get("policy_uin") or s["uin"]
    return dict(loaded=True, claim=_summary(claim))


@app.post("/sessions/{sid}/chat")
def chat(sid: str, body: ChatIn):
    s = _session(sid)
    result = get_agent().ask(s, body.message)
    if result.status == "ok":   # a timed-out or unavailable turn is not part of the conversation: the officer will simply ask again
        headline = (result.final or {}).get("headline") or next((l.strip("# ").strip() for l in result.markdown.splitlines() if l.strip()), "")
        s["history"].append(dict(user=body.message, answer_type=result.answer_type, headline=headline))
    return dict(session_id=sid, status=result.status, answer_type=result.answer_type, answer_markdown=result.markdown, citations=result.citations,
                tool_trace=result.trace)


@app.post("/assess")
def assess_stateless(body: ClaimIn):
    """Deterministic assessment with the standard format. No LLM involved."""
    if body.sample_id:
        if body.sample_id not in SAMPLES:
            raise HTTPException(404, f"Unknown sample {body.sample_id}")
        claim = SAMPLES[body.sample_id]["claim"]
    elif body.claim:
        claim = body.claim
    else:
        raise HTTPException(422, "Provide sample_id or claim.")
    _check_claim(claim)
    res = E.assess(claim)
    rendered = render_claim_assessment(res, get_retriever())
    return dict(recommendation=res["recommendation"], amounts=res["amounts"], answer_markdown=rendered.markdown, citations=rendered.citations)
