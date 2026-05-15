# ─── Code Understanding System ───────────────────────────────────
# Multi-stage build: builder → runner
#
# Build:  docker build -t code-kg .
# Run:    docker run -p 8765:8765 -v code-kg-data:/data -e DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY code-kg
# Or:     docker compose up

# ─── Stage 1: Builder ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build deps for tree-sitter native bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a wheel cache
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ─── Stage 2: Runner ───────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.title="Code Understanding System (CodeKG)"
LABEL org.opencontainers.image.description="Human + Agent friendly codebase understanding — Web UI + MCP Server + Git Diff Impact Analysis"
LABEL org.opencontainers.image.source="https://github.com/bamboo/code-understanding-system"

# Runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/

# Make scripts executable
RUN chmod +x scripts/*.sh 2>/dev/null || true

# KuzuDB data directory (persist via volume mount)
ENV CODE_KG_DATA=/data
RUN mkdir -p /data

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/status')" || exit 1

EXPOSE 8765

ENV PYTHONPATH=/app/backend
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
