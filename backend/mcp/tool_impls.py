"""
CodeKG MCP Tool Implementations.

Each tool is a class inheriting from Tool. Auto-registered via
__init_subclass__ — no manual wiring needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .tools import Tool, ToolError


class SearchByPatternTool(Tool):
    """Search code symbols by name pattern (exact/BM25)."""

    name = "search_by_pattern"
    description = "Search code symbols by name pattern (exact/BM25). Returns matching functions, classes, modules."

    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query — symbol name or keyword."},
            "node_type": {"type": "string", "default": "function", "description": "Node type filter: function, class, module, file."},
            "max_results": {"type": "integer", "default": 20, "description": "Maximum results to return."},
        },
        "required": ["query"],
    }

    def execute(self, query: str, node_type: str = "function", max_results: int = 20) -> dict:
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path
        db_path = get_default_db_path()
        if not db_path.exists():
            return {"results": [], "error": "No graph database. Analyze a repo first."}
        kg = KnowledgeGraph(str(db_path))
        try:
            results = kg.search_by_pattern(node_type, query)
            return {
                "results": [
                    {"id": r["n.id"], "label": r["n.label"], "type": r["n.type"],
                     "file": r["n.file_path"], "line": r["n.line_number"],
                     "signature": r.get("n.signature", ""),
                     "docstring": r.get("n.docstring", "")}
                    for r in results[:max_results]
                ],
                "total": len(results),
            }
        finally:
            kg.close()


class SearchSemanticTool(Tool):
    """Semantic code search using three-layer adaptive retrieval."""

    name = "search_semantic"
    description = "Semantic code search using the three-layer engine (structural → vector → graph). Uses real TF-IDF embeddings or OpenAI API."

    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language query describing what you're looking for."},
            "max_results": {"type": "integer", "default": 20, "description": "Maximum results."},
        },
        "required": ["query"],
    }

    def execute(self, query: str, max_results: int = 20) -> dict:
        from search.engine import get_search_engine
        from graph.kuzu_store import get_default_db_path
        engine = get_search_engine(str(get_default_db_path()))
        response = engine.search(query, max_results=max_results)
        return {
            "results": [
                {
                    "id": r.node_id, "label": r.label, "type": r.node_type,
                    "file": r.file_path, "line": r.line_number,
                    "signature": r.signature, "docstring": r.docstring,
                    "score": r.score, "source_layer": r.source_layer,
                }
                for r in response.results
            ],
            "total": response.total_found,
            "layers_consulted": response.layers_consulted,
            "escalation_path": response.escalation_path,
            "query": response.query,
        }


class TraverseGraphTool(Tool):
    """Traverse code knowledge graph — explore dependencies, callers, callees."""

    name = "traverse_graph"
    description = "Traverse code knowledge graph — explore dependencies, callers, callees, and impact of a node."

    input_schema = {
        "type": "object",
        "properties": {
            "node_id": {"type": "string", "description": "Node ID to start traversal from."},
            "hops": {"type": "integer", "default": 2, "description": "Number of hops (max 5)."},
        },
        "required": ["node_id"],
    }

    def execute(self, node_id: str, hops: int = 2) -> dict:
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path
        db_path = get_default_db_path()
        if not db_path.exists():
            return {"neighbors": [], "error": "No graph database."}
        kg = KnowledgeGraph(str(db_path))
        try:
            neighbors = kg.traverse_neighbors(node_id, min(hops, 5))
            impact = kg.impact_analysis(node_id)
            return {
                "node_id": node_id,
                "neighbors": neighbors,
                "dependents": impact.get("dependents", []),
                "dependencies": impact.get("dependencies", []),
                "total_affected": impact.get("total_affected", 0),
            }
        finally:
            kg.close()


class GetConventionsTool(Tool):
    """Get coding conventions (.agent-conventions.yaml)."""

    name = "get_conventions"
    description = "Get project coding conventions and architectural rules from .agent-conventions.yaml."

    input_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self) -> dict:
        from memory.store import load_conventions
        content = load_conventions()
        return {"conventions": content, "source": "stored" if content else "none"}


class GetContextTool(Tool):
    """Get code context for a task — conventions, related functions, recent changes."""

    name = "get_context"
    description = "Get code context for a coding task — conventions, related functions, dependency graph, recent changes."

    input_schema = {
        "type": "object",
        "properties": {
            "task_description": {"type": "string", "description": "Description of the coding task."},
            "current_file": {"type": "string", "default": "", "description": "Current file being edited."},
            "max_tokens": {"type": "integer", "default": 4000, "description": "Max output tokens."},
        },
        "required": ["task_description"],
    }

    def execute(self, task_description: str, current_file: str = "", max_tokens: int = 4000) -> dict:
        from memory.store import load_conventions, get_recent_episodes
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path

        conventions = load_conventions()
        db_path = get_default_db_path()

        related_functions = []
        if db_path.exists():
            kg = KnowledgeGraph(str(db_path))
            try:
                keywords = task_description.split()
                for kw in keywords[:3]:
                    if len(kw) > 2:
                        results = kg.search_by_pattern("function", kw)
                        for r in results[:5]:
                            related_functions.append({
                                "name": r["n.label"],
                                "signature": r.get("n.signature", ""),
                                "file": r["n.file_path"],
                                "line": r["n.line_number"],
                                "docstring": r.get("n.docstring", ""),
                            })
            finally:
                kg.close()

        recent = get_recent_episodes(30)
        return {
            "task": task_description,
            "current_file": current_file,
            "conventions": conventions,
            "related_functions": related_functions[:15],
            "recent_changes": [
                {"date": e["date"], "type": e["type"], "description": e["description"]}
                for e in recent if e["type"] == "change"
            ][:10],
        }


class AskQuestionTool(Tool):
    """Ask a natural language question about the codebase."""

    name = "ask_question"
    description = "Ask a natural language question about the codebase. Uses LLM + code search to answer."

    input_schema = {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "Your question about the codebase."}},
        "required": ["question"],
    }

    def execute(self, question: str) -> dict:
        from search.llm import get_llm
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path

        db_path = get_default_db_path()
        context = ""
        if db_path.exists():
            kg = KnowledgeGraph(str(db_path))
            try:
                keywords = [w for w in question.split() if len(w) > 2]
                if keywords:
                    results = kg.search_by_pattern("", keywords[0])
                    context = "\n".join(
                        f"- {r['n.label']} ({r['n.type']}) @ {r['n.file_path']}:{r['n.line_number']}"
                        for r in results[:10]
                    )
            finally:
                kg.close()

        llm = get_llm()
        if llm.available:
            answer = llm.answer_question(question, context)
            return {"question": question, "answer": answer, "source": "llm"}
        return {"question": question, "answer": f"LLM unavailable.\nRelevant code:\n{context[:1000]}", "source": "search"}


class AnalyzeImpactTool(Tool):
    """Analyze impact of a git diff or code entity change."""

    name = "analyze_impact"
    description = "Analyze impact of a git diff or code entity change. Supports entity-level and git-diff modes."

    input_schema = {
        "type": "object",
        "properties": {
            "node_id": {"type": "string", "description": "Node ID for entity-level impact analysis."},
            "repo_path": {"type": "string", "description": "Repo path for git diff analysis."},
            "commit_range": {"type": "string", "default": "HEAD~1..HEAD", "description": "Git commit range."},
        },
    }

    def execute(self, node_id: str = "", repo_path: str = "", commit_range: str = "HEAD~1..HEAD") -> dict:
        if repo_path:
            from impact.analyzer import DiffAnalyzer
            analyzer = DiffAnalyzer(repo_path=repo_path)
            result = analyzer.analyze(commit_range=commit_range)
            return {
                "mode": "git_diff",
                "commit_range": commit_range,
                "changed_files": result.changed_files,
                "changed_entities": [
                    {"name": e.entity_name, "type": e.entity_type, "file": e.file_path, "change": e.change_type}
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

        if node_id:
            from graph.kuzu_store import KnowledgeGraph, get_default_db_path
            db_path = get_default_db_path()
            if not db_path.exists():
                return {"error": "No graph database."}
            kg = KnowledgeGraph(str(db_path))
            try:
                impact = kg.impact_analysis(node_id)
                return {"mode": "entity", "node_id": node_id, **impact}
            finally:
                kg.close()

        return {"error": "Provide node_id or repo_path."}


class SearchDocsTool(Tool):
    """Search documentation — Markdown files, source comments, API docs."""

    name = "search_docs"
    description = "Search documentation — Markdown files, source comments, and API docs."

    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "default": 20},
        },
        "required": ["query"],
    }

    def execute(self, query: str, max_results: int = 20) -> dict:
        from search.doc_indexer import DocIndexer
        indexer = DocIndexer()
        results = indexer.search_docs(query, max_results)
        return {"results": results, "total": len(results)}


class ReviewCodeTool(Tool):
    """Review code changes — diff or snippet — with static analysis + LLM."""

    name = "review_code"
    description = "Review code changes (diff or code snippet) with static analysis rules and optional LLM assessment. Returns issues, strengths, risk level, and score."

    input_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Code diff (unified format) or code snippet to review."},
            "mode": {"type": "string", "default": "diff", "description": "'diff' for git diff review, 'snippet' for code snippet review."},
            "repo_path": {"type": "string", "default": "", "description": "Repository path for graph context (only for diff mode)."},
            "language": {"type": "string", "default": "", "description": "Programming language (only for snippet mode)."},
        },
        "required": ["code"],
    }

    def execute(self, code: str, mode: str = "diff", repo_path: str = "", language: str = "") -> dict:
        from review.reviewer import review_diff, review_code
        if mode == "snippet":
            return review_code(code, language=language, file_path="")
        return review_diff(code, repo_path=repo_path)


class GenerateTourTool(Tool):
    """Generate a guided code tour of the repository."""

    name = "generate_tour"
    description = "Generate a guided code tour highlighting key files, hot spots, and entry points in the codebase."

    input_schema = {
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "default": "", "description": "Repository path."},
            "max_stops": {"type": "integer", "default": 10},
        },
    }

    def execute(self, repo_path: str = "", max_stops: int = 10) -> dict:
        from review.tour import generate_tour
        stops = generate_tour(repo_path, max_stops)
        return {"stops": stops, "total": len(stops)}


class GenerateQuestionsTool(Tool):
    """Generate suggested questions about the codebase."""

    name = "generate_questions"
    description = "Generate suggested questions a developer might ask about this codebase."

    input_schema = {
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "default": "", "description": "Repository path."},
            "max_questions": {"type": "integer", "default": 5},
        },
    }

    def execute(self, repo_path: str = "", max_questions: int = 5) -> dict:
        from review.tour import generate_questions
        questions = generate_questions(repo_path, max_questions)
        return {"questions": questions, "total": len(questions)}
