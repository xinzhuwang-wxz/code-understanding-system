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


class DiffRequest(BaseModel):
    repo_path: str
    commit_range: str = "HEAD~1..HEAD"


class ImpactRequest(BaseModel):
    node_id: str = ""
    repo_path: str = ""
    commit_range: str = "HEAD~1..HEAD"


class DocIndexRequest(BaseModel):
    repo_path: str


class DocSearchRequest(BaseModel):
    query: str
    max_results: int = 20


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


@app.post("/api/diff")
async def diff_analyze(req: DiffRequest):
    """Run git diff analysis with impact assessment."""
    try:
        from impact.analyzer import DiffAnalyzer
        expanded = Path(os.path.expanduser(req.repo_path)).resolve()
        if not expanded.is_dir():
            raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")

        analyzer = DiffAnalyzer(repo_path=str(expanded))
        result = analyzer.analyze(commit_range=req.commit_range)

        return {
            "commit_range": req.commit_range,
            "changed_files": result.changed_files,
            "changed_entities": [
                {"name": e.entity_name, "type": e.entity_type,
                 "file": e.file_path, "line_range": e.line_range,
                 "change": e.change_type}
                for e in result.changed_entities
            ],
            "direct_dependents": result.direct_dependents,
            "cascading_impact": result.cascading_impact,
            "total_affected_files": result.total_affected_files,
            "total_affected_nodes": result.total_affected_nodes,
            "related_tests": result.related_tests,
            "summary": result.summary,
            "risk_level": result.risk_level,
            "diff_summary": result.diff_summary,
            "errors": result.raw_errors,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/impact")
async def impact_analyze(req: ImpactRequest):
    """Analyze impact — entity-level (node_id) or git diff (repo_path)."""
    try:
        # Git diff mode
        if req.repo_path:
            from impact.analyzer import DiffAnalyzer
            expanded = Path(os.path.expanduser(req.repo_path)).resolve()
            if not expanded.is_dir():
                raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")
            analyzer = DiffAnalyzer(repo_path=str(expanded))
            result = analyzer.analyze(commit_range=req.commit_range)
            return {
                "mode": "git_diff",
                "commit_range": req.commit_range,
                "changed_files": result.changed_files,
                "changed_entities": [
                    {"name": e.entity_name, "type": e.entity_type,
                     "file": e.file_path, "change": e.change_type}
                    for e in result.changed_entities
                ],
                "direct_dependents": result.direct_dependents,
                "cascading_impact": result.cascading_impact,
                "total_affected_files": result.total_affected_files,
                "related_tests": result.related_tests,
                "summary": result.summary,
                "risk_level": result.risk_level,
                "diff_summary": result.diff_summary,
            }

        # Entity mode
        if req.node_id:
            from graph.kuzu_store import KnowledgeGraph
            db_path = Path.home() / ".code-kg" / "graph"
            if not db_path.exists():
                raise HTTPException(status_code=404, detail="No graph database. Analyze a repo first.")
            kg = KnowledgeGraph(str(db_path))
            impact = kg.impact_analysis(req.node_id)
            kg.close()
            return {"mode": "entity", "node_id": req.node_id, **impact}

        raise HTTPException(status_code=400, detail="Provide node_id or repo_path.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/docs/index")
async def docs_index(req: DocIndexRequest):
    """Index all documentation in a repo (Markdown + comments + API docs)."""
    try:
        from search.doc_indexer import DocIndexer
        expanded = Path(os.path.expanduser(req.repo_path)).resolve()
        if not expanded.is_dir():
            raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")

        indexer = DocIndexer()
        stats = indexer.index_all(str(expanded))
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/docs/search")
async def docs_search(req: DocSearchRequest):
    """Search indexed documentation."""
    try:
        from search.doc_indexer import DocIndexer
        indexer = DocIndexer()
        results = indexer.search_docs(req.query, req.max_results)
        return {"query": req.query, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
