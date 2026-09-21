# ARC, single instance. One worker on purpose: sessions live in memory (app/main.py SESSIONS), so a second worker would
# lose half the customers' visits. Put a real limiter and a session store in front before running more than one.
#
#   docker build -t arc .
#   docker run --rm -p 8000:8000 --env-file .env arc          # .env stays OUTSIDE the image (see .dockerignore)
#
# The image carries no secret: every key comes from the environment at start-up.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# the pinned runtime dependencies first, so a code change does not reinstall them
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# the application: the package, the rule tables and the clause file, the three synthetic document sets ("Use sample documents" reads them)
COPY app ./app
COPY data ./data
COPY demo/samples ./demo/samples

# a normal user, no shell, no home: nothing in the image is writable by the app
RUN useradd --system --no-create-home --shell /usr/sbin/nologin arc && chown -R arc:arc /app
USER arc

EXPOSE 8000
# /health answers {"status":"ok"} without touching Azure; /ready checks whether Azure can be reached
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
