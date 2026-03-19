FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Runtime dependencies (git for exec_shell usage in workspace)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cached layer — only rebuilds when pyproject.toml changes)
COPY pyproject.toml ./
RUN mkdir -p core && touch core/__init__.py && \
    uv pip install --system --no-cache . && \
    rm -rf core

# Copy source
COPY core/ core/
COPY main.py ./

# Copy built-in skills and template (needed by ConfigLoader on first run)
COPY workspace/skills/ workspace/skills/
COPY template/ template/

# workspace/ and configs/ are mounted as volumes at runtime
# so memory, history, and config persist across container restarts

ENTRYPOINT ["python", "main.py"]
