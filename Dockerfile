# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Build stage: resolve dependencies into a self-contained virtualenv.
# The base image ships uv, so no pip bootstrap is needed.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.18 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# `all` pulls the OpenAI/Anthropic/Ollama integrations and the Redis
# checkpointer. Drop it to build a slimmer, provider-less image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python ".[all]"

# ---------------------------------------------------------------------------
# Runtime stage: slim, non-root, one image for both the API and the workers.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    AGENTMESH_HOST=0.0.0.0 \
    AGENTMESH_PORT=8100 \
    AGENTMESH_KNOWLEDGE_DIR=/app/knowledge

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 agentmesh

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=agentmesh:agentmesh docker/entrypoint.sh /usr/local/bin/agentmesh-entrypoint
COPY --chown=agentmesh:agentmesh docs/knowledge /app/knowledge
RUN chmod 0755 /usr/local/bin/agentmesh-entrypoint

USER agentmesh
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c 'import os,sys,urllib.request; url="http://127.0.0.1:"+os.getenv("AGENTMESH_PORT","8100")+"/healthz"; sys.exit(0 if urllib.request.urlopen(url, timeout=4).status == 200 else 1)'

ENTRYPOINT ["agentmesh-entrypoint"]
CMD ["api"]

