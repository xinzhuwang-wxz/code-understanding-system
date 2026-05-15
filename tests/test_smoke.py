"""Smoke tests for CodeKG backend.

Run with: pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
import json


def test_import_core():
    """Verify all core modules import successfully."""
    from analyzer.orchestrator_v2 import analyze_repo_universal
    from graph.kuzu_store import KnowledgeGraph, get_default_db_path
    from search.engine import SearchEngine, get_search_engine
    from search.llm import LLMClient, get_llm
    assert True


def test_import_mcp_tools():
    """Verify all MCP tools register without import errors."""
    from mcp.tools import ToolRegistry
    # Import to trigger auto-registration
    import mcp.tool_impls  # noqa: F401
    tools = ToolRegistry.list_tools()
    assert len(tools) >= 10, f"Expected >=10 tools, got {len(tools)}"
    tool_names = {t["name"] for t in tools}
    required = {
        "search_by_pattern", "search_semantic", "traverse_graph",
        "get_conventions", "get_context", "ask_question",
        "analyze_impact", "search_docs", "review_code",
        "generate_tour", "generate_questions",
    }
    missing = required - tool_names
    assert not missing, f"Missing MCP tools: {missing}"


def test_mcp_tools_have_schema():
    """Verify all MCP tools have valid input schemas."""
    import mcp.tool_impls  # noqa: F401
    from mcp.tools import ToolRegistry
    for tool_def in ToolRegistry.list_tools():
        assert "name" in tool_def, f"Tool missing name: {tool_def}"
        assert "description" in tool_def, f"Tool {tool_def['name']} missing description"
        schema = tool_def.get("inputSchema", {})
        assert "type" in schema, f"Tool {tool_def['name']} schema missing type"
        assert schema["type"] == "object"


def test_search_engine_three_layer():
    """Verify search engine initializes and produces a response (empty DB ok)."""
    from search.engine import SearchEngine
    engine = SearchEngine()
    response = engine.search("test_query", node_type="function")
    assert response.query == "test_query"
    assert isinstance(response.total_found, int)
    assert isinstance(response.total_latency_ms, float)
    assert isinstance(response.layers_consulted, list)
    # With empty DB, escalation should reach "zero" → semantic
    assert response.escalation_path or response.layers_consulted


def test_llm_client_graceful():
    """Verify LLM client degrades gracefully without a key."""
    # Temporarily hide .env to test fallback behavior
    from search.llm import LLMClient
    import pathlib
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    backup = None
    if env_path.exists():
        backup = env_path.read_text()
        env_path.rename(env_path.with_suffix(".env.bak"))
    try:
        os.environ.pop("DEEPSEEK_API_KEY", None)
        client = LLMClient()
        # Without .env or env var, should be unavailable
        assert not client.available
        result = client.explain_code("test_func", "def test_func():", "")
        assert result  # fallback should produce something
        result = client.summarize_impact("test_id", 5)
        assert result
        result = client.extract_conventions([])
        assert result == ""
    finally:
        if backup and env_path.with_suffix(".env.bak").exists():
            env_path.with_suffix(".env.bak").rename(env_path)


def test_kuzu_store_schema():
    """Verify KuzuDB schema initialization works."""
    import tempfile
    from graph.kuzu_store import KnowledgeGraph
    db_path = os.path.join(tempfile.mkdtemp(), "graph")
    kg = KnowledgeGraph(db_path)
    try:
        stats = kg.stats()
        assert stats["nodes"] == 0
        assert stats["edges"] == 0
        assert isinstance(stats["type_distribution"], list)
    finally:
        kg.close()


def test_rrf_fusion():
    """Verify Reciprocal Rank Fusion works correctly."""
    from search.engine import SearchResult, SearchEngine
    a = [SearchResult(node_id="1", label="a", node_type="fn", file_path="a.py", line_number=1, score=0.9)]
    b = [SearchResult(node_id="2", label="b", node_type="fn", file_path="b.py", line_number=1, score=0.8)]
    merged = SearchEngine._rbf_merge(a, b)
    assert len(merged) == 2


def test_memory_store():
    """Verify memory store atomic write works."""
    from memory.store import (
        save_conventions, load_conventions,
    )
    import tempfile
    import memory.store as ms

    tmpdir = tempfile.mkdtemp()
    orig_mem = ms.MEMORY_DIR
    orig_conv = ms.CONVENTIONS_FILE
    try:
        ms.MEMORY_DIR = ms.Path(tmpdir) / "memory"
        ms.CONVENTIONS_FILE = ms.MEMORY_DIR / "conventions.yaml"

        yaml_content = "codebase: test\nconventions:\n  python:\n    naming:\n      functions: snake_case\n"
        save_conventions(yaml_content)
        loaded = load_conventions()
        assert loaded == yaml_content
    finally:
        ms.MEMORY_DIR = orig_mem
        ms.CONVENTIONS_FILE = orig_conv


def test_impact_commit_range_validation():
    """Verify commit range validation blocks injection."""
    from impact.analyzer import _validate_commit_range
    # Valid ranges
    _validate_commit_range("HEAD~1..HEAD")
    _validate_commit_range("main..feature")
    _validate_commit_range("abc123..def456")
    _validate_commit_range("abc/def..ghi/jkl")
    # Invalid ranges
    import pytest
    with pytest.raises(ValueError):
        _validate_commit_range("HEAD~1; rm -rf /")
    with pytest.raises(ValueError):
        _validate_commit_range("HEAD~1 | cat /etc/passwd")
    with pytest.raises(ValueError):
        _validate_commit_range("")
