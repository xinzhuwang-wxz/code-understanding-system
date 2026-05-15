# CodeKG — Code Understanding System

**Human + Agent friendly codebase understanding.**

A Web UI for interactive code exploration and an MCP Server for AI agents (Claude Code, Codex, OpenClaw, Hermes Agent) to query, traverse, and understand any codebase.

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Analyze a repo
code-kg analyze /path/to/your/repo

# 3. Start the Web UI
PYTHONPATH=backend uvicorn app:app --port 8765
# Open http://localhost:8765

# 4. Configure MCP for your agent
code-kg mcp-config
```

## Docker

```bash
docker compose up -d
# Web UI at http://localhost:8765
# Data persisted in Docker volumes: kuzu_data, memory
```

## Architecture

```
code-understanding-system/
├── backend/
│   ├── analyzer/       # tree-sitter parser (20+ languages)
│   ├── graph/          # KuzuDB graph + HNSW vector index
│   ├── search/         # Three-layer search + DeepSeek LLM
│   ├── memory/         # Mnemosyne episodic memory + conventions
│   ├── impact/         # Git diff → dependency impact analysis
│   ├── mcp/            # MCP Server (8 tools, stdio JSON-RPC)
│   └── app.py          # FastAPI Web Server
├── frontend/           # D3.js interactive graph visualization
├── scripts/            # Crash-restart MCP wrapper
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## MCP Tools (for AI Agents)

| Tool | Description |
|------|-------------|
| `search_by_pattern` | Search code symbols by name |
| `search_semantic` | Semantic code search |
| `traverse_graph` | Explore dependencies, callers, callees |
| `get_conventions` | Get `.agent-conventions.yaml` |
| `get_context` | Task-aware code context (token-budgeted) |
| `ask_question` | NL question about the codebase (LLM) |
| `analyze_impact` | Git diff or entity impact analysis |
| `search_docs` | Search Markdown + comments + API docs |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/analyze` | Analyze a repository |
| `POST /api/search` | Three-layer code search |
| `POST /api/explain` | LLM-powered code explanation |
| `POST /api/conventions` | Get/generate coding conventions |
| `POST /api/diff` | Git diff analysis |
| `POST /api/impact` | Entity or diff impact analysis |
| `POST /api/docs/index` | Index documentation |
| `POST /api/docs/search` | Search documentation |
| `GET /api/status` | System health + graph stats |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPSEEK_API_KEY` | Optional | Enables LLM features (explain, ask, impact summary) |
| `CODE_KG_DATA` | Optional | KuzuDB data directory (default: `~/.code-kg/`) |

## CI Integration

GitHub Actions workflow at `.github/workflows/ci.yml`:
- Auto-analyzes codebase on push/PR
- Runs integration tests (search + MCP tools + doc index)
- Posts impact analysis summary on PRs

## License

MIT — based on CodeLandscapeViewer (MIT).
