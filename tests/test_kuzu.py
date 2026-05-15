import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from graph.kuzu_store import KnowledgeGraph
from search.embeddings import EmbeddingClient


def _make_db():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_graph")
    kg = KnowledgeGraph(db_path)
    return kg, tmpdir


SAMPLE_NODES = [
    {"id": "auth.py:authenticate_user:10", "label": "authenticate_user",
     "type": "function", "file_path": "auth.py", "line_number": 10,
     "signature": "def authenticate_user():", "docstring": "Authenticates a user"},
    {"id": "auth.py:validate_token:25", "label": "validate_token",
     "type": "function", "file_path": "auth.py", "line_number": 25,
     "signature": "def validate_token():", "docstring": "Validates JWT token"},
    {"id": "ui.py:render_button:5", "label": "render_button",
     "type": "function", "file_path": "ui.py", "line_number": 5,
     "signature": "def render_button():", "docstring": "Renders a button"},
    {"id": "auth.py:AuthHandler:1", "label": "AuthHandler",
     "type": "class", "file_path": "auth.py", "line_number": 1,
     "signature": "class AuthHandler:", "docstring": "Handles authentication"},
]

SAMPLE_EDGES = [
    {"source": "auth.py:authenticate_user:10", "target": "auth.py:AuthHandler:1",
     "type": "Contains"},
    {"source": "auth.py:authenticate_user:10", "target": "auth.py:validate_token:25",
     "type": "Invokes"},
]


def test_ingest_and_search():
    kg, tmpdir = _make_db()
    try:
        result = kg.ingest_analysis("test_repo", SAMPLE_NODES, SAMPLE_EDGES)
        assert result["nodes"] == 4
        assert result["edges"] == 2

        # Pattern search
        rows = kg.search_by_pattern("function", "auth")
        assert len(rows) >= 1
        assert any("authenticate_user" in str(r) for r in rows)

        # Wildcard search — should find by label match
        rows_all = kg.search_by_pattern("", "Auth")
        assert len(rows_all) >= 1
        rows_fn = kg.search_by_pattern("", "authenticate")
        assert len(rows_fn) >= 1
    finally:
        kg.close()
        shutil.rmtree(tmpdir)


def test_regex_search():
    kg, tmpdir = _make_db()
    try:
        kg.ingest_analysis("test_repo", SAMPLE_NODES, SAMPLE_EDGES)
        rows = kg.search_by_regex("validate.*", "function")
        assert len(rows) >= 1
        assert any("validate_token" in str(r) for r in rows)
    finally:
        kg.close()
        shutil.rmtree(tmpdir)


def test_vector_search():
    kg, tmpdir = _make_db()
    try:
        kg.ingest_analysis("test_repo", SAMPLE_NODES, SAMPLE_EDGES)
        ec = EmbeddingClient()
        for n in SAMPLE_NODES:
            vec = ec.embed_text(n["label"] + " " + n.get("docstring", ""))
            kg._conn.execute(
                "MATCH (n:Node {id: $id}) SET n.embedding_vector = $vec",
                {"id": n["id"], "vec": vec},
            )
        query_vec = ec.embed_text("authentication")
        results = kg.vector_search(query_vec, top_k=5)
        assert len(results) >= 2
        assert results[0]["score"] > 0.5
    finally:
        kg.close()
        shutil.rmtree(tmpdir)


def test_traverse_neighbors():
    kg, tmpdir = _make_db()
    try:
        kg.ingest_analysis("test_repo", SAMPLE_NODES, SAMPLE_EDGES)
        neighbors = kg.traverse_neighbors("auth.py:authenticate_user:10", hops=2)
        assert len(neighbors) >= 2
    finally:
        kg.close()
        shutil.rmtree(tmpdir)


def test_impact_analysis():
    kg, tmpdir = _make_db()
    try:
        kg.ingest_analysis("test_repo", SAMPLE_NODES, SAMPLE_EDGES)
        impact = kg.impact_analysis("auth.py:authenticate_user:10")
        assert len(impact.get("dependencies", [])) >= 2
        assert isinstance(impact.get("total_affected", 0), int)
    finally:
        kg.close()
        shutil.rmtree(tmpdir)


def test_stats():
    kg, tmpdir = _make_db()
    try:
        kg.ingest_analysis("test_repo", SAMPLE_NODES, SAMPLE_EDGES)
        stats = kg.stats()
        assert stats["nodes"] >= 4
        assert stats["edges"] >= 2
    finally:
        kg.close()
        shutil.rmtree(tmpdir)
