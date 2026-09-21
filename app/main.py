from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import intake, report
from .agent.runner import get_agent
from .config import settings
from .observability import configure_logging
from .tools import claims_engine as E
from .tools.canned import NOT_READY
from .tools.registry import assessment_view

configure_logging()
log = logging.getLogger("claims.api")

app = FastAPI(title="Claims Adjudication Assistant API", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)   # docs are served only with DEBUG=1 (below)


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


def _debug_only() -> None:
    """The developer routes do not exist unless the server was started with DEBUG=1 (read at request time)."""
    if not settings.debug:
        raise HTTPException(404, "Not found.")


def _sweep() -> None:
    """Delete every session idle longer than the TTL, with everything in it (documents, claim, chat history)."""
    cutoff = time.monotonic() - settings.session_ttl_s
    for sid in [k for k, v in SESSIONS.items() if v.get("last_seen", 0) < cutoff]:
        del SESSIONS[sid]


_HITS: dict[tuple, deque] = defaultdict(deque)


def _limited(ip: str, bucket: str, limit: int) -> bool:
    now, q = time.monotonic(), _HITS[(ip, bucket)]
    while q and q[0] < now - 60:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


API_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "Cache-Control": "no-store",
               "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'", "Cross-Origin-Resource-Policy": "same-origin"}


@app.middleware("http")
async def guard_every_request(request: Request, call_next):
    """A per-IP rate limit on every route (a tighter one on chat, which costs a model call) and the security headers on every response."""
    ip = request.client.host if request.client else "unknown"
    path = request.url.path
    if _limited(ip, "all", settings.rate_limit_per_min) or (path.endswith("/chat") and _limited(ip, "chat", settings.chat_rate_limit_per_min)):
        resp = JSONResponse(_error_body("rate_limited", "That is too many requests in a minute. Please wait a moment and try again."), status_code=429, headers={"Retry-After": "30"})
    else:
        resp = await call_next(request)
    for k, v in API_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/openapi.json", include_in_schema=False)
def openapi_json():
    _debug_only()
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
def docs():
    _debug_only()
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json", title="ARC API (debug)")
SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))


class ClaimIn(BaseModel):
    sample_id: str | None = Field(None, description="One of GET /samples, for example TC07")
    claim: dict | None = Field(None, description="A full claim object (same shape as data/sample_claims.json)")


class SessionIn(BaseModel):
    audience: str | None = Field(None, description="ignored: every session is a customer session")


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


def _session(sid: str) -> dict:
    _sweep()
    if sid not in SESSIONS:
        raise HTTPException(404, "Unknown session. It may have ended after 30 minutes without activity. Create one with POST /sessions.")
    SESSIONS[sid]["last_seen"] = time.monotonic()
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


