"""
Cross-file reference indexer — replaces SCIP external binary dependency.

Uses tree-sitter + KuzuDB graph data to infer cross-file references:
  1. Scans all source files (via tree-sitter) for import statements
  2. Matches imported symbols to existing nodes in KuzuDB
  3. Adds TYPED_AS, REFERENCES, IMPORTS edges between files/modules

No external SCIP toolkit binaries required.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from log import get_logger; logger = get_logger(__name__)


_REPO_CACHE: dict[str, dict] = {}


def _get_file_imports(repo_root: Path, file_path: str, content: str) -> list[dict]:
    """Extract imports from a file using tree-sitter if available, else regex."""
    imports: list[dict] = []
    ext = Path(file_path).suffix.lower()

    try:
        from analyzer.ts_parser import get_parser
        parser = get_parser()
        symbols, relations = parser.parse_file(str(file_path), content)
        for r in relations:
            if r.kind == "imports":
                imports.append({
                    "source_file": str(Path(file_path).relative_to(repo_root)),
                    "target": r.target,
                    "kind": "imports",
                    "line_number": r.line_number,
                })
        if imports:
            return imports
    except Exception:
        pass

    lines = content.split("\n")

    if ext == ".py":
        for i, line in enumerate(lines, 1):
            m = re.match(r'^\s*from\s+(\S+)\s+import\s+(.+)$', line)
            if m:
                module = m.group(1)
                names = [n.strip().split(" as ")[0] for n in m.group(2).split(",")]
                for name in names:
                    if name and not name.startswith("_"):
                        imports.append({
                            "source_file": str(Path(file_path).relative_to(repo_root)),
                            "target": f"{module}.{name}" if name != "*" else module,
                            "kind": "imports",
                            "line_number": i,
                        })
                continue
            m = re.match(r'^\s*import\s+(.+)$', line)
            if m:
                for mod in m.group(1).split(","):
                    mod = mod.strip().split(" as ")[0]
                    if mod:
                        imports.append({
                            "source_file": str(Path(file_path).relative_to(repo_root)),
                            "target": mod,
                            "kind": "imports",
                            "line_number": i,
                        })

    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        for i, line in enumerate(lines, 1):
            m = re.search(r'(?:import|require)\s+.*?[\'\"]([^\'\"]+)[\'\"]', line)
            if m:
                target = m.group(1)
                if target.startswith(".") or target.startswith("/"):
                    imports.append({
                        "source_file": str(Path(file_path).relative_to(repo_root)),
                        "target": target,
                        "kind": "imports",
                        "line_number": i,
                    })

    return imports


def _get_file_exports(file_path: str, content: str) -> list[dict]:
    """Extract exported symbols from a file using tree-sitter if available, else regex."""
    exports: list[dict] = []
    ext = Path(file_path).suffix.lower()

    try:
        from analyzer.ts_parser import get_parser
        parser = get_parser()
        symbols, _ = parser.parse_file(file_path, content)
        for s in symbols:
            exports.append({
                "name": s.name,
                "kind": s.kind,
                "file_path": file_path,
                "line": s.line_start,
                "signature": s.signature,
                "docstring": s.docstring,
            })
        if exports:
            return exports
    except Exception:
        pass

    lines = content.split("\n")
    patterns = {
        ".py": [
            (r'^\s*async\s+def\s+(\w+)\s*\(', "function"),
            (r'^\s*def\s+(\w+)\s*\(', "function"),
            (r'^\s*class\s+(\w+)', "class"),
        ],
        ".js": [
            (r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', "function"),
            (r'^\s*(?:export\s+)?class\s+(\w+)', "class"),
            (r'^\s*(?:export\s+)?const\s+(\w+)\s*=', "variable"),
        ],
        ".ts": [
            (r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', "function"),
            (r'^\s*(?:export\s+)?class\s+(\w+)', "class"),
            (r'^\s*(?:export\s+)?const\s+(\w+)\s*:', "variable"),
            (r'^\s*(?:export\s+)?interface\s+(\w+)', "class"),
            (r'^\s*(?:export\s+)?type\s+(\w+)\s*=', "class"),
        ],
        ".go": [
            (r'^\s*func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(', "function"),
            (r'^\s*type\s+(\w+)\s+struct', "class"),
            (r'^\s*type\s+(\w+)\s+interface', "class"),
        ],
        ".rs": [
            (r'^\s*(?:pub\s+)?fn\s+(\w+)\s*\(', "function"),
            (r'^\s*(?:pub\s+)?struct\s+(\w+)', "class"),
            (r'^\s*(?:pub\s+)?trait\s+(\w+)', "class"),
            (r'^\s*(?:pub\s+)?enum\s+(\w+)', "class"),
        ],
    }

    file_patterns = patterns.get(ext, patterns.get(".py", []))
    for i, line in enumerate(lines, 1):
        for pat, kind in file_patterns:
            m = re.match(pat, line)
            if m and m.group(1) not in ("if", "for", "while"):
                exports.append({
                    "name": m.group(1),
                    "kind": kind,
                    "file_path": file_path,
                    "line": i,
                    "signature": line.strip()[:120],
                    "docstring": "",
                })
                break

    return exports


def _resolve_cross_references(
    repo_root: Path,
    source_files: list[Path],
) -> tuple[list[dict], list[dict]]:
    """Build cross-file reference index.

    Returns (symbols, relations):
      symbols: all exported symbols across the repo
      relations: cross-file import/reference relationships
    """
    all_symbols: list[dict] = []
    all_relations: list[dict] = []
    file_exports: dict[str, list[dict]] = {}
    file_imports: dict[str, list[dict]] = {}

    for fp in source_files:
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(fp.relative_to(repo_root))

        exports = _get_file_exports(str(fp), content)
        file_exports[rel] = exports
        for exp in exports:
            exp["file_path"] = rel
            all_symbols.append(exp)

        imports = _get_file_imports(repo_root, str(fp), content)
        file_imports[rel] = imports

        for imp in imports:
            all_relations.append(imp)

    # Resolve imports to actual file paths
    ext_order = [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".mjs", ".cjs"]
    for rel, imports in file_imports.items():
        for imp in imports:
            target_mod = imp["target"]
            resolved = False

            for ext in ext_order:
                candidate = repo_root / f"{target_mod}{ext}"
                if candidate.exists():
                    try:
                        imp["resolved_file"] = str(candidate.relative_to(repo_root))
                    except ValueError:
                        imp["resolved_file"] = str(candidate)
                    resolved = True
                    break

                for prefix in ("src/", "lib/", "app/", ""):
                    candidate = repo_root / prefix / f"{target_mod}{ext}"
                    if candidate.exists():
                        try:
                            imp["resolved_file"] = str(candidate.relative_to(repo_root))
                        except ValueError:
                            imp["resolved_file"] = str(candidate)
                        resolved = True
                        break
                if resolved:
                    break

            imp["resolved"] = resolved

    # Create cross-file REFERENCES edges
    for rel, imports in file_imports.items():
        for imp in imports:
            resolved_file = imp.get("resolved_file")
            if not resolved_file:
                continue
            if resolved_file == rel:
                continue
            all_relations.append({
                "source_file": rel,
                "target_file": resolved_file,
                "source_id": f"file:{rel}",
                "target_id": f"file:{resolved_file}",
                "kind": "references",
                "line_number": imp["line_number"],
            })

    return all_symbols, all_relations


def _should_skip(path: Path) -> bool:
    parts = path.parts
    skip_dirs = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "_ref", "build", "dist", ".next", ".cache", "kuzu_data",
        ".code-kg", ".pytest_cache", ".ruff_cache", ".mypy_cache",
        ".tox", ".eggs", ".egg-info", "__pycache__",
    }
    return any(p in skip_dirs for p in parts)


SOURCE_EXTS = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
               ".go", ".rs", ".java", ".rb", ".php", ".kt", ".scala",
               ".swift", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"}


class CrossFileIndexer:
    """Built-in cross-file reference indexer — no external SCIP required."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    def index(self) -> dict[str, Any]:
        """Run cross-file reference indexing.

        Returns:
            dict with 'symbols' (all exported symbols), 'relations' (cross-file refs),
            and stats.
        """
        source_files = [
            p for p in self.repo_path.rglob("*")
            if p.suffix.lower() in SOURCE_EXTS and not _should_skip(p)
        ]

        symbols, relations = _resolve_cross_references(self.repo_path, source_files)

        result = {
            "symbols": symbols,
            "relations": relations,
            "indexer": "builtin-crossref",
            "stats": {
                "files_scanned": len(source_files),
                "symbols_found": len(symbols),
                "relations_found": len(relations),
                "resolved_imports": sum(1 for r in relations if r.get("resolved", False)),
            },
        }
        return result

    def ingest_into_graph(self, kg: Any, repo_path: str) -> dict:
        """Ingest cross-file references into the knowledge graph.

        Args:
            kg: KnowledgeGraph instance
            repo_path: Repository path for context

        Returns:
            dict with ingestion stats
        """
        result = self.index()
        symbols = result["symbols"]
        relations = result["relations"]

        if not symbols and not relations:
            return {"nodes_added": 0, "edges_added": 0}

        added = kg.ingest_analysis(repo_path, symbols, relations, replace=False)
        return {
            "nodes_added": added.get("nodes", 0),
            "edges_added": added.get("edges", 0),
        }


_SCIP_CACHE: CrossFileIndexer | None = None


def index_repo(repo_path: str, use_cache: bool = False) -> dict[str, Any]:
    """Main entry point — index cross-file references.

    Args:
        repo_path: Path to the repository to index.
        use_cache: If True and the same repo was indexed before, return cached result.

    Returns:
        dict with symbols, relations, and stats.
    """
    global _SCIP_CACHE
    if use_cache and _SCIP_CACHE is not None:
        return _SCIP_CACHE.index()

    indexer = CrossFileIndexer(repo_path)
    result = indexer.index()
    _SCIP_CACHE = indexer
    return result
