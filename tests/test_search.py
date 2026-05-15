import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from search.embeddings import EmbeddingClient


def test_embeddings_real_vectors():
    """Verify embeddings are real semantic vectors, not SHA256 hashes."""
    ec = EmbeddingClient()
    v1 = ec.embed_text("def authenticate_user: pass")
    v2 = ec.embed_text("authentication handler middleware")
    v3 = ec.embed_text("css color palette button styling")
    assert len(v1) == 384, f"wrong dim: {len(v1)}"
    non_zero = sum(1 for v in v1 if v != 0)
    assert non_zero > 10, f"practically zero vector: {non_zero} non-zero"
    dot12 = sum(a * b for a, b in zip(v1, v2))
    dot13 = sum(a * b for a, b in zip(v1, v3))
    assert dot12 > dot13, (
        f"semantic separation failed: auth-handler={dot12:.4f} auth-css={dot13:.4f}"
    )


def test_embeddings_deterministic():
    """Verify embeddings are deterministic for the same input."""
    ec = EmbeddingClient()
    v1 = ec.embed_text("hello world")
    v2 = ec.embed_text("hello world")
    assert v1 == v2, "embeddings not deterministic"


def test_empty_text():
    """Verify empty text returns zero vector."""
    ec = EmbeddingClient()
    v = ec.embed_text("")
    assert all(x == 0.0 for x in v), "empty text should return zero vector"


def test_embeddings_batch():
    """Verify batch embedding works."""
    ec = EmbeddingClient()
    results = ec.embed_batch(["hello", "world", "test"])
    assert len(results) == 3
    for r in results:
        assert len(r) == 384
