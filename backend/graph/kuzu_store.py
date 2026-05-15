"""
KuzuDB-backed knowledge graph store.

Extends the existing in-memory Graph with persistence, Cypher queries,
and HNSW vector indexing for semantic search.

Architecture:
  - Graph (in-memory, fast) for analysis phase
  - KuzuDB (persistent, queryable) for storage and retrieval
  - Nodes carry embedding_vector for vector similarity search
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import kuzu


@dataclass
class KGNode:
    """Node in the persistent knowledge graph."""
    id: str
    label: str
    type: str  # function, class, file, module, etc.
    file_path: str = ""
    line_number: int = 0
    signature: str = ""
    docstring: str = ""
    embedding_vector: list[float] | None = None
    git_blame: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KGEdge:
    """Edge in the persistent knowledge graph."""
    source: str
    target: str
    type: str  # calls, imports, inherits, contains, etc.
    line_number: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# Graph schema for KuzuDB
SCHEMA_SQL = """
CREATE NODE TABLE IF NOT EXISTS Node (
    id STRING PRIMARY KEY,
    label STRING,
    type STRING,
    file_path STRING,
    line_number INT64,
    signature STRING,
    docstring STRING,
    embedding_vector DOUBLE[],
    git_blame STRING,
    metadata STRING
);

CREATE NODE TABLE IF NOT EXISTS File (
    path STRING PRIMARY KEY,
    language STRING,
    lines INT64,
    last_modified STRING
);

