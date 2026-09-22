# ARC, single instance. One worker on purpose: sessions live in memory (app/main.py SESSIONS), so a second worker would
# lose half the customers' visits. Put a real limiter and a session store in front before running more than one.
#
#   docker build -t arc .
#   docker run --rm -p 8000:8000 --env-file .env arc          # .env stays OUTSIDE the image (see .dockerignore)
#
# The image carries no secret: every key comes from the environment at start-up.
#
# Two stages: "build" installs the pinned dependencies into a virtual environment (pip, its cache and any build tooling stay
# there); "runtime" copies only that environment and the app. Same port, same environment variables, same command as before.

# ---------------------------------------------------------------- stage 1: dependencies
FROM python:3.13-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# the pinned runtime dependencies only; a code change does not reinstall them
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && find /opt/venv -type d -name "__pycache__" -prune -exec rm -rf {} +

# ---------------------------------------------------------------- stage 2: what runs
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

WORKDIR /app

# a normal user, no shell, no home: nothing in the image is writable by the app
RUN useradd --system --no-create-home --shell /usr/sbin/nologin arc

COPY --from=build /opt/venv /opt/venv

# the application: the package, the rule tables and the clause file, the three synthetic document sets ("Use sample documents" reads them)
COPY --chown=arc:arc app ./app
COPY --chown=arc:arc data ./data
COPY --chown=arc:arc demo/samples ./demo/samples

USER arc

EXPOSE 8000
# /health answers {"status":"ok"} without touching Azure; /ready checks whether Azure can be reached
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
