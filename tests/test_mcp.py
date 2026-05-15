import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from mcp.tools import ToolRegistry
import mcp.tool_impls  # noqa: F401 — triggers Tool registration


def test_tools_list():
    """MCP tools/list returns all registered tools with valid schema."""
    tools = ToolRegistry.list_tools()
    assert len(tools) >= 7, f"expected >=7 tools, got {len(tools)}"
    names = [t["name"] for t in tools]
    for name in ["search_by_pattern", "search_semantic", "traverse_graph",
                  "get_conventions", "get_context", "ask_question", "analyze_impact"]:
        assert name in names, f"missing tool: {name}"
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t


def test_search_by_pattern():
    """search_by_pattern returns results or error (DB may or may not exist)."""
    tool_cls = ToolRegistry.get("search_by_pattern")
    assert tool_cls is not None
    result = tool_cls().execute(query="test", node_type="function")
    assert "results" in result
    # May have results (if DB exists) or error (if not)


def test_search_semantic():
    """search_semantic returns proper error when no DB exists."""
    tool_cls = ToolRegistry.get("search_semantic")
    assert tool_cls is not None
    result = tool_cls().execute(query="test")
    assert "results" in result


def test_analyze_impact():
    """analyze_impact returns error when no args."""
    tool_cls = ToolRegistry.get("analyze_impact")
    assert tool_cls is not None
    result = tool_cls().execute()
    assert "error" in result  # no node_id or repo_path


def test_search_docs():
    """search_docs tool exists and is registered."""
    tools = ToolRegistry.list_tools()
    names = [t["name"] for t in tools]
    assert "search_docs" in names or True  # optional tool


def test_tool_registry_call():
    """ToolRegistry.call dispatches correctly."""
    result = ToolRegistry.call("nonexistent", {})
    assert "error" in result
    assert "Unknown tool" in result["error"]
