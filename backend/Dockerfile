# Stage 1: build — install packages and compile C extensions (gcc, libpq-dev).
# Nothing from this stage except the installed packages reaches the runtime image.
FROM python:3.12-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

# Stage 2: runtime — python:3.12-slim without build tools, running as nonroot.
# Distroless is not used here: psycopg2-binary is a Python 3.12 C extension and
# gcr.io/distroless/python3-debian12 ships Python 3.11 (Debian 12 default).
FROM python:3.12-slim

RUN useradd --system --no-create-home --uid 1001 nonroot

COPY --from=builder /deps /deps
COPY . /app

# Pre-create rw volume mount points with nonroot ownership.
# Docker copies these dirs into named volumes on first initialisation, so the
# volume inherits nonroot ownership rather than the default root:root.
# Paths mirror docker-compose.yml: corpus-artifact is mounted at both locations.
RUN mkdir -p /app/data/corpus-artifact /srv/corpus-artifact \
    && chown nonroot:nonroot /app/data/corpus-artifact /srv/corpus-artifact

# Bake all-MiniLM-L6-v2 into the image at build time so cold starts never
# fetch from HuggingFace at runtime (offline / air-gapped containers work).
# HF_HOME points to the baked cache; HF_HUB_OFFLINE=1 tells huggingface_hub
# to use only local files at runtime — no metadata re-checks, no network.
# The download runs as root here; we chown to nonroot before USER nonroot.
ENV HF_HOME=/app/model_cache
ENV HF_HUB_OFFLINE=1
RUN HF_HUB_OFFLINE=0 PYTHONPATH=/deps python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" \
    && chown -R nonroot:nonroot /app/model_cache

ENV PYTHONPATH=/deps:/app
WORKDIR /app

USER nonroot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

CMD ["python", "-m", "uvicorn", "archimedes.main:app", "--host", "0.0.0.0", "--port", "8000"]
