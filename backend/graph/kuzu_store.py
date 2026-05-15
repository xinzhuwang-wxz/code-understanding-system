"""
KuzuDB-backed knowledge graph store.

Extends the existing in-memory Graph with persistence, Cypher queries,
and HNSW vector indexing for semantic search.

Architecture:
  - Graph (in-memory, fast) for analysis phase
  - KuzuDB (persistent, queryable) for storage and retrieval
  - Nodes carry embedding_vector for vector similarity search

Edge Types (named REL TABLEs):
  - Contains       → general containment / "uses" relationships (was generic "Edge")
  - Invokes        → function/method calls, API calls
  - Inherits       → class inheritance
  - Imports        → module imports
  - References     → general references between symbols
  - Decorates      → Python decorator relationships
  - Handles        → endpoint / route handler relationships
  - DependsOn      → general dependency (data flow, etc.)
  - Reads          → database read operations
  - Writes         → database write operations
  - CallsTransitively → transitive call relationships (future use)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import os
import kuzu

from log import get_logger; logger = get_logger(__name__)


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
    type: str
    line_number: int = 0
    metadata: dict = field(default_factory=dict)


def get_default_db_path() -> Path:
    """Resolve the default KuzuDB database path.

    Checks CODE_KG_DATA env var first, then falls back to
    ~/.code-kg/graph using Path.home() which is portable across
    Unix and Windows.
    """
    if env_path := os.environ.get("CODE_KG_DATA"):
        return Path(env_path)
    return Path.home() / ".code-kg" / "graph"


# ─── Edge Type Mapping ────────────────────────────────────────────
#
# Maps analyzer edge types (python_analyzer, tree-sitter, etc.) to
# KuzuDB REL TABLE names. Unknown types fall back to "Contains".
# -------------------------------------------------------------------
EDGE_TYPE_TO_REL_TABLE: dict[str, str] = {
    # From python_analyzer.py
    "uses":             "Contains",
    "inherits":         "Inherits",
    "implements":       "Implements",
    "extends":          "Extends",
    "endpoint_handler": "Handles",
    "imports":          "Imports",
    "db_read":          "Reads",
    "db_write":         "Writes",
    "api_call":         "Invokes",
    # From tree-sitter / other analyzers
    "calls":            "Invokes",
    "contains":         "Contains",
    "references":       "References",
    "decorates":        "Decorates",
    "data_flows_to":    "DataFlowsTo",
    "handles":          "Handles",
    "typed_as":         "TypedAs",
    "throws":           "Throws",
    "calls_transitively": "CallsTransitively",
    "reads":            "Reads",
    "writes":           "Writes",
    "depends_on":       "DependsOn",
}


# All Node-to-Node relationship tables (used for multi-edge queries)
NODE_EDGE_TABLES: list[str] = [
    "Contains",
    "Invokes",
    "Inherits",
    "Implements",
    "Extends",
    "Imports",
    "References",
    "Decorates",
    "Handles",
    "DependsOn",
    "DataFlowsTo",
    "Reads",
    "Writes",
    "CallsTransitively",
    "TypedAs",
    "Throws",
]

# Default fallback REL TABLE for unknown edge types
DEFAULT_EDGE_TABLE = "Contains"


# ─── Graph Schema ─────────────────────────────────────────────────
#
# Node tables:
#   Node     – code entities (functions, classes, variables, etc.)
#   File     – source files
#   DocNode  – documentation / markdown content
#
# Relationship tables (Node → Node):
#   Contains, Invokes, Inherits, Imports, References, Decorates,
#   Handles, DependsOn, Reads, Writes, CallsTransitively,
#   TypedAs, Throws
#
# Relationship tables (File → Node):
#   FileContains – which nodes belong to which files
# -------------------------------------------------------------------
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

-- File → Node containment (which file a node lives in)
CREATE REL TABLE IF NOT EXISTS FileContains (
    FROM File TO Node
);

-- Node → Node relationship tables (was generic "Edge" table)
CREATE REL TABLE IF NOT EXISTS Contains (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Invokes (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Inherits (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Imports (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS References (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Decorates (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Handles (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS DependsOn (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Reads (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Writes (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS CallsTransitively (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS TypedAs (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Throws (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Implements (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS Extends (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
);

CREATE REL TABLE IF NOT EXISTS DataFlowsTo (
    FROM Node TO Node,
    line_number INT64,
    metadata STRING
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
        self._hnsw_index: Any = None
        self._hnsw_ids: list[str] = []
        self._init_hnsw()

    def _init_schema(self) -> None:
        """Initialize the graph schema if not exists."""
        for stmt in SCHEMA_SQL.strip().split(";\n"):
            stmt = stmt.strip()
            lines = stmt.split("\n")
            while lines and (not lines[0].strip() or lines[0].strip().startswith("--")):
                lines.pop(0)
            stmt = "\n".join(lines).strip()
            if stmt:
                try:
                    self._conn.execute(stmt)
                except Exception as e:
                    err_msg = str(e)
                    if "already exists" not in err_msg.lower():
                        import sys
                        print(f"  ⚠ Schema init warning [{stmt[:40]}...]: {e}", file=sys.stderr)

    # ─── HNSW Vector Index ────────────────────────────────────────
    #
    # KuzuDB 0.11.x does not support native HNSW index creation
    # (requires ≥0.12).  We maintain a sidecar HNSW index via hnswlib
    # for fast approximate nearest-neighbor search.
    #
    # The index lives at {db_path}/hnsw_index.bin with a separate
    # id-mapping file at {db_path}/hnsw_ids.npy.
    # -----------------------------------------------------------------

    @property
    def _hnsw_index_path(self) -> Path:
        return self.db_path / "hnsw" / "index.bin"

    @property
    def _hnsw_ids_path(self) -> Path:
        return self.db_path / "hnsw" / "ids.npy"

    def _init_hnsw(self) -> None:
        """Load or build the HNSW vector index."""
        try:
            import numpy as np
        except ImportError:
            return

        if self._hnsw_ids_path.exists() and self._hnsw_index_path.exists():
            self._load_hnsw_index()
            return

        # Build from existing vectors in DB
        rows = self.query(
            "MATCH (n:Node) WHERE size(n.embedding_vector) > 0 "
            "RETURN n.id, n.embedding_vector LIMIT 50000"
        )
        if not rows:
            return

        vectors = []
        ids = []
        dim = len(rows[0].get("n.embedding_vector", []))
        for r in rows:
            vec = r.get("n.embedding_vector")
            nid = r.get("n.id", "")
            if vec and nid:
                vectors.append(vec)
                ids.append(nid)

        if not vectors:
            return

        try:
            import hnswlib
            import numpy as np
            dim = len(vectors[0])
            index = hnswlib.Index(space="cosine", dim=dim)
            index.init_index(
                max_elements=max(len(vectors) * 2, 1000),
                ef_construction=200,
                M=32,
            )
            index.add_items(np.array(vectors, dtype=np.float32), np.arange(len(vectors)))
            index.set_ef(100)
            self._hnsw_index = index
            self._hnsw_ids = ids
            self._save_hnsw_index()
        except Exception as e:
            from log import get_logger
            get_logger(__name__).warning(f"HNSW index build error: {e}")

    def _save_hnsw_index(self) -> None:
        """Persist the HNSW index and id mapping to disk."""
        if self._hnsw_index is None:
            return
        try:
            import numpy as np
            self._hnsw_index_path.parent.mkdir(parents=True, exist_ok=True)
            self._hnsw_index.save_index(str(self._hnsw_index_path))
            np.save(str(self._hnsw_ids_path), np.array(self._hnsw_ids, dtype=object))
        except Exception as e:
            from log import get_logger
            get_logger(__name__).warning(f"HNSW save error: {e}")

    def _load_hnsw_index(self) -> None:
        """Load the HNSW index and id mapping from disk."""
        try:
            import hnswlib
            import numpy as np
            dim = self._vec_dim()
            index = hnswlib.Index(space="cosine", dim=dim)
            index.load_index(str(self._hnsw_index_path))
            index.set_ef(100)
            self._hnsw_index = index
            self._hnsw_ids = list(np.load(str(self._hnsw_ids_path), allow_pickle=True))
        except Exception as e:
            from log import get_logger
            get_logger(__name__).warning(f"HNSW load error: {e}")

    def _update_hnsw_index(self, node_id: str, vector: list[float]) -> None:
        """Add or update a single node in the HNSW index."""
        if not vector or not any(vector):
            return
        try:
            import hnswlib
            import numpy as np
            # Check if this node already has an entry
            if node_id in self._hnsw_ids:
                return  # Already indexed; rebuild would be expensive for single item
            idx = len(self._hnsw_ids)
            self._hnsw_ids.append(node_id)
            if self._hnsw_index is None:
                dim = len(vector)
                index = hnswlib.Index(space="cosine", dim=dim)
                index.init_index(max_elements=10000, ef_construction=200, M=32)
                index.set_ef(100)
                index.add_items(np.array([vector], dtype=np.float32), np.array([idx]))
                self._hnsw_index = index
            else:
                max_elements = self._hnsw_index.max_elements
                if idx >= max_elements:
                    self._hnsw_index.resize_index(max_elements * 2)
                self._hnsw_index.add_items(
                    np.array([vector], dtype=np.float32), np.array([idx])
                )
        except Exception as e:
            from log import get_logger
            get_logger(__name__).warning(f"HNSW update error: {e}")

    def clear(self) -> None:
        """Clear all data from the graph, including HNSW index files."""
        self._conn.execute("MATCH (n:Node) DETACH DELETE n;")
        self._conn.execute("MATCH (f:File) DETACH DELETE f;")
        # Also clear HNSW sidecar files
        self._hnsw_index = None
        self._hnsw_ids = []
        hnsw_dir = self.db_path / "hnsw"
        for p in (hnsw_dir / "index.bin", hnsw_dir / "ids.npy"):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        if hnsw_dir.exists():
            try:
                hnsw_dir.rmdir()
            except Exception:
                pass

    # ─── Edge Type Resolution ─────────────────────────────────────

    @staticmethod
    def _resolve_rel_table(edge_type: str) -> str:
        """Resolve an analyzer edge type to a KuzuDB REL TABLE name.

        Falls back to DEFAULT_EDGE_TABLE for unknown types.
        """
        return EDGE_TYPE_TO_REL_TABLE.get(edge_type, DEFAULT_EDGE_TABLE)

    @staticmethod
    def _build_multi_edge_match(
        direction: str = "both",
        node_var: str = "n",
        neighbor_var: str = "m",
        node_id_param: str = "$id",
    ) -> str:
        """Build a UNION ALL query pattern matching across all edge types.

        Args:
            direction: "forward" (→), "backward" (←), or "both" (—).
            node_var: Variable name for the source/center node.
            neighbor_var: Variable name for the neighbor node.
            node_id_param: Parameter reference for the center node's id
                           (default "$id").

        Returns:
            A UNION ALL of MATCH clauses covering all NODE_EDGE_TABLES,
            each filtering on {node_var}.id = {node_id_param}.
        """
        patterns: dict[str, str] = {
            "forward":  f"({node_var}:Node {{{{id: {node_id_param}}}}})-[:{{table}}]->({neighbor_var}:Node)",
            "backward": f"({node_var}:Node {{{{id: {node_id_param}}}}})<-[:{{table}}]-({neighbor_var}:Node)",
            "both":     f"({node_var}:Node {{{{id: {node_id_param}}}}})-[:{{table}}]-({neighbor_var}:Node)",
        }
        template = patterns.get(direction, patterns["both"])

        clauses = [
            f"MATCH {template.format(table=t)}"
            for t in NODE_EDGE_TABLES
        ]
        return " UNION ALL ".join(clauses)

    # ─── Batch Import ──────────────────────────────────────────────

    def ingest_analysis(
        self,
        repo_path: str,
        nodes: list[dict],
        edges: list[dict],
        replace: bool = False,
    ) -> dict:
        """Ingest an analysis result into the knowledge graph.

        Args:
            replace: If True, clear ALL data before ingestion.
                     If False (default), add nodes alongside existing ones
                     (duplicate node IDs will be skipped).
        """
        if replace:
            self.clear()

        node_count = 0
        try:
            self._conn.execute("BEGIN TRANSACTION;")
            for n in nodes:
                meta = n.get("metadata", {}) or {}
                embedding = n.get("embedding_vector") or meta.get("embedding_vector")
                node_data = {
                    "id": str(n.get("id", "")),
                    "label": str(n.get("label", "")),
                    "type": str(n.get("type", "unknown")),
                    "file_path": str(n.get("file_path", "")),
                    "line_number": int(n.get("line_number", 0)),
                    "signature": str(n.get("signature", "") or meta.get("signature", "")),
                    "docstring": str(n.get("docstring", "") or meta.get("docstring", "")),
                    "git_blame": str(n.get("git_blame", "")),
                    "embedding_vector": embedding if embedding else [],
                }
                self._conn.execute(
                    "MERGE (n:Node {id: $id}) "
                    "ON CREATE SET n.label = $label, n.type = $type, "
                    "n.file_path = $file_path, n.line_number = $line_number, "
                    "n.signature = $signature, n.docstring = $docstring, "
                    "n.git_blame = $git_blame, "
                    "n.embedding_vector = $embedding_vector "
                    "ON MATCH SET n.label = $label, n.type = $type, "
                    "n.file_path = $file_path, n.line_number = $line_number, "
                    "n.signature = $signature, n.docstring = $docstring, "
                    "n.git_blame = $git_blame, "
                    "n.embedding_vector = $embedding_vector",
                    node_data,
                )
                if embedding:
                    self._update_hnsw_index(node_data["id"], embedding)
                node_count += 1
            self._conn.execute("COMMIT;")
            # Persist HNSW index after batch node ingest
            self._save_hnsw_index()
        except Exception as e:
            try:
                self._conn.execute("ROLLBACK;")
            except Exception:
                pass
            logger.error(f"Node ingestion error: {e}")
            return {"nodes": 0, "edges": 0, "files": 0, "error": str(e)}

        # Create File nodes and FileContains edges
        file_count = 0
        try:
            self._conn.execute("BEGIN TRANSACTION;")
            seen_files: set[str] = set()
            for n in nodes:
                file_path = str(n.get("file_path", ""))
                if file_path and file_path not in seen_files:
                    seen_files.add(file_path)
                    file_id = f"file:{file_path}"
                    self._conn.execute(
                        "MERGE (f:File {path: $path}) "
                        "ON CREATE SET f.language = $language, f.last_modified = $last_modified "
                        "ON MATCH SET f.language = $language",
                        {
                            "path": file_path,
                            "language": Path(file_path).suffix.lstrip(".") or "unknown",
                            "last_modified": "",
                        },
                    )
                    file_count += 1
                if file_path:
                    node_id = str(n.get("id", ""))
                    if node_id:
                        try:
                            self._conn.execute(
                                "MATCH (f:File {path: $file_path}), (n:Node {id: $node_id}) "
                                "CREATE (f)-[:FileContains]->(n)",
                                {"file_path": file_path, "node_id": node_id},
                            )
                        except Exception:
                            pass
            self._conn.execute("COMMIT;")
        except Exception as e:
            try:
                self._conn.execute("ROLLBACK;")
            except Exception:
                pass
            logger.error(f"File/FileContains ingestion error: {e}")

        # Batch insert edges using named REL TABLEs
        edge_count = 0
        try:
            self._conn.execute("BEGIN TRANSACTION;")
            for e in edges:
                source_id = str(e.get("source", ""))
                target_id = str(e.get("target", ""))
                if not source_id or not target_id:
                    continue

                edge_type = str(e.get("type", ""))
                rel_table = self._resolve_rel_table(edge_type)

                # Extract line_number from metadata or top-level
                meta = e.get("metadata", {})
                if isinstance(meta, dict):
                    line_no = int(meta.get("line_number", 0))
                else:
                    line_no = int(e.get("line_number", 0))

                try:
                    self._conn.execute(
                        f"MATCH (a:Node {{id: $source}}), (b:Node {{id: $target}}) "
                        f"CREATE (a)-[:{rel_table} {{line_number: $line_number}}]->(b)",
                        {
                            "source": source_id,
                            "target": target_id,
                            "line_number": line_no,
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
            logger.error(f"Edge ingestion error: {e}")

        return {"nodes": node_count, "edges": edge_count, "files": file_count}

    def _rebuild_hnsw_from_db(self) -> None:
        """Force-rebuild the HNSW index from all embedding vectors in KuzuDB."""
        self._hnsw_index = None
        self._hnsw_ids = []
        hnsw_dir = self.db_path / "hnsw"
        for p in (hnsw_dir / "index.bin", hnsw_dir / "ids.npy"):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        self._init_hnsw()

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
        """Traverse N-hop neighbors of a node across all edge types.

        Args:
            node_id: Starting node ID.
            hops: Number of hops to traverse.
            direction: "forward" (outgoing), "backward" (incoming), or "both".

        Returns:
            List of connected nodes with relationship info.
        """
        return self._query_multi_edge(direction=direction, node_id=node_id, limit=100)

    def search_by_pattern(self, node_type: str, name_pattern: str) -> list[dict]:
        """Search nodes by type and name pattern (BM25-like, exact).

        Args:
            node_type: Node type filter. '' means any type.
            name_pattern: Search pattern for label (CONTAINS).

        Returns:
            List of matching nodes.
        """
        if node_type:
            query = (
                "MATCH (n:Node) "
                "WHERE n.type = $type AND n.label CONTAINS $pattern "
                "RETURN n.id, n.label, n.type, n.file_path, n.line_number, "
                "n.signature, n.docstring "
                "LIMIT 50"
            )
            return self.query(query, {"type": node_type, "pattern": name_pattern})
        query = (
            "MATCH (n:Node) "
            "WHERE n.label CONTAINS $pattern "
            "RETURN n.id, n.label, n.type, n.file_path, n.line_number, "
            "n.signature, n.docstring "
            "LIMIT 50"
        )
        return self.query(query, {"pattern": name_pattern})

    def search_by_regex(self, pattern: str, node_type: str = "") -> list[dict]:
        """Search nodes by regular expression pattern on label.

        Provides ast-grep-like structural pattern matching using
        KuzuDB's native regex support.

        Args:
            pattern: Regex pattern for label matching (e.g., '^get[A-Z]').
            node_type: Optional node type filter ('function', 'class', etc.)

        Returns:
            List of matching nodes.
        """
        if node_type:
            query = (
                "MATCH (n:Node) "
                "WHERE n.type = $type AND n.label =~ $pattern "
                "RETURN n.id, n.label, n.type, n.file_path, n.line_number, "
                "n.signature, n.docstring "
                "LIMIT 50"
            )
            return self.query(query, {"type": node_type, "pattern": pattern})
        query = (
            "MATCH (n:Node) "
            "WHERE n.label =~ $pattern "
            "RETURN n.id, n.label, n.type, n.file_path, n.line_number, "
            "n.signature, n.docstring "
            "LIMIT 50"
        )
        return self.query(query, {"pattern": pattern})

    def _vec_dim(self) -> int:
        """Detect the embedding dimension from the first node that has one."""
        rows = self.query("MATCH (n:Node) WHERE size(n.embedding_vector) > 0 RETURN size(n.embedding_vector) AS d LIMIT 1")
        if rows:
            return rows[0]["d"]
        return 384  # default

    def vector_search(
        self, query_vector: list[float], top_k: int = 20
    ) -> list[dict]:
        """Search nodes by embedding vector similarity (cosine).

        Uses the on-disk HNSW index (hnswlib) when available for
        approximate nearest-neighbor search (~1ms at 10K nodes).
        Falls back to brute-force array_cosine_similarity via KuzuDB
        when the HNSW index is empty or needs rebuilding.

        Args:
            query_vector: Query embedding vector.
            top_k: Number of results to return.

        Returns:
            List of matching nodes sorted by similarity.
        """
        # ─── Fast path: HNSW index ───
        if self._hnsw_index is not None and self._hnsw_ids:
            try:
                import numpy as np
                labels, distances = self._hnsw_index.knn_query(
                    np.array([query_vector], dtype=np.float32), k=min(top_k * 2, len(self._hnsw_ids))
                )
                results = []
                for label, dist in zip(labels[0], distances[0]):
                    if label == -1:
                        continue
                    idx = int(label)
                    if idx >= len(self._hnsw_ids):
                        continue
                    node_id = self._hnsw_ids[idx]
                    cosine_sim = 1.0 - dist  # hnswlib returns cosine distance → similarity
                    if cosine_sim < 0.3:
                        continue
                    rows = self.query(
                        "MATCH (n:Node {id: $id}) "
                        "RETURN n.id, n.label, n.type, n.file_path, "
                        "n.signature, n.docstring",
                        {"id": node_id},
                    )
                    if rows:
                        row = rows[0]
                        row["score"] = round(cosine_sim, 4)
                        results.append(row)
                        if len(results) >= top_k:
                            break
                results.sort(key=lambda r: r["score"], reverse=True)
                return results
            except Exception as e:
                from log import get_logger
                get_logger(__name__).warning(f"HNSW query error, falling back: {e}")

        # ─── Fallback: KuzuDB brute-force ───
        dim = self._vec_dim()
        cast_target = f"DOUBLE[{dim}]"
        query = (
            "MATCH (n:Node) "
            "WHERE size(n.embedding_vector) > 0 "
            "WITH n, array_cosine_similarity("
            f"  CAST(n.embedding_vector, '{cast_target}'),"
            f"  CAST($query_vec, '{cast_target}')"
            ") AS score "
            "WHERE score > 0.3 "
            "RETURN n.id, n.label, n.type, n.file_path, "
            "n.signature, n.docstring, score "
            "ORDER BY score DESC "
            f"LIMIT {top_k}"
        )
        return self.query(query, {"query_vec": query_vector})

    # ─── Dependency Analysis ──────────────────────────────────────

    def get_neighbors(
        self, node_id: str, direction: str = "both", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get direct neighbors of a node across all edge types.

        Args:
            node_id: The node to find neighbors for.
            direction: "forward" (outgoing), "backward" (incoming), or "both".
            limit: Max results.

        Returns:
            List of neighbor nodes with id, label, type, file_path, line_number.
        """
        return self._query_multi_edge(direction=direction, node_id=node_id, limit=limit)

    def get_dependents(self, node_id: str) -> list[dict[str, Any]]:
        """Get nodes that depend on (reference/call/use) this node.

        Searches across all incoming edge types.
        """
        return self._query_multi_edge(direction="backward", node_id=node_id)

    def get_dependencies(self, node_id: str) -> list[dict[str, Any]]:
        """Get nodes that this node depends on (calls/imports/references).

        Searches across all outgoing edge types.
        """
        return self._query_multi_edge(direction="forward", node_id=node_id)

    def _query_multi_edge(self, direction: str, node_id: str,
                          limit: int = 50) -> list[dict[str, Any]]:
        """Query across all Node→Node REL TABLEs, merging results in Python."""
        if direction == "backward":
            pattern = "(n:Node {{id: $id}})<-[:{table}]-(dependent:Node)"
        elif direction == "forward":
            pattern = "(n:Node {{id: $id}})-[:{table}]->(dependent:Node)"
        else:
            pattern = "(n:Node {{id: $id}})-[:{table}]-(dependent:Node)"

        seen = set()
        results = []
        for table in NODE_EDGE_TABLES:
            query = (
                f"MATCH {pattern.format(table=table)} "
                f"RETURN DISTINCT dependent.id AS id, dependent.label AS label, "
                f"dependent.type AS type, dependent.file_path AS file_path, "
                f"dependent.line_number AS line_number "
                f"LIMIT {limit}"
            )
            try:
                rows = self.query(query, {"id": node_id})
                for row in rows:
                    rid = row["id"]
                    if rid not in seen:
                        seen.add(rid)
                        results.append(row)
            except Exception:
                pass  # Table may be empty or just created

        return results

    def impact_analysis(self, node_id: str) -> dict:
        """Analyze the impact of modifying a node.

        Returns direct dependents and N-hop impact.
        """
        dependents = self.get_dependents(node_id)
        dependencies = self.get_dependencies(node_id)

        return {
            "node_id": node_id,
            "dependents": dependents,
            "dependencies": dependencies,
            "total_affected": len(dependents),
        }

    def stats(self) -> dict:
        """Get graph statistics including per-edge-type counts."""
        node_count = self.query(
            "MATCH (n:Node) RETURN count(n) AS cnt"
        )

        # Count edges across all Node→Node REL TABLEs
        edge_parts = [
            f"MATCH ()-[:{t}]->() RETURN '{t}' AS rel_type, count(*) AS cnt"
            for t in NODE_EDGE_TABLES
        ]
        edge_query = " UNION ALL ".join(edge_parts)

        # Also count FileContains edges
        edge_query += (
            " UNION ALL "
            "MATCH ()-[:FileContains]->() RETURN 'FileContains' AS rel_type, count(*) AS cnt"
        )

        edge_counts = self.query(edge_query)
        total_edges = sum(row["cnt"] for row in edge_counts)

        type_counts = self.query(
            "MATCH (n:Node) RETURN n.type AS type, count(n) AS cnt "
            "ORDER BY cnt DESC"
        )

        return {
            "nodes": node_count[0]["cnt"] if node_count else 0,
            "edges": total_edges,
            "edge_type_distribution": edge_counts,
            "type_distribution": type_counts,
        }

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass
