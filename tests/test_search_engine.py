import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from graph.kuzu_store import KnowledgeGraph
from search.embeddings import EmbeddingClient
from search.engine import SearchEngine, SearchResult


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
]

SAMPLE_EDGES = [
    {"source": "auth.py:authenticate_user:10", "target": "auth.py:validate_token:25",
     "type": "Invokes"},
]


def _make_db():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_graph")
    kg = KnowledgeGraph(db_path)
    kg.ingest_analysis("test_repo", SAMPLE_NODES, SAMPLE_EDGES)
    ec = EmbeddingClient()
    for n in SAMPLE_NODES:
        vec = ec.embed_text(n["label"] + " " + n.get("docstring", ""))
        kg._conn.execute(
            "MATCH (n:Node {id: $id}) SET n.embedding_vector = $vec",
            {"id": n["id"], "vec": vec},
        )
    kg.close()
    return db_path, tmpdir


def test_search_engine_layers():
    db_path, tmpdir = _make_db()
    try:
        engine = SearchEngine(db_path)
        response = engine.search("authentication", node_type="function")
        assert len(response.results) > 0
        assert "structural" in response.layers_consulted
        assert response.query == "authentication"
        assert response.total_found >= 1
    finally:
        shutil.rmtree(tmpdir)


def test_search_engine_escalation():
    """With rich data, structural alone should be 'healthy', no escalation."""
    db_path, tmpdir = _make_db()
    try:
        engine = SearchEngine(db_path)
        response = engine.search("authenticate_user", node_type="function")
        assert response.total_found >= 1
    finally:
        shutil.rmtree(tmpdir)


def test_rbf_merge():
    a = [
        SearchResult(node_id="1", label="a", node_type="f", file_path="", line_number=0, score=0.9, source_layer="structural"),
        SearchResult(node_id="2", label="b", node_type="f", file_path="", line_number=0, score=0.8, source_layer="structural"),
    ]
    b = [
        SearchResult(node_id="2", label="b", node_type="f", file_path="", line_number=0, score=0.7, source_layer="semantic"),
        SearchResult(node_id="3", label="c", node_type="f", file_path="", line_number=0, score=0.6, source_layer="semantic"),
    ]
    merged = SearchEngine._rbf_merge(a, b, k=60)
    assert len(merged) == 3
    assert merged[0].node_id == "2"  # appears in both
    assert merged[0].score > 0.01  # RRF merged from both lists


def test_diagnose():
    assert SearchEngine._diagnose([]) == "zero"
    assert SearchEngine._diagnose([
        SearchResult(node_id="1", label="a", node_type="f", file_path="", line_number=0, score=0.9, source_layer=""),
    ]) == "few"
    assert SearchEngine._diagnose([
        SearchResult(node_id="1", label="a", node_type="f", file_path="", line_number=0, score=0.9, source_layer=""),
        SearchResult(node_id="2", label="b", node_type="f", file_path="", line_number=0, score=0.5, source_layer=""),
        SearchResult(node_id="3", label="c", node_type="f", file_path="", line_number=0, score=0.3, source_layer=""),
    ]) == "healthy"