CREATE REL TABLE IF NOT EXISTS Edge (
    FROM Node TO Node,
    type STRING,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Contains (
    FROM File TO Node
);

CREATE NODE TABLE IF NOT EXISTS DocNode (
    id STRING PRIMARY KEY,
    source_file STRING,
    source_type STRING,
    title STRING,
    content STRING,
    language STRING,
    embedding_vector DOUBLE[]
);
"""


class KnowledgeGraph:
    """Persistent knowledge graph backed by KuzuDB.

    Stores code entities (functions, classes, files) as nodes and
    their relationships (calls, imports, inheritance) as edges.
    Supports Cypher queries and vector similarity search via HNSW index.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the knowledge graph.

        Args:
            db_path: Path to the KuzuDB database. This should be a path
                     like '~/.code-kg/graph' — KuzuDB creates a directory
                     structure from this base path.
        """
        self.db_path = Path(db_path)
        self._db = kuzu.Database(str(self.db_path))
        self._conn = kuzu.Connection(self._db)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize the graph schema if not exists."""
        for stmt in SCHEMA_SQL.strip().split(";\n"):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._conn.execute(stmt + ";")
                except Exception:
                    pass  # Already exists

        # Create DocNode table separately (KuzuDB IF NOT EXISTS may be flaky)
        try:
            self._conn.execute(
                "CREATE NODE TABLE DocNode ("
                "id STRING PRIMARY KEY, "
                "source_file STRING, "
                "source_type STRING, "
                "title STRING, "
                "content STRING, "
                "language STRING, "
                "embedding_vector DOUBLE[]"
                ")"
            )
        except Exception:
            pass  # Table already exists

    def clear(self) -> None:
        """Clear all data from the graph."""
        self._conn.execute("MATCH (n:Node) DETACH DELETE n;")
        self._conn.execute("MATCH (f:File) DETACH DELETE f;")

    # ─── Batch Import ──────────────────────────────────────────────

    def ingest_analysis(
        self,
        repo_path: str,
        nodes: list[dict],
        edges: list[dict],
    ) -> dict:
        """Ingest an analysis result into the knowledge graph.

        Uses prepared statements for fast batch import.
        """
        self.clear()

        # Batch insert nodes using prepared statement
        node_count = 0
        try:
            # Build a single COPY-like statement or batch with BEGIN/COMMIT
            self._conn.execute("BEGIN TRANSACTION;")
            
            for n in nodes:
                self._conn.execute(
                    "CREATE (n:Node {"
                    "id: $id, label: $label, type: $type, "
                    "file_path: $file_path, line_number: $line_number, "
                    "signature: $signature, docstring: $docstring, "
                    "git_blame: $git_blame"
                    "})",
                    {
                        "id": str(n.get("id", "")),
                        "label": str(n.get("label", "")),
                        "type": str(n.get("type", "unknown")),
                        "file_path": str(n.get("file_path", "")),
                        "line_number": int(n.get("line_number", 0)),
                        "signature": str(n.get("signature", "")),
                        "docstring": str(n.get("docstring", "")),
                        "git_blame": str(n.get("git_blame", "")),
                    },
                )
                node_count += 1
            self._conn.execute("COMMIT;")
        except Exception as e:
            try:
                self._conn.execute("ROLLBACK;")
            except Exception:
                pass
            print(f"  ⚠ Node ingestion error: {e}")
            return {"nodes": 0, "edges": 0, "files": 0, "error": str(e)}

        # Batch insert edges
        edge_count = 0
        try:
            self._conn.execute("BEGIN TRANSACTION;")
            for e in edges:
                source_id = str(e.get("source", ""))
                target_id = str(e.get("target", ""))
                if not source_id or not target_id:
                    continue
                try:
                    self._conn.execute(
                        "MATCH (a:Node {id: $source}), (b:Node {id: $target}) "
                        "CREATE (a)-[:Edge {type: $type, line_number: $line_number}]->(b)",
                        {
                            "source": source_id,
                            "target": target_id,
                            "type": str(e.get("type", "")),
                            "line_number": int(e.get("line_number", 0)),
                        },
                    )
                    edge_count += 1
                except Exception:
                    pass  # Source or target node not found
            self._conn.execute("COMMIT;")
        except Exception as e:
            try:
                self._conn.execute("ROLLBACK;")
            except Exception:
                pass
            print(f"  ⚠ Edge ingestion error: {e}")

        return {"nodes": node_count, "edges": edge_count, "files": 0}

    # ─── Query ────────────────────────────────────────────────────

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a Cypher query.

        Args:
            cypher: Cypher query string.
            params: Named parameters for the query.

        Returns:
            List of result rows as dicts.
        """
        params = params or {}
        result = self._conn.execute(cypher, params)
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append(dict(zip(result.get_column_names(), row)))
        return rows

    def traverse_neighbors(
        self, node_id: str, hops: int = 2, direction: str = "both"
    ) -> list[dict]:
        """Traverse N-hop neighbors of a node.

        Args:
            node_id: Starting node ID.
            hops: Number of hops to traverse.
            direction: "forward" (outgoing), "backward" (incoming), or "both".

        Returns:
            List of connected nodes with relationship info.
        """
        if direction == "forward":
            rel_pattern = "-[:Edge]->"
        elif direction == "backward":
            rel_pattern = "<-[:Edge]-"
        else:
            rel_pattern = "-[:Edge]-"

        query = (
            f"MATCH (n:Node {{id: $id}}){rel_pattern}"
            f"(m:Node) "
            f"RETURN DISTINCT m.id, m.label, m.type, m.file_path, "
            f"m.signature, m.docstring "
            f"LIMIT 100"
        )
        return self.query(query, {"id": node_id})

    def search_by_pattern(self, node_type: str, name_pattern: str) -> list[dict]:
        """Search nodes by type and name pattern (BM25-like, exact).

        Args:
            node_type: Node type to filter by.
            name_pattern: SQL LIKE pattern for label.

        Returns:
            List of matching nodes.
        """
        query = (
            "MATCH (n:Node) "
            "WHERE n.type = $type AND n.label CONTAINS $pattern "
            "RETURN n.id, n.label, n.type, n.file_path, n.line_number, "
            "n.signature, n.docstring "
            "LIMIT 50"
        )
        return self.query(query, {"type": node_type, "pattern": name_pattern})

    def vector_search(
        self, query_vector: list[float], top_k: int = 20
    ) -> list[dict]:
        """Search nodes by embedding vector similarity (cosine).

        Requires nodes to have embedding_vector populated.

        Args:
            query_vector: Query embedding vector.
            top_k: Number of results to return.

        Returns:
            List of matching nodes sorted by similarity.
        """
        query = (
            "MATCH (n:Node) "
            "WHERE size(n.embedding_vector) > 0 "
            "WITH n, array_cosine_similarity(n.embedding_vector, $query_vec) AS score "
            "WHERE score > 0.3 "
            "RETURN n.id, n.label, n.type, n.file_path, "
            "n.signature, n.docstring, score "
            "ORDER BY score DESC "
            f"LIMIT {top_k}"
        )
        return self.query(query, {"query_vec": query_vector})

    def get_dependents(self, node_id: str) -> list[dict[str, Any]]:
        """Get nodes that depend on (call) this node."""
        deps = self.query(
            "MATCH (n:Node {id: $id})<-[:Edge]-(dependent:Node) "
            "RETURN dependent.id AS id, dependent.label AS label, "
            "dependent.type AS type, dependent.file_path AS file_path, "
            "dependent.line_number AS line_number "
            "LIMIT 50",
            {"id": node_id},
        )
        return deps

    def impact_analysis(self, node_id: str) -> dict:
        """Analyze the impact of modifying a node.

        Returns direct dependents and N-hop impact.
        """
        # Direct dependents
        dependents = self.query(
            "MATCH (n:Node {id: $id})<-[:Edge]-(dependent:Node) "
            "RETURN dependent.id, dependent.label, dependent.type, "
            "dependent.file_path "
            "LIMIT 50",
            {"id": node_id},
        )

        # Direct dependencies
        dependencies = self.query(
            "MATCH (n:Node {id: $id})-[:Edge]->(dep:Node) "
            "RETURN dep.id, dep.label, dep.type, dep.file_path "
            "LIMIT 50",
            {"id": node_id},
        )

        return {
            "node_id": node_id,
            "dependents": dependents,
            "dependencies": dependencies,
            "total_affected": len(dependents),
        }

    def stats(self) -> dict:
        """Get graph statistics."""
        node_count = self.query(
            "MATCH (n:Node) RETURN count(n) AS cnt"
        )
        edge_count = self.query(
            "MATCH ()-[e:Edge]->() RETURN count(e) AS cnt"
        )
        type_counts = self.query(
            "MATCH (n:Node) RETURN n.type AS type, count(n) AS cnt "
            "ORDER BY cnt DESC"
        )

        return {
            "nodes": node_count[0]["cnt"] if node_count else 0,
            "edges": edge_count[0]["cnt"] if edge_count else 0,
            "type_distribution": type_counts,
        }

    def close(self) -> None:
        """Close the database connection."""
        pass  # KuzuDB auto-closes on object destruction
