FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FF_WORKSPACE=/data

WORKDIR /app

COPY pyproject.toml ./
COPY flashforensics ./flashforensics
COPY tools ./tools

RUN pip install --no-cache-dir -e .

# Build the format index at image build time so the first request is not slow.
# This is also where the MiniLM download happens; if the build host has no
# access to the model host, the image falls back to the offline embedder at
# runtime and reports that on /api/health rather than failing.
RUN python -c "from flashforensics.agents.graph import get_knowledge_base; from flashforensics.config import get_settings; get_knowledge_base(get_settings())" || true

RUN mkdir -p /data && useradd -m -u 10001 forensics && chown -R forensics /data /app
USER forensics

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/api/health', timeout=4).status_code==200 else 1)"

# Shell form so a platform-supplied $PORT (Render, Railway, Fly) is honoured
# while local runs keep the documented 8000.
CMD uvicorn flashforensics.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
