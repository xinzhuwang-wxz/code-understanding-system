from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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
    max_results: int = 20


class ExplainRequest(BaseModel):
    node_id: str


class ConventionsRequest(BaseModel):
    repo_path: str = ""


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
    """Three-layer semantic search with adaptive escalation."""
    try:
        from search.engine import get_search_engine
        engine = get_search_engine()
        response = engine.search(req.query, req.node_type, req.max_results)
        return {
            "query": response.query,
            "results": [
                {
                    "node_id": r.node_id,
                    "label": r.label,
                    "type": r.node_type,
                    "file_path": r.file_path,
                    "line_number": r.line_number,
                    "signature": r.signature,
                    "docstring": r.docstring,
                    "score": r.score,
                    "source": r.source_layer,
                }
                for r in response.results
            ],
            "total": response.total_found,
            "layers": response.layers_consulted,
            "escalation": response.escalation_path,
            "latency_ms": response.total_latency_ms,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/explain")
async def explain(req: ExplainRequest):
    """Explain a code symbol using LLM."""
    try:
        from search.llm import get_llm
        from graph.kuzu_store import KnowledgeGraph

        db_path = Path.home() / ".code-kg" / "graph"
        if not db_path.exists():
            return {"explanation": "No graph database. Analyze a repo first."}

        kg = KnowledgeGraph(str(db_path))
        nodes = kg.query(
            "MATCH (n:Node {id: $id}) RETURN n.label, n.signature, n.docstring, n.type",
            {"id": req.node_id},
        )
        kg.close()

        if not nodes:
            return {"explanation": "Node not found.", "node_id": req.node_id}

        n = nodes[0]
        llm = get_llm()
        explanation = llm.explain_code(
            n.get("n.label", ""),
            n.get("n.signature", ""),
            n.get("n.docstring", ""),
        )
        return {"node_id": req.node_id, "explanation": explanation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/conventions")
async def conventions(req: ConventionsRequest):
    """Get or generate coding conventions."""
    try:
        from memory.store import load_conventions, save_conventions
        from search.llm import get_llm

        existing = load_conventions()
        if existing:
            return {"conventions": existing, "source": "stored"}

        # Try to auto-generate from analyzed code
        if req.repo_path:
            from graph.kuzu_store import KnowledgeGraph
            db_path = Path.home() / ".code-kg" / "graph"
            if db_path.exists():
                kg = KnowledgeGraph(str(db_path))
                samples = kg.query(
                    "MATCH (n:Node) WHERE n.docstring <> '' "
                    "RETURN n.label, n.signature, n.docstring, n.file_path "
                    "LIMIT 20"
                )
                kg.close()

                if samples:
                    llm = get_llm()
                    code_samples = [
                        {"name": s["n.label"], "content": s["n.signature"], "language": "unknown"}
                        for s in samples
                    ]
                    generated = llm.extract_conventions(code_samples)
                    if generated:
                        save_conventions(generated)
                        return {"conventions": generated, "source": "generated"}

        return {"conventions": "", "source": "none", "hint": "Analyze a repo first, then call again."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
async def status() -> dict[str, Any]:
    result: dict[str, Any] = {"status": "ok", "kg_stats": None}
    try:
        from graph.kuzu_store import KnowledgeGraph
        db_path = Path.home() / ".code-kg" / "graph"
        if db_path.exists():
            kg = KnowledgeGraph(str(db_path))
            result["kg_stats"] = kg.stats()
            kg.close()
    except Exception:
        pass

    # Check LLM availability
    try:
        from search.llm import get_llm
        result["llm_available"] = get_llm().available
    except Exception:
        result["llm_available"] = False

    return result


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
