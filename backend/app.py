from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Load .env file for local development (DEEPSEEK_API_KEY, etc.)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from analyzer.orchestrator import analyze_repo
from analyzer.orchestrator_v2 import analyze_repo_universal

app = FastAPI(title="Code Understanding System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    # Auto-generate conventions if LLM is available
    try:
        from memory.store import load_conventions, save_conventions
        from search.llm import get_llm
        if not load_conventions() and get_llm().available:
            from graph.kuzu_store import KnowledgeGraph, get_default_db_path
            db_path = get_default_db_path()
            if db_path.exists():
                kg = KnowledgeGraph(str(db_path))
                samples = kg.query(
                    "MATCH (n:Node) WHERE n.docstring <> '' "
                    "RETURN n.label, n.signature, n.docstring, n.file_path "
                    "LIMIT 20"
                )
                kg.close()
                if samples:
                    code_samples = [
                        {"name": s["n.label"], "content": s["n.signature"], "language": "unknown"}
                        for s in samples
                    ]
                    generated = get_llm().extract_conventions(code_samples)
                    if generated:
                        save_conventions(generated)
    except Exception:
        pass  # Conventions auto-gen is best-effort

    # Reset search engine singleton so next search sees fresh data
    try:
        from search.engine import reset_search_engine
        reset_search_engine()
    except Exception:
        pass

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

        from graph.kuzu_store import get_default_db_path
        db_path = get_default_db_path()
        if not db_path.exists():
            return {"explanation": "No graph database. Analyze a repo first."}

        kg = KnowledgeGraph(str(db_path))
        nodes = kg.query(
            "MATCH (n:Node {id: $id}) RETURN n.label, n.signature, n.docstring, n.type, n.file_path, n.line_number",
            {"id": req.node_id},
        )
        kg.close()

        if not nodes:
            return {"explanation": "Node not found.", "node_id": req.node_id}

        n = nodes[0]
        label = n.get("n.label", "")
        sig = n.get("n.signature", "")
        doc = n.get("n.docstring", "")
        ntype = n.get("n.type", "")
        file_path = n.get("n.file_path", "")
        llm = get_llm()

        # For file/module nodes with no code context, try reading source
        extra_context = ""
        if (not sig or not doc) and file_path:
            from pathlib import Path
            # Try repo_root from recent analysis memory or common locations
            possible_roots = [
                Path("/Users/bamboo/Githubs"),
                Path.home() / "Githubs",
            ]
            for root in possible_roots:
                full = root / file_path
                if full.exists() and full.is_file():
                    try:
                        content = full.read_text(encoding="utf-8", errors="replace")
                        extra_context = content[:3000]
                        break
                    except Exception:
                        pass

        # Build context for LLM
        context_parts = [f"Symbol: {label}"]
        if sig:
            context_parts.append(f"Signature: {sig}")
        if doc:
            context_parts.append(f"Docstring: {doc}")
        if extra_context:
            context_parts.append(f"Source (first 3000 chars):\n{extra_context}")
        context = "\n".join(context_parts)

        explanation = ""
        if llm.available:
            explanation = llm.chat([
                {"role": "system", "content": "You are a code explanation assistant. Explain what this code does in 2-4 sentences. If it's a file, describe its purpose and what modules/functions it contains. Be specific and reference the actual code shown."},
                {"role": "user", "content": f"Explain this code:\n\n{context}"},
            ], max_tokens=384)

        if not explanation:
            explanation = f"**{label}**\n\n"
            if sig:
                explanation += f"`{sig}`\n\n"
            if doc:
                explanation += f"{doc}\n\n"
            if extra_context:
                explanation += f"(File content available, {len(extra_context)} chars)\n"
            if not sig and not doc and not extra_context:
                explanation += f"Type: {ntype}\n"
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
            from graph.kuzu_store import get_default_db_path
            db_path = get_default_db_path()
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
        from graph.kuzu_store import get_default_db_path
        db_path = get_default_db_path()
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


@app.post("/api/clear")
async def clear_data() -> dict[str, Any]:
    """Clear all analyzed data from the knowledge graph.

    Resets the database and search engine. Use before analyzing
    a different repository to prevent cross-contamination.
    """
    try:
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path
        from search.engine import reset_search_engine

        db_path = get_default_db_path()
        cleared = False
        if db_path.exists():
            kg = KnowledgeGraph(str(db_path))
            kg.clear()
            kg.close()
            cleared = True

        reset_search_engine()
        return {"status": "cleared", "database": str(db_path), "cleared": cleared}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/capabilities")
async def capabilities():
    """Agent-friendly discovery endpoint: lists all tools and features."""
    return {
        "name": "CodeKG",
        "version": "0.3.0",
        "description": "Code knowledge graph — analyze, search, and visualize any codebase. For humans and AI agents.",
        "endpoints": {
            "rest_api": "http://localhost:8765",
            "openapi_spec": "http://localhost:8765/openapi.json",
            "mcp_server": "stdio://mcp-server (run: python -m backend.mcp.server_standalone)",
            "cli": "python -m backend.cli",
        },
        "capabilities": {
            "analysis": {
                "methods": ["tree-sitter", "original"],
                "output": "nodes + edges + node_colors + edge_colors + stats",
                "note": "tree-sitter method recommended for multi-language support",
            },
            "search": {
                "structural": {"endpoint": "/api/search", "method": "POST", "description": "Pattern-based code search (CONTAINS/BM25)"},
                "semantic": {"status": "active", "description": "Keyword/embedding search (TF-IDF or OpenAI embeddings)"},
            },
            "visualization": {
                "force_graph": {"status": "active", "description": "D3.js force-directed graph"},
                "tree_view": {"status": "active", "description": "Hierarchical tree view"},
                "matrix_view": {"status": "active", "description": "Adjacency matrix"},
                "sunburst_view": {"status": "active", "description": "Sunburst directory layout"},
                "codecity_3d": {"status": "beta", "description": "Three.js 3D city visualization"},
                "metro_map": {"status": "beta", "description": "Metro-style map"},
                "code_panel": {"status": "active", "description": "Monaco Editor source viewer"},
            },
            "llm": {
                "explain": "/api/explain — AI-powered code explanation (requires DEEPSEEK_API_KEY)",
                "conventions": "/api/conventions — extract coding conventions",
                "impact_summary": "/api/diff → returns LLM-generated impact summaries",
            },
            "code_intelligence": {
                "lsp": {"status": "partial", "description": "Python-only (Jedi-based). TS/JS/Go support planned."},
                "scip": {"status": "active", "description": "Built-in cross-file reference indexer (no external SCIP toolkit required)"},
            },
            "code_review": ["/api/review/diff (git diff review)", "/api/review/code (code snippet review)"],
            "code_tour": ["/api/tour (guided exploration)", "/api/questions (suggested questions)"],
            "impact_analysis": ["/api/diff (git diff)", "/api/impact (entity-level)"],
            "docs": ["/api/docs/index", "/api/docs/search"],
        },
        "mcp_tools": [
            {"name": "search_by_pattern", "description": "Search code symbols by name pattern (exact/BM25)"},
            {"name": "search_semantic", "description": "Semantic code search using three-layer retrieval"},
            {"name": "traverse_graph", "description": "Traverse code dependencies — callers, callees"},
            {"name": "get_context", "description": "Get project context, stats, and LLM status"},
            {"name": "get_conventions", "description": "Get coding conventions"},
            {"name": "ask_question", "description": "Ask NL question about the codebase (LLM)"},
            {"name": "analyze_impact", "description": "Git diff or entity impact analysis"},
            {"name": "search_docs", "description": "Search documentation (Markdown + comments + API docs)"},
            {"name": "review_code", "description": "Code review with static analysis + LLM"},
            {"name": "generate_tour", "description": "Generate guided code tour"},
            {"name": "generate_questions", "description": "Generate suggested questions"},
        ],
        "cli_commands": ["analyze", "search", "neighbors", "explain", "conventions", "diff", "impact", "docs", "status", "mcp-config"],
        "data_formats": {
            "graph": "nodes[].{id, label, type, file_path, line_number, signature, docstring} + edges[].{source, target, type, metadata}",
            "search_result": "{node_id, label, type, file_path, line_number, signature, docstring, score, source}",
        },
    }


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
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
            from graph.kuzu_store import get_default_db_path
            db_path = get_default_db_path()
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


@app.get("/api/source")
async def get_source(file_path: str, repo_root: str = "", line_start: int = 0, line_end: int = 0):
    """Read source file content, optionally scoped to a line range.

    Security: resolves file_path against repo_root and rejects path traversal.
    """
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path is required")

    # Determine allowed base directory
    if repo_root:
        base = Path(repo_root).resolve()
        if not base.is_dir():
            raise HTTPException(status_code=400, detail=f"Invalid repo_root: {repo_root}")
    else:
        base = Path.cwd()

    p = Path(file_path)
    if not p.is_absolute():
        p = (base / file_path).resolve()
    else:
        p = p.resolve()

    # Path traversal guard: resolved path must be within base
    try:
        p.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal blocked")

    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {file_path}")

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")

    lines = text.split("\n")
    total_lines = len(lines)

    if line_start > 0 and line_end > 0:
        start = max(0, line_start - 1)
        end = min(total_lines, line_end)
        lines = lines[start:end]
    elif line_start > 0:
        start = max(0, line_start - 1)
        lines = lines[start:]

    return {
        "file_path": str(p),
        "total_lines": total_lines,
        "returned_lines": len(lines),
        "line_start": line_start if line_start > 0 else 1,
        "content": "\n".join(lines),
    }


# ─── LSP Endpoints ─────────────────────────────────────────────

class LspDefinitionRequest(BaseModel):
    file_path: str
    line: int
    column: int = 1
    repo_root: str = ""


class LspReferencesRequest(BaseModel):
    file_path: str
    line: int
    column: int = 1
    repo_root: str = ""


class LspHoverRequest(BaseModel):
    file_path: str
    line: int
    column: int = 1
    repo_root: str = ""


@app.post("/api/lsp/definition")
async def lsp_definition(req: LspDefinitionRequest):
    """Go to definition via LSP/Jedi."""
    try:
        from lsp.client import get_lsp_client
        root = req.repo_root or str(Path(req.file_path).parent)
        client = get_lsp_client(root)
        results = client.definition(req.file_path, req.line, req.column)
        return {"results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/lsp/references")
async def lsp_references(req: LspReferencesRequest):
    """Find all references via LSP/Jedi."""
    try:
        from lsp.client import get_lsp_client
        root = req.repo_root or str(Path(req.file_path).parent)
        client = get_lsp_client(root)
        results = client.references(req.file_path, req.line, req.column)
        return {"results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/lsp/hover")
async def lsp_hover(req: LspHoverRequest):
    """Get hover info via LSP/Jedi."""
    try:
        from lsp.client import get_lsp_client
        root = req.repo_root or str(Path(req.file_path).parent)
        client = get_lsp_client(root)
        result = client.hover(req.file_path, req.line, req.column)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── SCIP Endpoint ─────────────────────────────────────────────

class ReviewDiffRequest(BaseModel):
    diff_text: str
    repo_path: str = ""
    language: str = ""


class ReviewCodeRequest(BaseModel):
    code: str
    language: str = ""
    file_path: str = ""


@app.post("/api/review/diff")
async def review_diff(req: ReviewDiffRequest):
    """Review a git diff with static analysis + LLM."""
    try:
        from review.reviewer import review_diff as run_review
        result = run_review(req.diff_text, req.repo_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/review/code")
async def review_code(req: ReviewCodeRequest):
    """Review a code snippet with static analysis + LLM."""
    try:
        from review.reviewer import review_code as run_review
        result = run_review(req.code, req.language, req.file_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TourRequest(BaseModel):
    repo_path: str = ""
    max_stops: int = 10


@app.post("/api/tour")
async def code_tour(req: TourRequest):
    """Generate a guided code tour — 'N things you should know'."""
    try:
        from review.tour import generate_tour
        stops = generate_tour(req.repo_path, req.max_stops)
        return {"stops": stops, "total": len(stops)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class QuestionsRequest(BaseModel):
    repo_path: str = ""
    max_questions: int = 5


@app.post("/api/questions")
async def suggested_questions(req: QuestionsRequest):
    """Generate suggested questions about the codebase."""
    try:
        from review.tour import generate_questions
        questions = generate_questions(req.repo_path, req.max_questions)
        return {"questions": questions, "total": len(questions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ScipIndexRequest(BaseModel):
    repo_path: str


@app.post("/api/scip/index")
async def scip_index(req: ScipIndexRequest):
    """Run SCIP cross-file reference indexing."""
    try:
        from scip.indexer import index_repo
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path

        result = index_repo(req.repo_path)

        # Ingest SCIP results into KuzuDB
        try:
            db_path = get_default_db_path()
            if db_path.exists():
                kg = KnowledgeGraph(str(db_path))
                symbols = result.get("symbols", [])
                relations = result.get("relations", [])
                if symbols or relations:
                    kg.ingest_analysis(
                        req.repo_path,
                        nodes=symbols,
                        edges=relations,
                    )
                kg.close()
                result["persisted"] = True
        except Exception:
            result["persisted"] = False

        return {
            "indexer": result["indexer"],
            "symbols_found": len(result.get("symbols", [])),
            "relations_found": len(result.get("relations", [])),
            "persisted": result.get("persisted", False),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
