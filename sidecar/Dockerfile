# Book Navigator sidecar (podcast-creator LLM/TTS over gRPC).
# Build context is the REPO ROOT:  docker build -f sidecar/Dockerfile .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
# Dependency layer first (cache-friendly)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --group sidecar --no-install-project
# Application code (sidecar imports open_notebook + prompts templates)
COPY open_notebook/ open_notebook/
COPY sidecar/ sidecar/
COPY prompts/ prompts/
ENV PATH="/app/.venv/bin:$PATH" \
    SIDECAR_ADDRESS="[::]:50069" \
    DATA_FOLDER=/app/data
EXPOSE 50069
CMD ["python", "-m", "sidecar.podcast_sidecar"]