# ---------------------------------------------------------------- the demo web UI: static files, same origin as the API (no CORS needed)
STATIC_DIR = Path(__file__).parent / "static"
PAGE_HEADERS = {   # the page loads only its own scripts and styles and talks only to this server
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-cache"}


PAGE_PATHS = ("/", "/upload", "/chat")     # three real paths, one page: a refresh or a shared link on any of them works


@app.get("/", include_in_schema=False)
@app.get("/upload", include_in_schema=False)
@app.get("/chat", include_in_schema=False)
def index():
    """The customer page: the landing view, the upload view and the chat view, chosen by the path (app/static/app.js)."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers=PAGE_HEADERS)


class _NoCacheStatic(StaticFiles):
    """The page and its scripts change together with the API, so the browser must always revalidate (a stale customer.js showed empty replies)."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _NoCacheStatic(directory=STATIC_DIR), name="static")


@app.get("/sessions/{sid}/messages")
def messages(sid: str):
    """The conversation so far, so a page refresh on /chat shows it again. Nothing new is computed and no model is called."""
    s = _session(sid)
    out = []
    if s.get("intro"):
        out.append(dict(who="arc", text=s["intro"]))
    for t in s.get("history", []):
        out += [dict(who="you", text=t["user"]), dict(who="arc", text=t["reply"])]
    return dict(ready=bool(s.get("claim")), messages=out)


@app.get("/sessions/{sid}/report.pdf")
async def claim_report(sid: str):
    """The claims team's report for this session's claim: built in code from the documents, no model call, nothing stored, nothing from the chat."""
    s = _session(sid)
    if not s.get("claim"):
        raise HTTPException(404, "There is no claim to report on yet. Upload your documents first.")
    pdf = await run_in_threadpool(report.report_pdf, s)
    log.info("report", extra={"fields": dict(session=s["id"], bytes=len(pdf))})   # a count only: never the content
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{report.filename(s["claim"])}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Length": str(len(pdf))})


@app.delete("/sessions/{sid}")
def delete_session(sid: str):
    """Deletes everything held for the session (documents, claim, chat). Safe to call twice."""
    existed = SESSIONS.pop(sid, None) is not None
    return {"deleted": existed}


@app.get("/samples")
def samples():
    _debug_only()
    return [dict(id=k, title=v["title"], purpose=v["purpose"]) for k, v in SAMPLES.items()]


@app.post("/sessions")
def create_session(body: SessionIn | None = None):
    _sweep()
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = dict(id=sid, uin=settings.default_uin, claim=None, history=[], audience="customer", last_seen=time.monotonic())
    return dict(session_id=sid, intake=True)     # lets the page notice a server that predates document intake


# ---------------------------------------------------------------- claim intake: documents in, claim out
def _shown_name(name: str | None) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", (name or "file").replace("\\", "/").split("/")[-1])[:100] or "file"


LIMIT_MESSAGE = "You have reached the limit of {n} documents for one claim, so this one was not added."


async def _read_all(s: dict, named: list[tuple[str, bytes]]) -> dict:
    results = []
    for name, data in named:
        if s.get("doc_count", 0) >= settings.max_docs_per_session:
            results.append(dict(filename=name, status="limit", message=LIMIT_MESSAGE.format(n=settings.max_docs_per_session)))
            continue
        s["doc_count"] = s.get("doc_count", 0) + 1
        results.append(intake.store(s, name, await run_in_threadpool(intake.process_file, name, data)))
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
async def use_sample_documents(sid: str, set: str | None = None):
    """Loads one of the three synthetic sets (on_time by default; 'late' and 'expired' for the other two). An unknown name loads the default."""
    s = _session(sid)
    return await _read_all(s, intake.sample_files(set))


@app.post("/sessions/{sid}/intake")
def run_intake(sid: str):
    """Build the claim from the documents read so far. status: ready (a claim is loaded in the session) or needs_attention (with plain reasons)."""
    s = _session(sid)
    out = intake.build(s)
    if out["status"] == "ready":
        claim = out.pop("claim")
        out["claim"] = _summary(claim)
        out["first_message"] = intake.first_message(claim, out["missing"], counts=intake.document_counts(s.get("documents", {})), first=not s.get("intro_sent"))
        s["intro_sent"] = True
        s["intro"] = out["first_message"]      # kept so a page refresh can show the conversation again (GET /sessions/{id}/messages)
        out["counts"] = intake.document_counts(s.get("documents", {}))
    return out


@app.post("/sessions/{sid}/claim")
def load_claim(sid: str, body: ClaimIn):
    _debug_only()
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
    if not s.get("claim"):   # no upload yet, or the documents need attention: a fixed answer, no model call
        return dict(status="ok", reply=NOT_READY, sources=[])
    if s.get("turns", 0) >= settings.max_messages_per_session:
        raise HTTPException(429, "This conversation has reached its limit. Please start again with a new upload.")
    s["turns"] = s.get("turns", 0) + 1
    result = get_agent().ask(s, body.message)
    if result.status == "ok":   # a timed-out or unavailable turn is not part of the conversation: the customer will simply ask again
        s["history"].append(dict(user=body.message, reply=result.reply))
    out = dict(status=result.status, reply=result.reply, sources=result.sources)
    if settings.debug_trace:   # developer detail only when the server was started with DEBUG_TRACE=1
        out.update(tool_trace=result.trace, guards=result.guards)
    return out


@app.post("/assess")
def assess_stateless(body: ClaimIn):
    """Deterministic assessment as JSON. No model involved. Developer route (DEBUG=1)."""
    _debug_only()
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
    return dict(recommendation=res["recommendation"], amounts=res["amounts"], assessment=assessment_view(res))
