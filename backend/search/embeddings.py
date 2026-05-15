import os
from pathlib import Path
from typing import List

from log import get_logger; logger = get_logger(__name__)
_TFIDF = None

def _get_tfidf():
    global _TFIDF
    if _TFIDF is not None:
        return _TFIDF
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        _TFIDF = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 5),
            max_features=384,
            norm="l2",
            sublinear_tf=True,
        )
    except Exception as e:
        logger.error(f"TF-IDF init error: {e}")
        _TFIDF = None
    return _TFIDF


class EmbeddingClient:
    """Text embedding client.

    Produces real text embeddings via one of:
      1. OpenAI Embeddings API (if OPENAI_API_KEY is set)
      2. sklearn character n-gram TF-IDF (lightweight, no neural deps)
    """

    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
            if _env_path.exists():
                with open(_env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, _, v = line.partition("=")
                            if k.strip() == "OPENAI_API_KEY":
                                self.api_key = v.strip().strip('"').strip("'")
                                break
        self.dim = 1536 if self.api_key else 384
        self._fitted = False

    def _ensure_fitted(self, texts: list[str]):
        if self._fitted:
            return
        tfidf = _get_tfidf()
        if tfidf is None:
            return
        corpus = texts if texts else ["default seed corpus for initialization"]
        try:
            tfidf.fit(corpus)
            self._fitted = True
        except Exception as e:
            logger.error(f"TF-IDF fit error: {e}")

    def embed_text(self, text: str) -> List[float]:
        if not text.strip():
            return [0.0] * self.dim

        # ─── Primary: OpenAI API ───
        if self.api_key:
            try:
                import urllib.request
                import json
                req = urllib.request.Request(
                    "https://api.openai.com/v1/embeddings",
                    data=json.dumps({
                        "model": "text-embedding-3-small",
                        "input": text
                    }).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}"
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res = json.loads(resp.read().decode())
                    return res["data"][0]["embedding"]
            except Exception as e:
                logger.error(f"Embedding API error: {e}")

        # ─── Fallback: sklearn char n-gram TF-IDF ───
        self._ensure_fitted([text])
        tfidf = _get_tfidf()
        if tfidf is None or not self._fitted:
            return [0.0] * self.dim
        try:
            vec = tfidf.transform([text]).toarray()[0].tolist()
            norm = sum(x * x for x in vec) ** 0.5
            if norm > 1e-9:
                vec = [x / norm for x in vec]
            # Pad or truncate to self.dim
            if len(vec) < self.dim:
                vec = vec + [0.0] * (self.dim - len(vec))
            else:
                vec = vec[:self.dim]
            return vec
        except Exception as e:
            logger.error(f"TF-IDF transform error: {e}")
            return [0.0] * self.dim

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        # Fit on the whole batch for a richer vocabulary
        self._ensure_fitted(texts)
        return [self.embed_text(t) for t in texts]

_client = None

def get_embedding_client() -> EmbeddingClient:
    global _client
    if _client is None:
        _client = EmbeddingClient()
    return _client
