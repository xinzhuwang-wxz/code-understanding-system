from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from analyzer.orchestrator import analyze_repo
from analyzer.orchestrator_v2 import analyze_repo_universal

app = FastAPI(title="Code Understanding System")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class AnalyzeRequest(BaseModel):
    repo_path: str
    method: str = "tree-sitter"  # "tree-sitter" or "original"


class SearchRequest(BaseModel):
    query: str
    node_type: str = "function"


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    repo = req.repo_path.strip()
    if not repo:
        raise HTTPException(status_code=400, detail="repo_path is required")

    expanded = os.path.expanduser(repo)
    resolved = Path(expanded).resolve()

    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {repo}")

    try:
        if req.method == "tree-sitter":
            result = analyze_repo_universal(str(resolved))
        else:
            result = analyze_repo(str(resolved))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return result


@app.post("/api/search")
async def search(req: SearchRequest):
    """Search the knowledge graph for symbols matching a pattern."""
    try:
        from graph.kuzu_store import KnowledgeGraph
        db_path = Path.home() / ".code-kg" / "graph"
        if not db_path.exists():
            return {"results": [], "error": "No graph database found. Analyze a repo first."}
        
        kg = KnowledgeGraph(str(db_path))
        results = kg.search_by_pattern(req.node_type, req.query)
        stats = kg.stats()
        kg.close()
        return {"results": results, "stats": stats, "query": req.query}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
async def status():
    try:
        from graph.kuzu_store import KnowledgeGraph
        db_path = Path.home() / ".code-kg" / "graph"
        if db_path.exists():
            kg = KnowledgeGraph(str(db_path))
            stats = kg.stats()
            kg.close()
            return {"status": "ok", "kg_stats": stats}
    except Exception:
        pass
    return {"status": "ok", "kg_stats": None}


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
