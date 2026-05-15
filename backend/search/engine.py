"""
Search engine — three-layer retrieval with adaptive escalation.

Layer 1: Structural (tree-sitter pattern matching + BM25)
Layer 2: Semantic (KuzuDB HNSW vector search)
Layer 3: Graph (Cypher traversal + LLM explanation)

Fusion: Reciprocal Rank Fusion (RRF, k=60)
Escalation: Mnemosyne-inspired healthy/zero/few/flat diagnosis
"""

from __future__ import annotations

from log import get_logger; logger = get_logger(__name__)

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """A single search result from any layer."""
    node_id: str
    label: str
    node_type: str
    file_path: str
    line_number: int
    signature: str = ""
    docstring: str = ""
    score: float = 0.0
    source_layer: str = ""  # "structural", "semantic", "graph"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResponse:
    """Unified search response with metadata for debugging."""
    query: str
    results: list[SearchResult]
    total_found: int
    layers_consulted: list[str]
    escalation_path: list[str]
    total_latency_ms: float
    healthy: bool = True


class SearchEngine:
    """Three-layer search with adaptive escalation.

    Usage:
        engine = SearchEngine(kg_path="~/.code-kg/graph")
        results = engine.search("JWT middleware authentication")
    """

    def __init__(self, kg_path: str = "") -> None:
        """Initialize the search engine.

        Args:
            kg_path: Path to KuzuDB graph database.
        """
        self._kg_path = kg_path
        self._kg = None

    def _get_kg(self):
        """Lazy-load KuzuDB connection."""
        if self._kg is None:
            from pathlib import Path
            from graph.kuzu_store import KnowledgeGraph, get_default_db_path
            path = self._kg_path or str(get_default_db_path())
            self._kg = KnowledgeGraph(path)
        return self._kg

    def search(
        self,
        query: str,
        node_type: str = "",
        max_results: int = 20,
    ) -> SearchResponse:
        """Execute a three-layer search with adaptive escalation.

        Args:
            query: Natural language or keyword query.
            node_type: Optional filter by node type (function, class, etc.).
            max_results: Maximum results to return.

        Returns:
            SearchResponse with results and diagnostics.
        """
        import time
        start = time.time()

        layers_consulted: list[str] = []
        escalation_path: list[str] = []
        all_results: list[SearchResult] = []

        # ─── Layer 1: Structural Search ───
        structural_results = self._search_structural(query, node_type)
        layers_consulted.append("structural")
        signal = self._diagnose(structural_results)

        if signal == "healthy":
            all_results = structural_results
        elif signal == "zero":
            escalation_path.append("structural:zero→semantic")
            semantic_results = self._search_semantic(query, node_type)
            layers_consulted.append("semantic")
            all_results = self._rbf_merge(structural_results, semantic_results)
        elif signal == "few":
            escalation_path.append("structural:few→semantic")
            semantic_results = self._search_semantic(query, node_type)
            layers_consulted.append("semantic")
            all_results = self._rbf_merge(structural_results, semantic_results)
        elif signal == "flat":
            escalation_path.append("structural:flat→semantic(more)")
            semantic_results = self._search_semantic(query, node_type, top_k=50)
            layers_consulted.append("semantic")
            all_results = self._rbf_merge(structural_results, semantic_results)

        # ─── Optional Layer 3: Graph expansion ───
        if len(all_results) < 5 and "graph" not in layers_consulted:
            escalation_path.append("sparse→graph_expansion")
            graph_results = self._search_graph(query, all_results[:3] if all_results else [])
            layers_consulted.append("graph")
            all_results = self._rbf_merge(all_results, graph_results)

        # ─── Finalize ───
        elapsed = (time.time() - start) * 1000
        all_results = all_results[:max_results]

        return SearchResponse(
            query=query,
            results=all_results,
            total_found=len(all_results),
            layers_consulted=layers_consulted,
            escalation_path=escalation_path,
            total_latency_ms=round(elapsed, 1),
            healthy=len(all_results) > 0,
        )

    # ─── Layer Implementations ───────────────────────────────────

    def _search_structural(
        self, query: str, node_type: str = ""
    ) -> list[SearchResult]:
        """Layer 1: Pattern-based search via KuzuDB CONTAINS (BM25-like)."""
        try:
            kg = self._get_kg()
            # Search by label AND file_path for filename queries like "model.py"
            rows = kg.query(
                "MATCH (n:Node) WHERE n.label CONTAINS $query OR n.file_path CONTAINS $query "
                "RETURN n.*, n.label AS label, n.id AS id LIMIT $limit",
                {"query": query, "limit": 200}
            )
            results = []
            seen = set()
            for r in rows:
                nid = r.get("n.id", "")
                if nid in seen: continue
                seen.add(nid)
                # Boost score for exact label match
                label = r.get("n.label", "")
                score = 0.9 if label.lower() == query.lower() else 0.7 if query.lower() in label.lower() else 0.5
                results.append(SearchResult(
                    node_id=nid,
                    label=label,
                    node_type=r.get("n.type", ""),
                    file_path=r.get("n.file_path", ""),
                    line_number=r.get("n.line_number", 0),
                    signature=r.get("n.signature", ""),
                    docstring=r.get("n.docstring", ""),
                    score=score,
                    source_layer="structural",
                ))
            # If node_type filter is set, apply it
            if node_type:
                results = [r for r in results if r.node_type == node_type]
            results.sort(key=lambda r: r.score, reverse=True)
            return results
        except Exception:
            return []

    def _search_semantic(
        self, query: str, node_type: str = "", top_k: int = 20
    ) -> list[SearchResult]:
        """Layer 2: Semantic search via vector similarity."""
        try:
            from search.embeddings import get_embedding_client
            client = get_embedding_client()
            query_vec = client.embed_text(query)
            
            kg = self._get_kg()
            # Vector search assumes we have vector_search method on KuzuStore
            # Let's ensure node_type filtering is supported or manually filter
            rows = kg.vector_search(query_vec, top_k=top_k * 2) # Get more to filter
            
            results = []
            for r in rows:
                if node_type and r.get("n.type", "") != node_type:
                    continue
                results.append(SearchResult(
                    node_id=r.get("n.id", ""),
                    label=r.get("n.label", ""),
                    node_type=r.get("n.type", ""),
                    file_path=r.get("n.file_path", ""),
                    line_number=r.get("n.line_number", 0),
                    signature=r.get("n.signature", ""),
                    docstring=r.get("n.docstring", ""),
                    score=r.get("score", 0.7),
                    source_layer="semantic",
                ))
            return results[:top_k]
        except Exception as e:
            logger.error(f"Semantic search error: {e}")
            # Fall back to structural if vectors fail
            return self._search_structural(query, node_type)

    def _search_graph(
        self, query: str, seed_results: list[SearchResult]
    ) -> list[SearchResult]:
        """Layer 3: Graph traversal from seed nodes."""
        results: list[SearchResult] = []
        try:
            kg = self._get_kg()
            seen_ids: set[str] = set()
            for seed in seed_results:
                if seed.node_id in seen_ids:
                    continue
                seen_ids.add(seed.node_id)
                neighbors = kg.traverse_neighbors(seed.node_id, hops=1)
                for n in neighbors:
                    nid = n.get("m.id", "")
                    if nid not in seen_ids:
                        seen_ids.add(nid)
                        results.append(SearchResult(
                            node_id=nid,
                            label=n.get("m.label", ""),
                            node_type=n.get("m.type", ""),
                            file_path=n.get("m.file_path", ""),
                            line_number=0,
                            signature=n.get("m.signature", ""),
                            docstring=n.get("m.docstring", ""),
                            score=0.5,
                            source_layer="graph",
                        ))
        except Exception:
            pass
        return results

    # ─── Fusion ──────────────────────────────────────────────────

    @staticmethod
    def _rbf_merge(
        list_a: list[SearchResult],
        list_b: list[SearchResult],
        k: int = 60,
    ) -> list[SearchResult]:
        """Reciprocal Rank Fusion — combines two ranked lists.

        score = Σ 1/(k + rank) for each list the item appears in.
        k=60 is the standard value; higher k reduces the impact
        of high-ranked items from a single list.
        """
        scores: dict[str, tuple[float, SearchResult]] = {}

        for rank, item in enumerate(list_a):
            rrf = 1.0 / (k + rank + 1)
            if item.node_id in scores:
                old_score, old_item = scores[item.node_id]
                scores[item.node_id] = (old_score + rrf, old_item)
            else:
                scores[item.node_id] = (rrf, item)

        for rank, item in enumerate(list_b):
            rrf = 1.0 / (k + rank + 1)
            if item.node_id in scores:
                old_score, old_item = scores[item.node_id]
                # Keep the item with better metadata
                merged = old_item if old_item.signature else item
                scores[item.node_id] = (old_score + rrf, merged)
            else:
                scores[item.node_id] = (rrf, item)

        # Sort by RRF score descending
        sorted_items = sorted(scores.values(), key=lambda x: x[0], reverse=True)
        results = []
        for score, item in sorted_items:
            item.score = round(score, 4)
            results.append(item)

        return results

    # ─── Diagnosis ───────────────────────────────────────────────

    @staticmethod
    def _diagnose(results: list[SearchResult]) -> str:
        """Mnemosyne-inspired retrieval diagnosis.

        Returns one of: healthy, zero, few, flat
        - healthy: Good results with clear score separation
        - zero: No results
        - few: Sparse results
        - flat: Top scores too close to distinguish
        """
        if not results:
            return "zero"
        if len(results) < 3:
            return "few"
        if len(results) >= 2:
            top2_ratio = results[0].score / max(results[1].score, 0.001)
            if top2_ratio < 1.2:  # Top-2 scores too close
                return "flat"
        return "healthy"


# Global singleton
_engine: SearchEngine | None = None


def get_search_engine(kg_path: str = "") -> SearchEngine:
    """Get or create the global search engine."""
    global _engine
    if _engine is None:
        _engine = SearchEngine(kg_path)
    return _engine


def reset_search_engine() -> None:
    """Reset the global search engine — call after new analysis.

    Forces the next search to reconnect to KuzuDB with fresh data.
    """
    global _engine
    if _engine is not None:
        _engine._kg = None
    _engine = None
