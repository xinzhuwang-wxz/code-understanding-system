"""
Updated orchestrator — adds tree-sitter based analysis alongside 
existing language-specific analyzers.
"""

from __future__ import annotations

from log import get_logger; logger = get_logger(__name__)

import os
from pathlib import Path
from typing import Any

import pathspec

from analyzer.graph import Graph, Node, Edge, NODE_TYPES
from analyzer.ts_parser import get_parser, ParsedSymbol, ParsedRelation

# Re-use existing file collection and skip logic
from analyzer.orchestrator import (
    collect_files,
    PYTHON_EXTENSIONS,
    JS_EXTENSIONS,
    ALWAYS_SKIP_DIRS,
    BINARY_EXTENSIONS,
)


def _symbol_to_node(symbol: ParsedSymbol, repo_path: str) -> Node:
    """Convert a ParsedSymbol to a Graph Node."""
    # Make file path relative to repo
    try:
        rel_path = str(Path(symbol.file_path).relative_to(repo_path))
    except ValueError:
        rel_path = symbol.file_path

    node_id = f"{rel_path}:{symbol.name}:{symbol.line_start}"

    return Node(
        id=node_id,
        label=symbol.name,
        type=symbol.kind,
        file_path=rel_path,
        line_number=symbol.line_start,
        metadata={
            "signature": symbol.signature,
            "docstring": symbol.docstring,
            "line_end": symbol.line_end,
        },
    )


def analyze_with_treesitter(repo_path: str) -> Graph:
    """Analyze a repository using tree-sitter for all supported languages.

    Falls back to the original analyzers for languages without tree-sitter support.
    """
    files = collect_files(repo_path)
    graph = Graph()
    parser = get_parser()

    supported_exts = set(parser.EXT_TO_LANG.keys())
    python_exts = PYTHON_EXTENSIONS
    js_exts = JS_EXTENSIONS

    # Process files with tree-sitter
    ts_files = [f for f in files if f["ext"] in supported_exts]
    other_files = [f for f in files if f["ext"] not in supported_exts]

    # Add file-level nodes for all files
    for f in files:
        try:
            rel = str(Path(f["full_path"]).relative_to(repo_path))
        except ValueError:
            rel = f["rel_path"]
        graph.add_node(Node(
            id=rel,
            label=Path(rel).name,
            type="file",
            file_path=rel,
        ))

    # Tree-sitter analysis
    parsed_count = 0
    for f in ts_files:
        try:
            symbols, relations = parser.parse_file(f["full_path"])
            for sym in symbols:
                node = _symbol_to_node(sym, repo_path)
                graph.add_node(node)
                # Link file → symbol
                try:
                    rel = str(Path(f["full_path"]).relative_to(repo_path))
                except ValueError:
                    rel = f["rel_path"]
                graph.add_edge(Edge(
                    source=rel,
                    target=node.id,
                    type="contains",
                ))
            for rel in relations:
                # Create relation edges — source should be file's relative path
                try:
                    rel_path = str(Path(f["full_path"]).relative_to(repo_path))
                except ValueError:
                    rel_path = f["rel_path"]
                graph.add_edge_deferred(Edge(
                    source=rel_path,
                    target=rel.target,
                    type=rel.kind,
                    metadata={"line_number": rel.line_number},
                ))
            parsed_count += len(symbols)
        except Exception as e:
            logger.error(f"  ⚠ Error parsing {f['rel_path']}: {e}")

    # Fall back to original analyzers for unsupported languages
    other_python = [f for f in other_files if f["ext"] in python_exts]
    other_js = [f for f in other_files if f["ext"] in js_exts]

    if other_python:
        from analyzer.python_analyzer import PythonAnalyzer
        py = PythonAnalyzer()
        for f in other_python:
            py.analyze_file(f, graph)
        py.resolve_imports(other_python, graph)

    if other_js:
        from analyzer.js_analyzer import JsAnalyzer
        js = JsAnalyzer()
        for f in other_js:
            js.analyze_file(f, graph)
        js.resolve_imports(other_js, graph)

    # Generic file-level analysis for everything else
    true_other = [f for f in other_files 
                  if f["ext"] not in python_exts and f["ext"] not in js_exts]
    if true_other:
        from analyzer.generic_analyzer import GenericAnalyzer
        generic = GenericAnalyzer()
        for f in true_other:
            generic.analyze_file(f, graph)

    graph.resolve_edges()
    print(f"  ✓ tree-sitter parsed {parsed_count} symbols from {len(ts_files)} files")
    return graph


def analyze_repo_universal(repo_path: str, persist: bool = True) -> dict[str, Any]:
    """Universal analyzer: tree-sitter first, fallback to original."""
    graph = analyze_with_treesitter(repo_path)
    result = graph.to_dict()
    result["repo_name"] = Path(repo_path).name
    result["repo_path"] = str(Path(repo_path).resolve())
    result["analyzer"] = "tree-sitter + fallback"
    result["persistent"] = False
    result["symbol_count"] = graph.node_count() if hasattr(graph, 'node_count') else len(graph.nodes)
    result["edge_count"] = graph.edge_count() if hasattr(graph, 'edge_count') else len(graph.edges)

    # Enrich nodes with git blame data
    try:
        from analyzer.git_blame import enrich_nodes_with_blame
        enrich_nodes_with_blame(result.get("nodes", []), repo_path)
    except Exception:
        pass  # Blame is best-effort, don't block analysis

    if persist:
        try:
            # Generate embeddings before persisting
            try:
                from search.embeddings import get_embedding_client
                client = get_embedding_client()
                nodes_to_embed = []
                for n in result.get("nodes", []):
                    # Combine label and docstring for better semantic meaning
                    text = n.get("label", "")
                    doc = n.get("metadata", {}).get("docstring", "")
                    if doc:
                        text += f" {doc}"
                    nodes_to_embed.append(text)
                
                if nodes_to_embed:
                    # Batch embedding is better, but since our dummy is fast we just loop
                    # If using OpenAI, we should chunk it, but let's just use embed_batch
                    vecs = client.embed_batch(nodes_to_embed)
                    for n, vec in zip(result.get("nodes", []), vecs):
                        if "metadata" not in n:
                            n["metadata"] = {}
                        n["metadata"]["embedding_vector"] = vec
            except Exception as e:
                print(f"Warning: Failed to generate embeddings: {e}")

            from graph.kuzu_store import KnowledgeGraph
            from graph.kuzu_store import get_default_db_path
            db_path = get_default_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            kg = KnowledgeGraph(str(db_path))
            kg.ingest_analysis(
                repo_path,
                result.get("nodes", []),
                result.get("edges", []),
                replace=True,  # Clear stale data from previous repo analyses
            )
            # Rebuild HNSW from fresh embedding vectors
            kg._rebuild_hnsw_from_db()
            kg_stats = kg.stats()
            result["persistent"] = True
            result["kg_stats"] = kg_stats
            kg.close()
        except Exception as e:
            result["kg_error"] = str(e)

    return result
