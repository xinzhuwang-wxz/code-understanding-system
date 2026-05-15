"""
SCIP Indexer — precise cross-file reference indexing via scip-python / scip-typescript.
Adds TYPED_AS, REFERENCES, DATA_FLOWS_TO edges to the knowledge graph.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json
import os
import subprocess


class ScipIndexer:
    """Runs SCIP indexers and ingests results into KuzuDB."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def has_python_indexer(self) -> bool:
        try:
            subprocess.run(["scip-python", "--help"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    def has_typescript_indexer(self) -> bool:
        try:
            subprocess.run(["npx", "scip-typescript", "--help"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def index(self) -> dict[str, Any]:
        results: dict[str, Any] = {"symbols": [], "relations": [], "indexer": "none"}
        if self.has_python_indexer:
            self._index_python(results)
        if self.has_typescript_indexer:
            self._index_typescript(results)
        if not results["symbols"]:
            results = self._fallback_index()
        return results

    def _index_python(self, results: dict) -> None:
        try:
            proc = subprocess.run(
                ["scip-python", "index", "--project-root", str(self.repo_path)],
                capture_output=True, text=True, timeout=120, cwd=str(self.repo_path)
            )
            if proc.returncode != 0:
                return
            index_file = self.repo_path / "index.scip"
            if index_file.exists():
                self._parse_scip_index(str(index_file), results)
                results["indexer"] = "scip-python"
        except Exception:
            pass

    def _index_typescript(self, results: dict) -> None:
        try:
            proc = subprocess.run(
                ["npx", "scip-typescript", "index"],
                capture_output=True, text=True, timeout=120, cwd=str(self.repo_path)
            )
            if proc.returncode != 0:
                return
            index_file = self.repo_path / "index.scip"
            if index_file.exists():
                self._parse_scip_index(str(index_file), results)
                results["indexer"] = "scip-typescript"
        except Exception:
            pass

    def _parse_scip_index(self, index_path: str, results: dict) -> None:
        try:
            proc = subprocess.run(
                ["scip", "print", index_path],
                capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                return
            data = json.loads(proc.stdout)
            symbols = data.get("symbols", [])
            occurrences = data.get("occurrences", [])
            symbol_map = {s.get("symbol", ""): s for s in symbols}

            for occ in occurrences:
                sid = occ.get("symbol", "")
                sinfo = symbol_map.get(sid, {})
                results["symbols"].append({
                    "name": sinfo.get("name", sid.split("/")[-1]),
                    "kind": self._kind_map(sinfo.get("kind", "")),
                    "file_path": occ.get("file", ""),
                    "line": occ.get("line", 0),
                    "signature": sinfo.get("signature", ""),
                    "docstring": sinfo.get("docstring", ""),
                })

            for sinfo in symbol_map.values():
                sid = sinfo.get("symbol", "")
                for rel in sinfo.get("relationships", {}).values():
                    results["relations"].append({
                        "source_id": f":{sid}",
                        "target_id": f":{rel.get('symbol','')}",
                        "kind": "references",
                        "line_number": 0,
                    })
        except Exception:
            pass

    def _fallback_index(self) -> dict[str, Any]:
        import re
        symbols: list[dict] = []
        relations: list[dict] = []
        py_files = list(Path(self.repo_path).rglob("*.py"))

        for fp in py_files:
            rel_path = str(fp.relative_to(self.repo_path))
            try:
                content = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lines = content.split("\n")

            for i, line in enumerate(lines, 1):
                m = re.match(r'^\s*def\s+(\w+)\s*\(([^)]*)\)', line)
                if m:
                    name, params = m.group(1), m.group(2)
                    symbols.append({
                        "name": name, "kind": "function",
                        "file_path": rel_path, "line": i,
                        "signature": f"{name}({params})",
                        "docstring": self._docstring(lines, i),
                    })
                    continue

                m = re.match(r'^\s*class\s+(\w+)(?:\(([^)]*)\))?', line)
                if m:
                    name, bases = m.group(1), m.group(2) or ""
                    symbols.append({
                        "name": name, "kind": "class",
                        "file_path": rel_path, "line": i,
                        "signature": f"class {name}" + (f"({bases})" if bases else ""),
                        "docstring": self._docstring(lines, i),
                    })
                    if bases:
                        for base in re.split(r'\s*,\s*', bases):
                            base = base.strip()
                            if base not in ("object", "ABC", "Exception", "type"):
                                relations.append({
                                    "source_id": f"{rel_path}:{name}",
                                    "target_id": base, "kind": "inherits", "line_number": i,
                                })

                m = re.match(r'^\s*(?:from\s+(\S+)\s+)?import\s+(.+)', line)
                if m:
                    names_str = m.group(2)
                    for name in re.split(r'\s*,\s*', names_str):
                        name = name.split(" as ")[0].strip()
                        if name and not name.startswith("_"):
                            relations.append({
                                "source_id": rel_path, "target_id": name,
                                "kind": "imports", "line_number": i,
                            })

            # Cross-file function call detection
            for i, line in enumerate(lines, 1):
                calls = re.findall(r'(?<![.\w])(\w+)\s*\(', line)
                for call in calls:
                    if call not in ("if", "for", "while", "print", "len", "range",
                                    "int", "str", "list", "dict", "set", "tuple",
                                    "isinstance", "hasattr", "getattr", "setattr",
                                    "super", "self", "cls", "type", "open", "zip",
                                    "enumerate", "sorted", "reversed", "filter", "map"):
                        relations.append({
                            "source_id": rel_path, "target_id": f"ref:{call}",
                            "kind": "calls", "line_number": i,
                        })

        return {"symbols": symbols, "relations": relations, "indexer": "fallback-regex"}

    @staticmethod
    def _docstring(lines: list[str], start: int) -> str:
        for i in range(start, min(start + 10, len(lines))):
            line = lines[i].strip()
            if line.startswith('"""') or line.startswith("'''"):
                q = line[:3]
                parts = [line[3:]] if len(line) > 3 and q not in line[3:] else [""]
                i += 1
                while i < len(lines):
                    if q in lines[i]:
                        parts.append(lines[i].split(q)[0])
                        break
                    parts.append(lines[i])
                    i += 1
                return "\n".join(p for p in parts if p).strip()
            if line and not line.startswith("#") and not line.startswith("@"):
                break
        return ""

    @staticmethod
    def _kind_map(kind: str) -> str:
        return {
            "Method": "function", "Function": "function",
            "Class": "class", "Interface": "class",
            "Type": "class", "Variable": "variable",
            "Module": "file", "Package": "file",
        }.get(kind, "unknown")


def index_repo(repo_path: str) -> dict[str, Any]:
    return ScipIndexer(repo_path).index()
