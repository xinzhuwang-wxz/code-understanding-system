"""
MCP Server — 8 tools for code understanding via Model Context Protocol.

Usage:
    python3 -m mcp.server    (stdio, for Claude Code / Codex / Hermes Agent)

Each tool is a standalone async function registered with the MCP server.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Add backend to path for imports
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ─── Tool Implementations ──────────────────────────────────────────


def tool_search_by_pattern(query: str, node_type: str = "function", max_results: int = 20) -> dict:
    """Search code symbols by name pattern (BM25/exact).

    Args:
        query: Search query string. Matches against symbol names.
        node_type: Filter by node type (function, class, file, etc.)
        max_results: Maximum number of results.
    """
    try:
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path
        db_path = get_default_db_path()
        if not db_path.exists():
            return {"results": [], "error": "No graph database. Analyze a repo first."}

        kg = KnowledgeGraph(str(db_path))
        results = kg.search_by_pattern(node_type, query)
        kg.close()

        return {
            "results": [
                {
                    "id": r["n.id"],
                    "label": r["n.label"],
                    "type": r["n.type"],
                    "file_path": r["n.file_path"],
                    "line": r["n.line_number"],
                    "signature": r.get("n.signature", ""),
                    "docstring": r.get("n.docstring", ""),
                }
                for r in results[:max_results]
            ],
            "total": len(results),
        }
    except Exception as e:
        return {"results": [], "error": str(e)}


def tool_search_semantic(query: str, max_results: int = 20) -> dict:
    """Semantic search using embedding similarity.

    Uses the three-layer search engine (structural → semantic → graph)
    with adaptive escalation. Produces real vector embeddings via a
    local transformers model or OpenAI API.

    Args:
        query: Natural language query describing what you're looking for.
        max_results: Maximum number of results.
    """
    try:
        from search.engine import get_search_engine
        engine = get_search_engine(str(get_default_db_path()))
        response = engine.search(query, max_results=max_results)
        code_results = [
            {
                "id": r.node_id,
                "label": r.label,
                "type": r.node_type,
                "file_path": r.file_path,
                "line": r.line_number,
                "signature": r.signature,
                "docstring": r.docstring,
                "score": r.score,
                "source_layer": r.source_layer,
            }
            for r in response.results
        ]
        return {
            "results": code_results[:max_results],
            "total": response.total_found,
            "layers_consulted": response.layers_consulted,
            "escalation_path": response.escalation_path,
            "query": response.query,
        }
    except Exception as e:
        return {"results": [], "error": str(e)}


def tool_traverse_graph(node_id: str, hops: int = 2) -> dict:
    """Traverse the code knowledge graph from a node.

    Args:
        node_id: Starting node ID (e.g., "src/auth.py:authenticate:42").
        hops: Number of hops to traverse (1-5).
    """
    try:
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path
        db_path = get_default_db_path()
        if not db_path.exists():
            return {"neighbors": [], "error": "No graph database."}

        kg = KnowledgeGraph(str(db_path))
        neighbors = kg.traverse_neighbors(node_id, min(hops, 5))
        impact = kg.impact_analysis(node_id)
        kg.close()

        return {
            "node_id": node_id,
            "neighbors": neighbors,
            "dependents": impact.get("dependents", []),
            "dependencies": impact.get("dependencies", []),
            "total_affected": impact.get("total_affected", 0),
        }
    except Exception as e:
        return {"neighbors": [], "error": str(e)}


def tool_get_conventions() -> dict:
    """Get the coding conventions for the current codebase.

    Returns the .agent-conventions.yaml content if available.
    """
    try:
        from memory.store import load_conventions
        content = load_conventions()
        return {
            "conventions": content,
            "source": "stored" if content else "none",
            "hint": "Auto-generate by analyzing a repo via /api/conventions",
        }
    except Exception as e:
        return {"conventions": "", "error": str(e)}


def tool_get_context(task_description: str, current_file: str = "", max_tokens: int = 4000) -> dict:
    """Get relevant code context for a coding task.

    Args:
        task_description: What you're trying to do (e.g., 'add refresh token to auth module').
        current_file: Path to the file you're working on.
        max_tokens: Token budget for the returned context.
    """
    try:
        from memory.store import load_conventions, get_recent_episodes
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path

        db_path = get_default_db_path()
        if not db_path.exists():
            return {"context": "No codebase analyzed yet.", "conventions": "", "related_functions": []}

        conventions = load_conventions()

        kg = KnowledgeGraph(str(db_path))

        # Find relevant functions
        keywords = task_description.replace(",", " ").split()
        related = kg.search_by_pattern("function", keywords[0] if keywords else "")
        related_functions = [
            {
                "name": r["n.label"], "signature": r.get("n.signature", ""),
                "file": r["n.file_path"], "line": r["n.line_number"],
                "docstring": r.get("n.docstring", ""),
            }
            for r in related[:10]
        ]

        # Get recent changes for current file
        recent = get_recent_episodes(30)
        recent_changes = [e for e in recent if e["type"] == "change"]

        kg.close()

        context_parts = [
            f"Task: {task_description}",
            f"Current file: {current_file}" if current_file else "",
        ]

        return {
            "context": "\n".join(filter(None, context_parts)),
            "conventions": conventions,
            "related_functions": related_functions,
            "recent_changes": recent_changes[:10],
            "token_estimate": len(conventions) + sum(len(json.dumps(f)) for f in related_functions),
        }
    except Exception as e:
        return {"context": "", "error": str(e)}


def tool_ask_question(question: str) -> dict:
    """Ask a natural language question about the codebase.

    Uses the search engine and LLM to answer.

    Args:
        question: Natural language question about the code.
    """
    try:
        from search.llm import get_llm

        # First, search for relevant code
        keywords = question.replace("?", "").replace(".", "").split()
        query = " ".join(k for k in keywords if len(k) > 2)

        from graph.kuzu_store import KnowledgeGraph, get_default_db_path
        db_path = get_default_db_path()
        kg = KnowledgeGraph(str(db_path)) if db_path.exists() else None

        context = ""
        if kg:
            results = kg.search_by_pattern("function", query) or kg.search_by_pattern("", query)
            context = "\n".join(
                f"- {r['n.label']} ({r['n.type']}) @ {r['n.file_path']}:{r['n.line_number']}"
                + (f"\n  {r.get('n.docstring', '')}" if r.get('n.docstring') else "")
                for r in results[:10]
            )
            kg.close()

        llm = get_llm()
        if llm.available:
            answer = llm.answer_question(question, context)
            return {"question": question, "answer": answer, "source": "llm"}
        else:
            return {
                "question": question,
                "answer": f"LLM not available. Here's relevant code:\n{context[:1000]}",
                "source": "search_only",
            }
    except Exception as e:
        return {"question": question, "answer": "", "error": str(e)}


def tool_analyze_impact(node_id: str) -> dict:
    """Analyze the impact of modifying a specific code entity.

    Args:
        node_id: Node ID to analyze (e.g., 'src/auth.py:authenticate:42').
    """
    try:
        from graph.kuzu_store import KnowledgeGraph, get_default_db_path
        from search.llm import get_llm

        db_path = get_default_db_path()
        if not db_path.exists():
            return {"error": "No graph database."}

        kg = KnowledgeGraph(str(db_path))
        impact = kg.impact_analysis(node_id)

        # Add LLM summary
        summary = ""
        llm = get_llm()
        if llm.available and impact["total_affected"] > 0:
            summary = llm.summarize_impact(node_id, impact["total_affected"])

        kg.close()
        return {
            "node_id": node_id,
            "dependents": impact["dependents"],
            "dependencies": impact["dependencies"],
            "total_affected": impact["total_affected"],
            "summary": summary,
        }
    except Exception as e:
        return {"error": str(e)}


def tool_search_docs(query: str, max_results: int = 20) -> dict:
    """Search documentation — Markdown files, source code comments, API docs."""
    try:
        from search.doc_indexer import DocIndexer
        indexer = DocIndexer()
        results = indexer.search_docs(query, max_results)
        return {"results": results, "total": len(results)}
    except Exception as e:
        return {"results": [], "error": str(e)}


def tool_review_code(code: str, mode: str = "diff", repo_path: str = "", language: str = "") -> dict:
    """Review code changes with static analysis + LLM."""
    try:
        from review.reviewer import review_diff, review_code
        if mode == "snippet":
            return review_code(code, language=language, file_path="")
        return review_diff(code, repo_path=repo_path)
    except Exception as e:
        return {"error": str(e), "issues": [], "score": 0}


def tool_generate_tour(repo_path: str = "", max_stops: int = 10) -> dict:
    """Generate a guided code tour."""
    try:
        from review.tour import generate_tour
        stops = generate_tour(repo_path, max_stops)
        return {"stops": stops, "total": len(stops)}
    except Exception as e:
        return {"stops": [], "error": str(e)}


def tool_generate_questions(repo_path: str = "", max_questions: int = 5) -> dict:
    """Generate suggested questions about the codebase."""
    try:
        from review.tour import generate_questions
        questions = generate_questions(repo_path, max_questions)
        return {"questions": questions, "total": len(questions)}
    except Exception as e:
        return {"questions": [], "error": str(e)}


# ─── Tool Registry ─────────────────────────────────────────────────

TOOL_REGISTRY = [
    {
        "name": "search_by_pattern",
        "description": "Search code symbols by name pattern (exact/BM25). Use for finding specific functions, classes, or files by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query — matches against symbol names."},
                "node_type": {"type": "string", "default": "function", "description": "Filter: function, class, file, etc."},
                "max_results": {"type": "integer", "default": 20, "description": "Maximum results."},
            },
            "required": ["query"],
        },
        "handler": tool_search_by_pattern,
    },
    {
        "name": "search_semantic",
        "description": "Semantic search using natural language. Describe what you're looking for in plain English.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query."},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        "handler": tool_search_semantic,
    },
    {
        "name": "traverse_graph",
        "description": "Traverse the code knowledge graph. Explore dependencies, callers, and callees of a code entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID (e.g., 'src/auth.py:authenticate:42')."},
                "hops": {"type": "integer", "default": 2, "description": "Traversal depth (1-5)."},
            },
            "required": ["node_id"],
        },
        "handler": tool_traverse_graph,
    },
    {
        "name": "get_conventions",
        "description": "Get coding conventions for the current codebase (.agent-conventions.yaml).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": tool_get_conventions,
    },
    {
        "name": "get_context",
        "description": "Get relevant code context for a coding task — conventions, related functions, recent changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_description": {"type": "string", "description": "What you're trying to do."},
                "current_file": {"type": "string", "default": "", "description": "File you're working on."},
                "max_tokens": {"type": "integer", "default": 4000, "description": "Token budget."},
            },
            "required": ["task_description"],
        },
        "handler": tool_get_context,
    },
    {
        "name": "ask_question",
        "description": "Ask a natural language question about the codebase. Uses LLM + search to answer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Your question about the code."},
            },
            "required": ["question"],
        },
        "handler": tool_ask_question,
    },
    {
        "name": "analyze_impact",
        "description": "Analyze the impact of modifying a code entity — what files/functions would be affected.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID to analyze."},
            },
            "required": ["node_id"],
        },
        "handler": tool_analyze_impact,
    },
    {
        "name": "search_docs",
        "description": "Search documentation — Markdown files, source code comments, and API docs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for documentation."},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        "handler": tool_search_docs,
    },
    {
        "name": "review_code",
        "description": "Review code changes (diff or snippet) with static analysis + LLM. Returns issues, strengths, risk level.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Unified diff or code snippet."},
                "mode": {"type": "string", "default": "diff", "enum": ["diff", "snippet"]},
                "repo_path": {"type": "string", "default": "", "description": "Repo path for graph context."},
                "language": {"type": "string", "default": ""},
            },
            "required": ["code"],
        },
        "handler": tool_review_code,
    },
    {
        "name": "generate_tour",
        "description": "Generate a guided code tour — highlights key files, hotspots, entry points.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": ""},
                "max_stops": {"type": "integer", "default": 10},
            },
        },
        "handler": tool_generate_tour,
    },
    {
        "name": "generate_questions",
        "description": "Generate suggested questions about the codebase for new developers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": ""},
                "max_questions": {"type": "integer", "default": 5},
            },
        },
        "handler": tool_generate_questions,
    },
]


# ─── MCP Server Entry Point ────────────────────────────────────────


async def run_mcp_server() -> None:
    """Run the MCP server on stdio using the official mcp SDK."""
    import logging
    import mcp.types as types
    from mcp.server import NotificationOptions, Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server

    logging.getLogger("mcp").setLevel(logging.WARNING)

    server = Server("code-kg")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOL_REGISTRY
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        args = arguments or {}
        tool = next((t for t in TOOL_REGISTRY if t["name"] == name), None)
        if tool is None:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": f"Tool not found: {name}", "code": "NOT_FOUND"}),
            )]

        try:
            # Run handler (sync or async)
            import asyncio
            handler = tool["handler"]
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**args)
            else:
                result = handler(**args)
            return [types.TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2),
            )]
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": str(e), "code": "INTERNAL_ERROR"}, ensure_ascii=False),
            )]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="code-kg",
                server_version="0.2.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    """Entry point for the MCP server."""
    import asyncio
    try:
        asyncio.run(run_mcp_server())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Fatal: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
