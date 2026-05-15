"""
LSP Client — go-to-definition, find-references, hover via Jedi.
Supports Python natively; falls back to grep-based search for other languages.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import re
import os


class LspClient:
    """Lightweight LSP client using Jedi for Python, regex for other languages."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self._jedi_project = None

    def _ensure_jedi(self):
        if self._jedi_project is None:
            import jedi
            self._jedi_project = jedi.Project(str(self.project_root))

    def definition(self, file_path: str, line: int, column: int) -> list[dict]:
        """Go to definition. Returns list of {file_path, line, column, name, description}."""
        if file_path.endswith('.py'):
            return self._jedi_definition(file_path, line, column)
        return self._grep_definition(file_path, line, column)

    def references(self, file_path: str, line: int, column: int) -> list[dict]:
        """Find all references. Returns list of {file_path, line, column, name, context}."""
        if file_path.endswith('.py'):
            return self._jedi_references(file_path, line, column)
        return self._grep_references(file_path, line, column)

    def hover(self, file_path: str, line: int, column: int) -> dict | None:
        """Get hover info. Returns {name, type, docstring, signature} or None."""
        if file_path.endswith('.py'):
            return self._jedi_hover(file_path, line, column)
        return self._grep_hover(file_path, line, column)

    # ─── Jedi (Python) ─────────────────────────────────────

    def _jedi_definition(self, file_path: str, line: int, column: int) -> list[dict]:
        self._ensure_jedi()
        import jedi
        try:
            source = Path(file_path).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return []
        script = jedi.Script(source, path=file_path, project=self._jedi_project)
        try:
            names = script.goto(line, column, follow_imports=True)
        except Exception:
            return []
        results = []
        for n in names:
            results.append({
                "file_path": str(n.module_path) if n.module_path else file_path,
                "line": n.line or 0,
                "column": n.column or 0,
                "name": n.name,
                "description": n.description,
                "type": n.type,
            })
        return results

    def _jedi_references(self, file_path: str, line: int, column: int) -> list[dict]:
        self._ensure_jedi()
        import jedi
        try:
            source = Path(file_path).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return []
        script = jedi.Script(source, path=file_path, project=self._jedi_project)
        try:
            refs = script.get_references(line, column)
        except Exception:
            return []
        results = []
        for r in refs:
            results.append({
                "file_path": str(r.module_path) if r.module_path else file_path,
                "line": r.line or 0,
                "column": r.column or 0,
                "name": r.name,
                "context": r.description,
                "type": r.type,
            })
        return results

    def _jedi_hover(self, file_path: str, line: int, column: int) -> dict | None:
        self._ensure_jedi()
        import jedi
        try:
            source = Path(file_path).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return None
        script = jedi.Script(source, path=file_path, project=self._jedi_project)
        try:
            names = script.infer(line, column)
        except Exception:
            return None
        if not names:
            # Try help() as fallback
            try:
                helps = script.help(line, column)
                if helps:
                    n = helps[0]
                    return {"name": n.name, "type": n.type, "docstring": n.docstring(), "signature": n.description}
            except Exception:
                pass
            return None
        n = names[0]
        return {
            "name": n.name,
            "type": n.type,
            "docstring": n.docstring(),
            "signature": n.description,
        }

    # ─── Grep fallback (non-Python) ──────────────────────────

    def _resolve_file(self, file_path: str) -> str:
        p = Path(file_path)
        if not p.is_absolute():
            p = self.project_root / file_path
        return str(p)

    def _read_line(self, file_path: str, line: int) -> str:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for i, l in enumerate(f, 1):
                    if i == line:
                        return l
        except OSError:
            pass
        return ""

    def _extract_word(self, text: str, column: int) -> str:
        """Extract the identifier at a given column position."""
        if column > len(text):
            return ""
        # Expand left
        start = column
        while start > 0 and (text[start-1].isalnum() or text[start-1] == '_'):
            start -= 1
        # Expand right
        end = column
        while end < len(text) and (text[end].isalnum() or text[end] == '_'):
            end += 1
        return text[start:end]

    def _grep_definition(self, file_path: str, line: int, column: int) -> list[dict]:
        fp = self._resolve_file(file_path)
        target_line = self._read_line(fp, line)
        if not target_line:
            return []
        word = self._extract_word(target_line, column - 1)
        if not word:
            return []
        # Search project for def/class/fn/function declarations
        results = []
        patterns = [
            rf'^\s*(def|class|fn|func|function|const|let|var|type|interface)\s+{re.escape(word)}',
        ]
        for root, _, files in os.walk(self.project_root):
            for f in files:
                if f.startswith('.') or '/.' in root:
                    continue
                full = os.path.join(root, f)
                try:
                    with open(full, 'r', encoding='utf-8', errors='ignore') as fh:
                        for i, l in enumerate(fh, 1):
                            for pat in patterns:
                                if re.search(pat, l):
                                    results.append({"file_path": full, "line": i, "column": 1, "name": word, "description": l.strip(), "type": "definition"})
                                    break
                except OSError:
                    pass
        return results[:20]

    def _grep_references(self, file_path: str, line: int, column: int) -> list[dict]:
        fp = self._resolve_file(file_path)
        target_line = self._read_line(fp, line)
        if not target_line:
            return []
        word = self._extract_word(target_line, column - 1)
        if not word or len(word) < 2:
            return []
        results = []
        for root, _, files in os.walk(self.project_root):
            for f in files:
                if f.startswith('.') or '/.' in root:
                    continue
                full = os.path.join(root, f)
                try:
                    with open(full, 'r', encoding='utf-8', errors='ignore') as fh:
                        for i, l in enumerate(fh, 1):
                            if re.search(rf'{re.escape(word)}', l) and full != fp:
                                results.append({"file_path": full, "line": i, "column": 1, "name": word, "context": l.strip(), "type": "reference"})
                except OSError:
                    pass
        return results[:50]

    def _grep_hover(self, file_path: str, line: int, column: int) -> dict | None:
        fp = self._resolve_file(file_path)
        target_line = self._read_line(fp, line)
        if not target_line:
            return None
        word = self._extract_word(target_line, column - 1)
        if not word:
            return None
        return {"name": word, "type": "unknown", "docstring": "", "signature": target_line.strip()}


# Module-level cache
_clients: dict[str, LspClient] = {}

def get_lsp_client(project_root: str) -> LspClient:
    """Get or create cached LSP client per project."""
    key = str(Path(project_root).resolve())
    if key not in _clients:
        _clients[key] = LspClient(project_root)
    return _clients[key]
