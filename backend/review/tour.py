"""
Code tour generator — creates guided explorations of a codebase.

Analyzes the KuzuDB graph to find key files, hubs, and important
structures, then generates "N things you should know" with LLM
descriptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def generate_tour(repo_path: str = "", max_stops: int = 10) -> list[dict[str, Any]]:
    """Generate a guided code tour from the knowledge graph.

    Args:
        repo_path: Repository path (for file access). Optional.
        max_stops: Maximum number of tour stops.

    Returns:
        List of tour stops, each with title, description, file, line, etc.
    """
    from graph.kuzu_store import KnowledgeGraph, get_default_db_path

    db_path = get_default_db_path()
    if not db_path.exists():
        return _empty_tour("No knowledge graph found. Analyze a repo first.")

    kg = KnowledgeGraph(str(db_path))
    tour_stops: list[dict[str, Any]] = []

    try:
        # 1. Most connected files (hubs)
        hubs = kg.query("""
            MATCH (f:File)-[:FileContains]->(n:Node)
            WITH f, count(n) AS node_count
            RETURN f.path AS path, f.language AS lang, node_count
            ORDER BY node_count DESC
            LIMIT 5
        """)
        for hub in hubs:
            path = hub.get("f.path", "") or hub.get("path", "")
            if not path:
                continue
            tour_stops.append({
                "id": f"file:{path}",
                "title": f"📁 {Path(path).name}",
                "description": f"Hub file with {hub.get('node_count', 0)} symbols",
                "file_path": path,
                "type": "file",
                "line": 1,
            })

        # 2. Most referenced functions (hot spots)
        hotspots = kg.query("""
            MATCH (n:Node)<-[r]-(:Node)
            WITH n, count(r) AS ref_count
            RETURN n.id, n.label, n.file_path, n.signature, ref_count
            ORDER BY ref_count DESC
            LIMIT 5
        """)
        for hs in hotspots:
            file_path = hs.get("n.file_path", "")
            if not file_path:
                continue
            tour_stops.append({
                "id": hs.get("n.id", ""),
                "title": f"⚡ {hs.get('n.label', '')}",
                "description": f"Referenced {hs.get('ref_count', 0)} times",
                "file_path": file_path,
                "signature": hs.get("n.signature", ""),
                "type": "function",
                "line": 0,
            })

        # 3. Entry points (files with no incoming references)
        entries = kg.query("""
            MATCH (f:File)
            WHERE NOT EXISTS {
                MATCH (f)<-[:FileContains]-()
            }
            AND f.path CONTAINS 'main' OR f.path CONTAINS 'index' OR f.path CONTAINS 'app'
            RETURN f.path, f.language
            LIMIT 3
        """)
        for ent in entries:
            path = ent.get("f.path", "") or ent.get("path", "")
            if not path:
                continue
            tour_stops.append({
                "id": f"file:{path}",
                "title": f"🚪 {Path(path).name}",
                "description": "Entry point / main module",
                "file_path": path,
                "type": "entry",
                "line": 1,
            })

        kg.close()
    except Exception:
        kg.close()
        return _empty_tour("Error querying knowledge graph")

    # LLM enrichment for descriptions
    if tour_stops:
        try:
            _enrich_descriptions(tour_stops)
        except Exception:
            pass

    return tour_stops[:max_stops]


def _enrich_descriptions(stops: list[dict[str, Any]]) -> None:
    """Use LLM to enrich tour stop descriptions (best-effort)."""
    from search.llm import get_llm
    llm = get_llm()
    if not llm.available:
        return

    for stop in stops:
        if not stop.get("description") or stop["description"].startswith("Hub file"):
            try:
                prompt = (
                    f"In one sentence, what does the code entity '{stop['title']}' "
                    f"typically do in a software project?"
                )
                desc = llm.answer_question(prompt, context="")
                if desc and len(desc) > 10:
                    stop["description"] = desc.strip()[:200]
            except Exception:
                pass


def _empty_tour(reason: str) -> list[dict[str, Any]]:
    return [{
        "id": "info",
        "title": "No tour available",
        "description": reason,
        "file_path": "",
        "type": "info",
        "line": 0,
    }]


def generate_questions(repo_path: str = "", max_questions: int = 5) -> list[dict[str, Any]]:
    """Generate suggested questions about the codebase.

    Analyzes the graph structure and uses LLM to suggest relevant questions.

    Returns:
        List of question dicts with 'question', 'category', 'context'.
    """
    from graph.kuzu_store import KnowledgeGraph, get_default_db_path

    db_path = get_default_db_path()
    questions: list[dict[str, Any]] = []

    if not db_path.exists():
        return _fallback_questions("Analyze a repo first.")

    kg = KnowledgeGraph(str(db_path))

    try:
        stats = kg.stats()
        total_nodes = stats.get("nodes", 0)
        total_edges = stats.get("edges", 0)

        type_dist = kg.query(
            "MATCH (n:Node) RETURN n.type AS type, count(n) AS cnt "
            "ORDER BY cnt DESC LIMIT 10"
        )
        top_types = [
            f"{r.get('type', 'unknown')}" for r in type_dist[:5]
        ]

        kg.close()

        questions.append({
            "question": f"What does the {top_types[0] if top_types else 'main'} module do?",
            "category": "architecture",
            "context": f"Codebase has {total_nodes} nodes and {total_edges} edges",
        })
        questions.append({
            "question": "What are the most important functions to understand?",
            "category": "navigation",
            "context": f"Top types: {', '.join(top_types[:3])}",
        })
    except Exception:
        kg.close()
        return _fallback_questions()

    # LLM-generated questions
    try:
        from search.llm import get_llm
        llm = get_llm()
        if llm.available:
            prompt = (
                f"Generate {max_questions} questions a developer new to this codebase "
                f"might ask. The codebase has {total_nodes} symbols "
                f"across types: {', '.join(top_types[:5])}. "
                "Return as JSON array: [{\"question\": \"...\", \"category\": \"...\"}]"
            )
            response = llm.answer_question(prompt, context="")
            import json, re
            m = re.search(r'\[.*\]', response, re.DOTALL)
            if m:
                extra = json.loads(m.group(0))
                for eq in extra[:max_questions]:
                    if isinstance(eq, dict) and "question" in eq:
                        questions.append(eq)
    except Exception:
        pass

    return questions[:max_questions]


def _fallback_questions(hint: str = "") -> list[dict[str, Any]]:
    return [
        {
            "question": "How is the project structured?",
            "category": "architecture",
            "context": hint or "Default question",
        },
        {
            "question": "What are the main entry points?",
            "category": "navigation",
            "context": hint or "Default question",
        },
    ]
