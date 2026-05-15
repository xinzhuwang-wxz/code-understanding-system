"""
Self-contained MCP Server — stdio JSON-RPC 2.0, no external SDK required.

Implements the Model Context Protocol directly over stdin/stdout.
Compatible with Claude Code, Codex, OpenClaw, Hermes Agent, and any
MCP-compliant client.

Usage:
    python3 -m mcp.server_standalone
    PYTHONPATH=backend python3 backend/mcp/server_standalone.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure backend in PYTHONPATH
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ─── Tool Implementations ──────────────────────────────────────────

def tool_list_tools() -> list[dict]:
    """List all available tools."""
    return [
        {
            "name": "search_by_pattern",
            "description": "Search code symbols by name pattern (exact/BM25).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "node_type": {"type": "string", "default": "function"},
                    "max_results": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_semantic",
            "description": "Semantic code search using natural language.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query."},
                    "max_results": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        },
        {
            "name": "traverse_graph",
            "description": "Traverse code knowledge graph — explore dependencies, callers, callees.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Node ID."},
                    "hops": {"type": "integer", "default": 2},
                },
                "required": ["node_id"],
            },
        },
        {
            "name": "get_conventions",
            "description": "Get coding conventions (.agent-conventions.yaml).",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_context",
            "description": "Get code context for a task — conventions, related functions, recent changes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string"},
                    "current_file": {"type": "string", "default": ""},
                    "max_tokens": {"type": "integer", "default": 4000},
                },
                "required": ["task_description"],
            },
        },
        {
            "name": "ask_question",
            "description": "Ask a natural language question about the codebase.",
            "inputSchema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
        {
            "name": "analyze_impact",
            "description": "Analyze impact of modifying a code entity.",
            "inputSchema": {
                "type": "object",
                "properties": {"node_id": {"type": "string"}},
                "required": ["node_id"],
            },
        },
    ]


def call_tool(name: str, arguments: dict) -> Any:
    """Route tool call to the appropriate handler."""
    if name == "search_by_pattern":
        return _search_by_pattern(**arguments)
    elif name == "search_semantic":
        return _search_semantic(**arguments)
    elif name == "traverse_graph":
        return _traverse_graph(**arguments)
    elif name == "get_conventions":
        return _get_conventions()
    elif name == "get_context":
        return _get_context(**arguments)
    elif name == "ask_question":
        return _ask_question(**arguments)
    elif name == "analyze_impact":
        return _analyze_impact(**arguments)
    else:
        return {"error": f"Unknown tool: {name}"}


# ─── Tool Handlers ─────────────────────────────────────────────────

def _search_by_pattern(query: str, node_type: str = "function", max_results: int = 20) -> dict:
    from graph.kuzu_store import KnowledgeGraph
    db_path = Path.home() / ".code-kg" / "graph"
    if not db_path.exists():
        return {"results": [], "error": "No graph database. Analyze a repo first."}
    kg = KnowledgeGraph(str(db_path))
    results = kg.search_by_pattern(node_type, query)
    kg.close()
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


def _search_semantic(query: str, max_results: int = 20) -> dict:
    from graph.kuzu_store import KnowledgeGraph
    db_path = Path.home() / ".code-kg" / "graph"
    if not db_path.exists():
        return {"results": [], "error": "No graph database."}
    kg = KnowledgeGraph(str(db_path))
    # Fallback to pattern search
    results = kg.search_by_pattern("", query)
    kg.close()
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


def _traverse_graph(node_id: str, hops: int = 2) -> dict:
    from graph.kuzu_store import KnowledgeGraph
    db_path = Path.home() / ".code-kg" / "graph"
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


def _get_conventions() -> dict:
    from memory.store import load_conventions
    content = load_conventions()
    return {"conventions": content, "source": "stored" if content else "none"}


def _get_context(task_description: str, current_file: str = "", max_tokens: int = 4000) -> dict:
    from memory.store import load_conventions, get_recent_episodes
    from graph.kuzu_store import KnowledgeGraph

    conventions = load_conventions()
    db_path = Path.home() / ".code-kg" / "graph"

    related_functions = []
    if db_path.exists():
        kg = KnowledgeGraph(str(db_path))
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


def _ask_question(question: str) -> dict:
    from search.llm import get_llm
    from graph.kuzu_store import KnowledgeGraph

    db_path = Path.home() / ".code-kg" / "graph"
    context = ""
    if db_path.exists():
        kg = KnowledgeGraph(str(db_path))
        keywords = [w for w in question.split() if len(w) > 2]
        if keywords:
            results = kg.search_by_pattern("", keywords[0])
            context = "\n".join(
                f"- {r['n.label']} ({r['n.type']}) @ {r['n.file_path']}:{r['n.line_number']}"
                for r in results[:10]
            )
        kg.close()

    llm = get_llm()
    if llm.available:
        answer = llm.answer_question(question, context)
        return {"question": question, "answer": answer, "source": "llm"}
    return {"question": question, "answer": f"LLM unavailable.\nRelevant code:\n{context[:1000]}", "source": "search"}


def _analyze_impact(node_id: str) -> dict:
    from graph.kuzu_store import KnowledgeGraph
    db_path = Path.home() / ".code-kg" / "graph"
    if not db_path.exists():
        return {"error": "No graph database."}
    kg = KnowledgeGraph(str(db_path))
    impact = kg.impact_analysis(node_id)
    kg.close()
    return impact


# ─── JSON-RPC 2.0 Server ───────────────────────────────────────────

def _send_response(id_val: Any, result: Any) -> None:
    """Send a JSON-RPC success response to stdout."""
    response = json.dumps({
        "jsonrpc": "2.0",
        "id": id_val,
        "result": result,
    }, ensure_ascii=False, default=str)
    sys.stdout.write(response + "\n")
    sys.stdout.flush()


def _send_error(id_val: Any, code: int, message: str) -> None:
    """Send a JSON-RPC error response."""
    response = json.dumps({
        "jsonrpc": "2.0",
        "id": id_val,
        "error": {"code": code, "message": message},
    }, ensure_ascii=False)
    sys.stdout.write(response + "\n")
    sys.stdout.flush()


def main() -> None:
    """Main MCP server loop — reads JSON-RPC from stdin, writes to stdout."""
    # Log to stderr so stdout stays clean for JSON-RPC
    print("[code-kg MCP] Server starting on stdio...", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        try:
            if method == "tools/list":
                _send_response(req_id, {"tools": tool_list_tools()})
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                result = call_tool(tool_name, tool_args)
                _send_response(req_id, {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                })
            elif method == "initialize":
                _send_response(req_id, {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "code-kg", "version": "0.2.0"},
                    "capabilities": {"tools": {}},
                })
            elif method == "notifications/initialized":
                pass  # No response needed for notifications
            else:
                _send_error(req_id, -32601, f"Method not found: {method}")
        except Exception as e:
            _send_error(req_id, -32603, str(e))
            print(f"[code-kg MCP] Error: {e}", file=sys.stderr, flush=True)

    print("[code-kg MCP] Server stopped.", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
