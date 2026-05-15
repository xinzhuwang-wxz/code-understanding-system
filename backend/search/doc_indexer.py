"""
Document joint indexer — unify code comments + Markdown docs + API docs
into a searchable index backed by KuzuDB with embedding vectors.

Three document types:
  1. Source comments  — extracted from tree-sitter (already in KuzuDB as docstring)
  2. Markdown files   — chunked by heading, embedded
  3. API docs         — OpenAPI/Swagger, JSDoc, Sphinx (future)

The indexer runs as a complement to the main code analysis:
  POST /api/analyze  → code graph (Phase 1)
  POST /api/docs/index → doc index (this module)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DocChunk:
    """A chunk of documentation with metadata."""
    id: str  # content-based hash
    source_file: str
    source_type: str  # "markdown", "comment", "apidoc"
    title: str  # section heading or inferred title
    content: str
    language: str = ""  # programming language hint
    embedding_vector: list[float] | None = None


class DocIndexer:
    """Index Markdown + API docs into KuzuDB for unified search."""

    # Heading patterns for chunk splitting
    _MD_HEADING = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    _CODE_BLOCK = re.compile(r'```[\s\S]*?```', re.MULTILINE)

    # File patterns
    MARKDOWN_GLOBS = ("*.md", "*.mdx", "*.markdown", "*.rst")
    API_DOC_GLOBS = ("*openapi*", "*swagger*", "*.yaml", "*.json")

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(Path.home() / ".code-kg" / "graph")

    # ─── Markdown Indexing ──────────────────────────────────────

    def index_markdown_files(self, repo_path: str) -> list[DocChunk]:
        """Scan a repo for Markdown files and index them as chunks."""
        repo = Path(repo_path).resolve()
        chunks: list[DocChunk] = []

        for glob_pattern in self.MARKDOWN_GLOBS:
            for md_file in repo.rglob(glob_pattern):
                # Skip hidden dirs, node_modules, _ref
                if self._should_skip(md_file):
                    continue

                try:
                    content = md_file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                file_chunks = self._chunk_markdown(
                    content, str(md_file.relative_to(repo)), str(md_file)
                )
                chunks.extend(file_chunks)

        # Store in KuzuDB
        if chunks:
            self._store_doc_chunks(chunks)

        return chunks

    def _chunk_markdown(
        self, content: str, rel_path: str, abs_path: str = ""
    ) -> list[DocChunk]:
        """Split Markdown into heading-level chunks."""
        chunks: list[DocChunk] = []

        # Find all heading positions
        headings = list(self._MD_HEADING.finditer(content))
        if not headings:
            # No headings → single chunk with filename as title
            clean = self._clean_markdown(content)
            if clean.strip():
                chunks.append(DocChunk(
                    id=self._hash(clean),
                    source_file=rel_path,
                    source_type="markdown",
                    title=Path(abs_path).stem if abs_path else rel_path,
                    content=clean[:2000],  # Cap chunk size
                ))
            return chunks

        # Split by heading
        for i, match in enumerate(headings):
            level = len(match.group(1))
            title = match.group(2).strip()
            start = match.start()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
            body = content[start:end]

            clean = self._clean_markdown(body)
            if not clean.strip():
                continue

            # Cap chunk size at ~2000 chars (token-budget friendly)
            chunks.append(DocChunk(
                id=self._hash(f"{rel_path}:{title}"),
                source_file=rel_path,
                source_type="markdown",
                title=title,
                content=clean[:2000],
            ))

        return chunks

    def _clean_markdown(self, text: str) -> str:
        """Strip code blocks and excessive whitespace."""
        text = self._CODE_BLOCK.sub("[code block]", text)
        # Collapse multiple blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ─── Source Comment Indexing ─────────────────────────────────

    def index_source_comments(self, repo_path: str) -> list[DocChunk]:
        """Extract top-level comments from source files as doc chunks.
        This supplements the tree-sitter docstring extraction with
        file-level and module-level comments.
        """
        repo = Path(repo_path).resolve()
        chunks: list[DocChunk] = []

        comment_patterns = {
            ".py": (r'(?:^|\n)\s*"""(?P<comment>[\s\S]*?)"""', "python"),
            ".js": (r'/\*\*(?P<comment>[\s\S]*?)\*/', "javascript"),
            ".ts": (r'/\*\*(?P<comment>[\s\S]*?)\*/', "typescript"),
            ".go": (r'/\*(?P<comment>[\s\S]*?)\*/', "go"),
            ".rs": (r'/\*\*(?P<comment>[\s\S]*?)\*/', "rust"),
            ".c": (r'/\*\*(?P<comment>[\s\S]*?)\*/', "c"),
            ".h": (r'/\*\*(?P<comment>[\s\S]*?)\*/', "c"),
            ".cpp": (r'/\*\*(?P<comment>[\s\S]*?)\*/', "cpp"),
            ".java": (r'/\*\*(?P<comment>[\s\S]*?)\*/', "java"),
        }

        source_extensions = set(comment_patterns.keys())

        for ext, (pattern, lang) in comment_patterns.items():
            for src_file in repo.rglob(f"*{ext}"):
                if self._should_skip(src_file):
                    continue

                try:
                    content = src_file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                rel = str(src_file.relative_to(repo))
                for match in re.finditer(pattern, content, re.MULTILINE):
                    comment = match.group("comment").strip()
                    if len(comment) < 50:  # Skip trivial comments
                        continue
                    # Clean up comment artifacts
                    comment = re.sub(r'^\s*\*\s?', '', comment, flags=re.MULTILINE)
                    comment = re.sub(r'\n\s*\n', '\n\n', comment).strip()

                    chunks.append(DocChunk(
                        id=self._hash(f"{rel}:comment:{match.start()}"),
                        source_file=rel,
                        source_type="comment",
                        title=self._infer_comment_title(comment, rel),
                        content=comment[:1500],
                        language=lang,
                    ))

        if chunks:
            self._store_doc_chunks(chunks)

        return chunks

    def _infer_comment_title(self, comment: str, file_path: str) -> str:
        """Infer a title from a comment block."""
        first_line = comment.split("\n")[0].strip()
        if len(first_line) < 80 and not first_line.startswith("@"):
            return first_line
        return f"Comments in {Path(file_path).name}"

    # ─── API Doc Indexing ───────────────────────────────────────

    def index_api_docs(self, repo_path: str) -> list[DocChunk]:
        """Index OpenAPI/Swagger specs as doc chunks."""
        repo = Path(repo_path).resolve()
        chunks: list[DocChunk] = []

        for spec_file in repo.rglob("*openapi*"):
            if self._should_skip(spec_file):
                continue
            try:
                content = spec_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if "openapi" not in content.lower() and "swagger" not in content.lower():
                continue

            # Extract endpoint summaries
            import json
            try:
                spec = json.loads(content) if spec_file.suffix == ".json" else None
            except Exception:
                spec = None

            if spec and "paths" in spec:
                for path, methods in spec["paths"].items():
                    for method, details in methods.items():
                        if isinstance(details, dict):
                            summary = details.get("summary", f"{method.upper()} {path}")
                            desc = details.get("description", "")
                            chunk_content = f"{summary}\n{desc}"[:1500]
                            chunks.append(DocChunk(
                                id=self._hash(f"{spec_file}:{method}:{path}"),
                                source_file=str(spec_file.relative_to(repo)),
                                source_type="apidoc",
                                title=summary,
                                content=chunk_content,
                            ))

        if chunks:
            self._store_doc_chunks(chunks)

        return chunks

    # ─── Full Indexing ──────────────────────────────────────────

    def index_all(self, repo_path: str) -> dict[str, int]:
        """Run all indexers on a repo."""
        md_chunks = self.index_markdown_files(repo_path)
        src_comments = self.index_source_comments(repo_path)
        api_chunks = self.index_api_docs(repo_path)

        return {
            "markdown_chunks": len(md_chunks),
            "source_comments": len(src_comments),
            "api_docs": len(api_chunks),
            "total": len(md_chunks) + len(src_comments) + len(api_chunks),
        }

    # ─── Search ─────────────────────────────────────────────────

    def search_docs(self, query: str, max_results: int = 20) -> list[dict[str, Any]]:
        """Search all indexed documentation."""
        from graph.kuzu_store import KnowledgeGraph
        kg = KnowledgeGraph(self.db_path)

        try:
            # Try DocNode table first (KuzuDB function syntax: CONTAINS(col, pattern))
            results = kg.query(
                "MATCH (d:DocNode) "
                "WHERE CONTAINS(d.title, $query) "
                "OR CONTAINS(d.content, $query) "
                "RETURN d.id, d.title, d.source_file, d.source_type, "
                "d.content, d.language "
                "LIMIT $limit",
                {"query": query, "limit": max_results},
            )
        except Exception:
            results = []

        # Fallback: search regular nodes' docstrings
        if not results:
            try:
                results = kg.query(
                    "MATCH (n:Node) "
                    "WHERE CONTAINS(n.docstring, $query) "
                    "RETURN n.id, n.label AS title, n.file_path AS source_file, "
                    "'comment' AS source_type, n.docstring AS content, "
                    "'' AS language "
                    "LIMIT $limit",
                    {"query": query, "limit": max_results},
                )
            except Exception:
                results = []

        kg.close()

        return [
            {
                "id": r.get("d.id", r.get("n.id", "")),
                "title": r.get("d.title", r.get("title", "")),
                "source_file": r.get("d.source_file", r.get("source_file", "")),
                "source_type": r.get("d.source_type", r.get("source_type", "comment")),
                "content": (r.get("d.content", r.get("content", "")) or "")[:300],
                "language": r.get("d.language", r.get("language", "")),
            }
            for r in results
        ]

    # ─── Helpers ─────────────────────────────────────────────────

    def _store_doc_chunks(self, chunks: list[DocChunk]) -> None:
        """Store doc chunks in KuzuDB as DocNode entities."""
        from graph.kuzu_store import KnowledgeGraph

        kg = KnowledgeGraph(self.db_path)
        stored = 0
        for chunk in chunks:
            try:
                # Check if already exists
                existing = kg.query(
                    "MATCH (d:DocNode {id: $id}) RETURN d.id",
                    {"id": chunk.id},
                )
                if existing:
                    continue

                kg.query(
                    "CREATE (d:DocNode {id: $id, source_file: $source_file, "
                    "source_type: $source_type, title: $title, "
                    "content: $content, language: $language})",
                    {
                        "id": chunk.id,
                        "source_file": chunk.source_file,
                        "source_type": chunk.source_type,
                        "title": chunk.title,
                        "content": chunk.content,
                        "language": chunk.language,
                    },
                )
                stored += 1
            except Exception:
                pass  # Duplicate or schema mismatch

        kg.close()

    def _should_skip(self, path: Path) -> bool:
        """Skip hidden dirs, build artifacts, _ref, node_modules."""
        parts = path.parts
        skip_dirs = {
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            "_ref", "build", "dist", ".next", ".cache", "kuzu_data",
            ".code-kg",
        }
        return any(p in skip_dirs for p in parts)

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()[:12]
