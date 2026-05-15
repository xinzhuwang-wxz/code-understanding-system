"""
Evaluation benchmark framework for CodeKG search quality.

Computes:
  - recall@K (K=1,3,5,10): fraction of queries where correct item is in top-K
  - MRR: Mean Reciprocal Rank
  - Precision@5
  - NDCG@5

Usage:
  python -m tests.benchmark run --queries queries.json
  python -m tests.benchmark generate --repo /path/to/repo --output queries.json
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class QueryCase:
    """A single evaluation query with ground truth."""
    query: str
    node_type: str = "function"
    expected_ids: list[str] = field(default_factory=list)  # Known correct node IDs
    expected_labels: list[str] = field(default_factory=list)  # Known correct labels
    category: str = "general"  # "structural", "semantic", "graph"


@dataclass
class QueryResult:
    """Result of evaluating a single query."""
    query: str
    rank: int = -1  # 1-indexed rank of first correct result, -1 if not found
    found_id: str = ""
    found_label: str = ""
    found_at_rank: int = -1
    total_results: int = 0
    latency_ms: float = 0.0


@dataclass
class BenchmarkReport:
    """Full benchmark report."""
    total_queries: int = 0
    recall_at_1: float = 0.0
    recall_at_3: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    precision_at_5: float = 0.0
    avg_latency_ms: float = 0.0
    per_query: list[QueryResult] = field(default_factory=list)
    by_category: dict[str, dict] = field(default_factory=dict)


def load_queries(path: str) -> list[QueryCase]:
    """Load evaluation queries from a JSON file."""
    with open(path) as f:
        data = json.load(f)

    queries = []
    for item in data.get("queries", []):
        queries.append(QueryCase(
            query=item["query"],
            node_type=item.get("node_type", "function"),
            expected_ids=item.get("expected_ids", []),
            expected_labels=item.get("expected_labels", []),
            category=item.get("category", "general"),
        ))
    return queries


def evaluate_query(
    qc: QueryCase,
    search_fn,
) -> QueryResult:
    """Evaluate a single query against a search function.

    Args:
        qc: Query case with ground truth.
        search_fn: Callable that takes (query, node_type, max_results) → list of
                    dicts with keys: node_id, label, type, score.

    Returns:
        QueryResult with ranking info.
    """
    start = time.time()
    results = search_fn(qc.query, qc.node_type, 20)
    elapsed = (time.time() - start) * 1000

    result = QueryResult(
        query=qc.query,
        total_results=len(results),
        latency_ms=round(elapsed, 1),
    )

    # Check ground truth match — prefer ID match, fall back to label
    for rank, r in enumerate(results):
        r_id = r.get("node_id", "")
        r_label = r.get("label", "")

        if qc.expected_ids and r_id in qc.expected_ids:
            result.rank = rank + 1
            result.found_id = r_id
            result.found_label = r_label
            result.found_at_rank = rank + 1
            break

        if qc.expected_labels and r_label in qc.expected_labels:
            result.rank = rank + 1
            result.found_id = r_id
            result.found_label = r_label
            result.found_at_rank = rank + 1
            break

    return result


def compute_metrics(results: list[QueryResult], queries: list[QueryCase]) -> BenchmarkReport:
    """Compute benchmark metrics from per-query results."""
    report = BenchmarkReport()
    report.total_queries = len(results)
    report.per_query = results

    ranks = [r.rank for r in results if r.rank > 0]

    # Recall@K
    def recall_at(k: int) -> float:
        if not results:
            return 0.0
        found = sum(1 for r in results if 0 < r.rank <= k)
        return found / len(results)

    report.recall_at_1 = recall_at(1)
    report.recall_at_3 = recall_at(3)
    report.recall_at_5 = recall_at(5)
    report.recall_at_10 = recall_at(10)

    # MRR
    report.mrr = sum(1.0 / r.rank for r in results if r.rank > 0) / max(len(results), 1)

    # Precision@5
    total_prec = 0.0
    for r in results:
        if r.rank > 0 and r.rank <= 5:
            total_prec += 1.0 / 5
    report.precision_at_5 = total_prec / max(len(results), 1)

    # Average latency
    report.avg_latency_ms = sum(r.latency_ms for r in results) / max(len(results), 1)

    # By category
    categories: dict[str, list[tuple[QueryResult, QueryCase]]] = {}
    for r, qc in zip(results, queries):
        cat = qc.category or "general"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((r, qc))

    for cat, cat_pairs in categories.items():
        cat_results = [p[0] for p in cat_pairs]
        report.by_category[cat] = {
            "count": len(cat_results),
            "recall@5": recall_at_for_list(cat_results, 5),
            "mrr": sum(1.0 / r.rank for r in cat_results if r.rank > 0) / max(len(cat_results), 1),
            "avg_latency_ms": sum(r.latency_ms for r in cat_results) / max(len(cat_results), 1),
        }

    return report


def recall_at_for_list(results: list[QueryResult], k: int) -> float:
    if not results:
        return 0.0
    found = sum(1 for r in results if 0 < r.rank <= k)
    return found / len(results)


def run_benchmark(queries_path: str) -> BenchmarkReport:
    """Run the full benchmark against the current CodeKG database.

    Args:
        queries_path: Path to the queries JSON file.
    """
    from search.engine import get_search_engine
    from graph.kuzu_store import get_default_db_path

    queries = load_queries(queries_path)
    engine = get_search_engine(str(get_default_db_path()))

    def search_fn(query: str, node_type: str, max_results: int) -> list[dict]:
        response = engine.search(query, node_type, max_results)
        return [
            {
                "node_id": r.node_id,
                "label": r.label,
                "type": r.node_type,
                "score": r.score,
            }
            for r in response.results
        ]

    results = []
    for qc in queries:
        r = evaluate_query(qc, search_fn)
        results.append(r)

    return compute_metrics(results, queries)


def generate_queries_from_repo(
    repo_path: str,
    output_path: str,
    num_queries: int = 30,
) -> list[QueryCase]:
    """Auto-generate evaluation queries from a repository.

    Strategy:
      1. Pick random functions from the analyzed graph
      2. Extract their docstrings, label, or signature as the query
      3. Use the original function as ground truth

    Args:
        repo_path: Path to the repository (must be already analyzed).
        output_path: Where to save the generated queries JSON.
        num_queries: Number of queries to generate.
    """
    import random
    from graph.kuzu_store import KnowledgeGraph, get_default_db_path

    db_path = get_default_db_path()
    if not db_path.exists():
        raise FileNotFoundError(
            f"No database found at {db_path}. Analyze a repo first with /api/analyze."
        )

    kg = KnowledgeGraph(str(db_path))
    try:
        # Get a random sample of functions with docstrings
        functions = kg.query(
            "MATCH (n:Node) WHERE n.type = 'function' AND n.docstring <> '' "
            "RETURN n.id, n.label, n.signature, n.docstring, n.file_path "
            "LIMIT 500"
        )
        if len(functions) < num_queries:
            # Fall back to all nodes
            functions = kg.query(
                "MATCH (n:Node) WHERE n.docstring <> '' "
                "RETURN n.id, n.label, n.signature, n.docstring, n.file_path "
                "LIMIT 500"
            )

        sampled = random.sample(functions, min(num_queries, len(functions)))
        queries = []

        for fn in sampled:
            label = fn.get("n.label", "")
            doc = fn.get("n.docstring", "")
            sig = fn.get("n.signature", "")
            nid = fn.get("n.id", "")

            # Use docstring as query (most natural) or label as fallback
            query_text = doc[:100] if doc else label

            # Heuristic: pick a category based on query type
            if "import" in query_text.lower() or "call" in query_text.lower():
                category = "graph"
            elif len(query_text) > 30:
                category = "semantic"
            else:
                category = "structural"

            queries.append({
                "query": query_text,
                "node_type": "function",
                "expected_ids": [nid],
                "expected_labels": [label],
                "category": category,
            })

        with open(output_path, "w") as f:
            json.dump({"queries": queries, "generated_from": repo_path}, f, indent=2)

        return [
            QueryCase(
                query=q["query"],
                node_type=q["node_type"],
                expected_ids=q["expected_ids"],
                expected_labels=q["expected_labels"],
                category=q["category"],
            )
            for q in queries
        ]
    finally:
        kg.close()


def format_report(report: BenchmarkReport) -> str:
    """Format a benchmark report as a readable string."""
    lines = [
        "=" * 60,
        "  CodeKG Search Benchmark Report",
        "=" * 60,
        "",
        f"  Total Queries: {report.total_queries}",
        "",
        "  ── Overall Metrics ──",
        f"  Recall@1:   {report.recall_at_1:.3f}",
        f"  Recall@3:   {report.recall_at_3:.3f}",
        f"  Recall@5:   {report.recall_at_5:.3f}",
        f"  Recall@10:  {report.recall_at_10:.3f}",
        f"  MRR:         {report.mrr:.3f}",
        f"  Precision@5: {report.precision_at_5:.3f}",
        f"  Avg Latency: {report.avg_latency_ms:.1f}ms",
        "",
    ]

    if report.by_category:
        lines.append("  ── By Category ──")
        for cat, metrics in sorted(report.by_category.items()):
            lines.append(f"  {cat}: recall@5={metrics['recall@5']:.3f}  MRR={metrics['mrr']:.3f}  "
                        f"latency={metrics['avg_latency_ms']:.1f}ms  (n={metrics['count']})")

    lines.append("")
    lines.append("  ── Per-Query Details ──")
    for i, r in enumerate(report.per_query):
        status = "✓" if r.rank > 0 else "✗"
        rank_str = f"rank={r.rank}" if r.rank > 0 else "not found"
        lines.append(f"  [{status}] {r.query[:60]:<60} {rank_str:<12} {r.latency_ms:.0f}ms")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ─── CLI Entry Point ───────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tests.benchmark <run|generate> [args]")
        print("  run --queries queries.json")
        print("  generate --repo /path/to/repo --output queries.json")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "run":
        queries_path = None
        for i, arg in enumerate(sys.argv):
            if arg == "--queries" and i + 1 < len(sys.argv):
                queries_path = sys.argv[i + 1]
        if not queries_path:
            print("Error: --queries is required")
            sys.exit(1)
        report = run_benchmark(queries_path)
        print(format_report(report))

    elif cmd == "generate":
        repo_path = None
        output_path = "queries.json"
        for i, arg in enumerate(sys.argv):
            if arg == "--repo" and i + 1 < len(sys.argv):
                repo_path = sys.argv[i + 1]
            elif arg == "--output" and i + 1 < len(sys.argv):
                output_path = sys.argv[i + 1]
        if not repo_path:
            print("Error: --repo is required")
            sys.exit(1)
        queries = generate_queries_from_repo(repo_path, output_path)
        print(f"Generated {len(queries)} queries → {output_path}")
