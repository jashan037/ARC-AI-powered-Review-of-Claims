from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import intake
from .agent.runner import get_agent
from .config import settings
from .observability import configure_logging
from .retrieval.azure_search import get_retriever
from .rendering.render import render_claim_assessment
from .tools import claims_engine as E
from .tools.canned import NOT_READY
from .tools.registry import trace_summary

configure_logging()
log = logging.getLogger("claims.api")

app = FastAPI(title="Claims Adjudication Assistant API", version="0.1.0")


class BodyLimitMiddleware:
    """Rejects request bodies over max_bytes with 413, whether or not the client sent a Content-Length."""

    class _TooLarge(BaseException):
        # not an Exception on purpose: FastAPI turns any Exception raised while reading the body into a generic 400
        pass

    def __init__(self, app, max_bytes: int, upload_bytes: int | None = None):
        self.app, self.max_bytes, self.upload_bytes = app, max_bytes, upload_bytes or max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.upload_bytes if scope["path"].endswith("/documents") else self.max_bytes    # only document uploads may be large
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            return await self._reject(send, limit)
        received, started = 0, False

        async def limited_receive():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > limit:
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
                await self._reject(send, limit)

    async def _reject(self, send, limit):
        size = f"{limit // (1024 * 1024)} MB" if limit >= 1024 * 1024 else f"{limit // 1024} KB"
        body = json.dumps(_error_body("request_too_large", f"That is larger than the {size} limit. Please send less at a time.")).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def harden(target: FastAPI, *, cors_origins, max_bytes: int, upload_bytes: int | None = None) -> None:
    target.add_middleware(BodyLimitMiddleware, max_bytes=max_bytes, upload_bytes=upload_bytes)
    if cors_origins:   # CORS_ORIGINS="http://localhost:5173,https://app.example.com"; unset means no cross-origin access
        target.add_middleware(CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


harden(app, cors_origins=settings.cors_origins, max_bytes=settings.max_request_bytes, upload_bytes=settings.max_upload_bytes)


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


class SessionIn(BaseModel):
    audience: str = Field("officer", pattern="^(officer|customer)$", description="customer: plain wording for the person who made the claim")


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


def _session(sid: str) -> dict:
    if sid not in SESSIONS:
        raise HTTPException(404, "Unknown session. Create one with POST /sessions.")
    return SESSIONS[sid]


def _public_citations(citations: list[dict]) -> list[dict]:
    """What a caller may see of each citation: label, clause, citation text and excerpt. The internal chunk_key only with DEBUG_TRACE=1."""
    return citations if settings.debug_trace else [{k: v for k, v in c.items() if k != "chunk_key"} for c in citations]


def _summary(c: dict) -> dict:
    return dict(claim_id=c["claim_id"], insured=c.get("insured_name"), plan=c["plan"], diagnosis=c.get("diagnosis"), procedure=c.get("procedure"),
                admission=c["admission_datetime"], discharge=c["discharge_datetime"], claimed_amount=c.get("claimed_amount"), policy_uin=c.get("policy_uin"))


def _check_claim(claim: dict) -> None:
    missing = [k for k in ("claim_id", "plan", "base_si_lakh", "first_policy_inception", "admission_datetime", "discharge_datetime", "bill_lines") if k not in claim]
    if missing:
        raise HTTPException(422, f"Claim is missing required fields: {missing}")
    if claim["plan"] not in E.PLANS:
        raise HTTPException(422, f"Unknown plan '{claim['plan']}'. Known plans: {list(E.PLANS)}")


# ---------------------------------------------------------------- the demo web UI: static files, same origin as the API (no CORS needed)
STATIC_DIR = Path(__file__).parent / "static"
PAGE_HEADERS = {   # the page loads only its own scripts and styles and talks only to this server
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-cache"}


@app.get("/", include_in_schema=False)
def index():
    """The customer page: upload documents, then chat about the claim."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers=PAGE_HEADERS)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health():
    live = settings.retriever == "azure" and settings.agent_mode == "foundry"   # the real Azure agent and index, not the offline stand-in
    return dict(status="ok", live=live, retriever=settings.retriever, agent_mode=settings.agent_mode, default_uin=settings.default_uin)


@app.get("/samples")
def samples():
    return [dict(id=k, title=v["title"], purpose=v["purpose"]) for k, v in SAMPLES.items()]


@app.post("/sessions")
def create_session(body: SessionIn | None = None):
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = dict(id=sid, uin=settings.default_uin, claim=None, history=[], audience=(body.audience if body else "officer"))
    return dict(session_id=sid, intake=True)     # lets the page notice a server that predates document intake


# ---------------------------------------------------------------- claim intake: documents in, claim out
def _shown_name(name: str | None) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", (name or "file").replace("\\", "/").split("/")[-1])[:100] or "file"


async def _read_all(s: dict, named: list[tuple[str, bytes]]) -> dict:
    results = [intake.store(s, name, await run_in_threadpool(intake.process_file, name, data)) for name, data in named]
    log.info("documents", extra={"fields": dict(session=s["id"], files=len(results), recognised=sum(r["status"] == "recognised" for r in results))})   # counts only: never names or contents
    return dict(files=results, checklist=intake.checklist(s.get("documents", {})))


@app.post("/sessions/{sid}/documents")
async def upload_documents(sid: str, files: list[UploadFile] = File(...)):
    s = _session(sid)
    if len(files) > intake.MAX_FILES_PER_UPLOAD:
        raise HTTPException(422, f"Please upload up to {intake.MAX_FILES_PER_UPLOAD} files at a time.")
    named = [(_shown_name(f.filename), await f.read(intake.MAX_FILE_BYTES + 1)) for f in files]
    return await _read_all(s, named)


@app.post("/sessions/{sid}/documents/sample")
async def use_sample_documents(sid: str):
    s = _session(sid)
    return await _read_all(s, intake.sample_files())


@app.post("/sessions/{sid}/intake")
def run_intake(sid: str):
    """Build the claim from the documents read so far. status: ready (a claim is loaded in the session) or needs_attention (with plain reasons)."""
    s = _session(sid)
    out = intake.build(s)
    if out["status"] == "ready":
        claim = out.pop("claim")
        out["claim"] = _summary(claim)
        out["summary_markdown"] = intake.claim_summary_markdown(claim, out["missing"])
        out["suggestions"] = intake.suggestions(claim, [h["user"] for h in s["history"]])
    return out


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
    if s.get("audience") == "customer" and not s.get("claim"):   # no upload yet, or the documents need attention: a fixed answer, no model call
        return dict(session_id=sid, status="ok", answer_type="direct_answer", answer_markdown=NOT_READY, summary_markdown=NOT_READY, sections=[], citations=[], suggestions=[])
    result = get_agent().ask(s, body.message)
    if result.status == "ok":   # a timed-out or unavailable turn is not part of the conversation: the officer will simply ask again
        headline = (result.final or {}).get("headline") or (result.final or {}).get("reply") or next((l.strip("# ").strip() for l in result.markdown.splitlines() if l.strip()), "")
        s["history"].append(dict(user=body.message, answer_type=result.answer_type, headline=headline))
    out = dict(session_id=sid, status=result.status, answer_type=result.answer_type, answer_markdown=result.markdown,
               summary_markdown=result.summary_markdown or result.markdown, sections=result.sections, citations=_public_citations(result.citations),
               suggestions=intake.suggestions(s.get("claim"), [h["user"] for h in s["history"]]))
    if settings.debug_trace:   # developer detail only when the server was started with DEBUG_TRACE=1; nothing the caller sends can switch it on
        out.update(trace_summary=trace_summary(result.trace),   # tool, ok, ms only
                   tool_trace=result.trace)                     # includes tool arguments
    return out


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
    return dict(recommendation=res["recommendation"], amounts=res["amounts"], answer_markdown=rendered.markdown,
                summary_markdown=rendered.summary_markdown, sections=rendered.sections, citations=_public_citations(rendered.citations))
