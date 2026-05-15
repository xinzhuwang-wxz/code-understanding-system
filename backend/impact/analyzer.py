"""
Diff-triggered impact analysis engine.

Flow:
  1. Git diff (or diff text) → identify changed files/functions
  2. KuzuDB graph traversal → direct dependents + cascading closure
  3. LLM → natural-language summary of changes and risk
  4. Structured output for API / MCP / CLI consumption
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ─── Data Model ────────────────────────────────────────────────────

@dataclass
class ChangedEntity:
    """A code entity that was modified in the diff."""
    file_path: str
    entity_name: str = ""
    entity_type: str = ""  # function, class, method, etc.
    line_range: tuple[int, int] = (0, 0)  # (start, end)
    change_type: str = "modified"  # added, modified, deleted


@dataclass
class ImpactResult:
    """Full impact analysis result for a diff."""
    changed_files: list[str] = field(default_factory=list)
    changed_entities: list[ChangedEntity] = field(default_factory=list)

    # Graph-based impact
    direct_dependents: list[dict[str, Any]] = field(default_factory=list)
    cascading_impact: list[dict[str, Any]] = field(default_factory=list)
    total_affected_files: int = 0
    total_affected_nodes: int = 0

    # Test impact
    related_tests: list[str] = field(default_factory=list)

    # LLM summary
    summary: str = ""
    risk_level: str = "unknown"  # low, medium, high, critical

    # Raw diff for reference
    diff_summary: str = ""  # short diffstat
    raw_errors: list[str] = field(default_factory=list)


class DiffAnalyzer:
    """Analyze git diffs for code impact assessment."""

    # Regex to detect function/class/method changes in diff hunks
    _FUNC_HUNK_PATTERN = re.compile(
        r'^@@.*@@\s+(?P<context>.*)$', re.MULTILINE
    )
    _DEF_PATTERN = re.compile(
        r'(def|class|function|const|let|var|func|fn|pub fn|'
        r'public (static )?(class|void|int|string|bool|Task|async) )\s+'
        r'(?P<name>\w+)',
        re.MULTILINE,
    )

    def __init__(self, repo_path: str | None = None, db_path: str | None = None):
        self.repo_path = str(Path(repo_path).resolve()) if repo_path else None
        self.db_path = db_path or str(Path.home() / ".code-kg" / "graph")

    # ─── Diff Operations ────────────────────────────────────────

    def run_git_diff(
        self,
        commit_range: str = "HEAD~1..HEAD",
    ) -> str:
        """Run git diff and return the raw output."""
        if not self.repo_path:
            return ""
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_path, "diff", commit_range],
                capture_output=True, text=True, timeout=30,
            )
            return result.stdout
        except Exception as e:
            return f"[ERROR running git diff: {e}]"

    def run_git_diffstat(self, commit_range: str = "HEAD~1..HEAD") -> str:
        """Get brief diffstat."""
        if not self.repo_path:
            return ""
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_path, "diff", "--stat", commit_range],
                capture_output=True, text=True, timeout=30,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def run_git_changed_files(
        self, commit_range: str = "HEAD~1..HEAD"
    ) -> list[str]:
        """Get list of changed files from git."""
        if not self.repo_path:
            return []
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_path, "diff", "--name-only", commit_range],
                capture_output=True, text=True, timeout=30,
            )
            return [f.strip() for f in result.stdout.split("\n") if f.strip()]
        except Exception:
            return []

    # ─── Diff Parsing ───────────────────────────────────────────

    def parse_diff(self, diff_text: str) -> list[ChangedEntity]:
        """Parse a unified diff to extract changed code entities."""
        entities: list[ChangedEntity] = []
        current_file = ""
        current_line_start = 0
        current_line_end = 0

        for line in diff_text.split("\n"):
            # Detect file header
            if line.startswith("diff --git "):
                # Extract b/ path
                parts = line.split(" ")
                if len(parts) >= 4:
                    b_path = parts[-1]
                    if b_path.startswith("b/"):
                        current_file = b_path[2:]
                    else:
                        current_file = b_path
                continue

            # Detect hunk header (@@ -x,y +a,b @@ context)
            if line.startswith("@@"):
                m = re.search(r'\+(\d+)(?:,(\d+))?', line)
                if m:
                    current_line_start = int(m.group(1))
                    current_line_end = current_line_start + (
                        int(m.group(2)) if m.group(2) else 1
                    )

                # Try to infer function name from hunk context
                context = line.split("@@", 2)[-1].strip()
                if context:
                    func_match = re.search(r'(?:def|function|class|fn|func)\s+(\w+)', context)
                    if func_match:
                        entities.append(ChangedEntity(
                            file_path=current_file,
                            entity_name=func_match.group(1),
                            line_range=(current_line_start, current_line_end),
                            change_type="modified",
                        ))
                continue

            # Detect added/modified lines with function definitions
            if line.startswith("+") and not line.startswith("+++"):
                def_match = self._DEF_PATTERN.search(line)
                if def_match:
                    name = def_match.group("name")
                    # Avoid duplicates for the same entity
                    if not any(
                        e.file_path == current_file and e.entity_name == name
                        for e in entities
                    ):
                        entities.append(ChangedEntity(
                            file_path=current_file,
                            entity_name=name,
                            entity_type=self._infer_type(line),
                            line_range=(current_line_start, current_line_end),
                            change_type="modified",
                        ))

            # Detect deleted entities
            if line.startswith("-") and not line.startswith("---"):
                def_match = self._DEF_PATTERN.search(line)
                if def_match:
                    name = def_match.group("name")
                    if not any(
                        e.file_path == current_file and e.entity_name == name
                        and e.change_type == "deleted"
                        for e in entities
                    ):
                        entities.append(ChangedEntity(
                            file_path=current_file,
                            entity_name=name,
                            entity_type=self._infer_type(line),
                            change_type="deleted",
                        ))

        return entities

    def _infer_type(self, line: str) -> str:
        """Infer entity type from a definition line."""
        line_stripped = line.lstrip("+- ").lower()
        if line_stripped.startswith("class "):
            return "class"
        elif line_stripped.startswith("def "):
            return "function"
        elif line_stripped.startswith("function "):
            return "function"
        elif line_stripped.startswith("fn "):
            return "function"
        elif line_stripped.startswith("pub fn "):
            return "function"
        elif "=>" in line or line_stripped.startswith("const "):
            return "function"
        return "unknown"

    # ─── Impact Analysis (Graph-based) ──────────────────────────

    def analyze_impact(
        self,
        changed_entities: list[ChangedEntity],
        repo_path_abs: str = "",
    ) -> dict[str, Any]:
        """Trace graph impact for changed entities."""
        from graph.kuzu_store import KnowledgeGraph

        if not os.path.isdir(self.db_path):
            return self._fallback_impact(changed_entities)

        kg = KnowledgeGraph(self.db_path)

        all_dependents: list[dict[str, Any]] = []
        all_cascading: list[dict[str, Any]] = []
        affected_files: set[str] = set()
        related_tests: set[str] = set()

        for entity in changed_entities:
            affected_files.add(entity.file_path)

            if not entity.entity_name:
                continue

            # Search for the entity in the graph
            nodes = kg.query(
                "MATCH (n:Node) "
                "WHERE n.label = $name AND n.file_path = $file "
                "RETURN n.id, n.label, n.type, n.file_path, n.line_number, "
                "n.signature, n.docstring",
                {"name": entity.entity_name, "file": entity.file_path},
            )

            if not nodes:
                # Try wider search by name only
                nodes = kg.query(
                    "MATCH (n:Node) WHERE n.label = $name "
                    "RETURN n.id, n.label, n.type, n.file_path, n.line_number, "
                    "n.signature, n.docstring",
                    {"name": entity.entity_name},
                )

            for node in nodes:
                node_id = node["n.id"]
                dependents = kg.get_dependents(node_id)
                for dep in dependents:
                    all_dependents.append(dep)
                    affected_files.add(dep.get("file_path", ""))

                # Cascading: find dependents of dependents (1 more hop)
                for dep in dependents[:5]:  # Limit to avoid explosion
                    dep_id = dep.get("id", "")
                    if dep_id:
                        casc = kg.get_dependents(dep_id)
                        for c in casc:
                            all_cascading.append(c)
                            affected_files.add(c.get("file_path", ""))

        kg.close()

        # Find related tests
        for f in affected_files:
            test_file = self._guess_test_file(f)
            if test_file:
                related_tests.add(test_file)

        return {
            "direct_dependents": all_dependents,
            "cascading_impact": all_cascading,
            "total_affected_files": len(affected_files),
            "total_affected_nodes": len(all_dependents) + len(all_cascading),
            "related_tests": sorted(related_tests),
        }

    def _fallback_impact(
        self, entities: list[ChangedEntity]
    ) -> dict[str, Any]:
        """Fallback when no graph DB is available."""
        files = list({e.file_path for e in entities})
        return {
            "direct_dependents": [],
            "cascading_impact": [],
            "total_affected_files": len(files),
            "total_affected_nodes": 0,
            "related_tests": self._guess_tests_from_files(files),
        }

    def _guess_test_file(self, file_path: str) -> str | None:
        """Heuristic: map src/foo/bar.py → tests/foo/test_bar.py."""
        p = Path(file_path)
        stem = p.stem
        parent = str(p.parent)

        # Common test directory patterns
        candidates = []
        for src_pattern, test_pattern in [
            ("/src/", "/tests/"),
            ("/lib/", "/test/"),
            ("/backend/", "/tests/"),
            ("/src/", "/test/"),
            ("/", "/tests/"),  # root project
        ]:
            if src_pattern in parent or "/" in parent:
                test_dir = parent.replace(src_pattern, test_pattern) if src_pattern in parent else (
                    str(Path(parent) / "tests")
                )
                for prefix in ["test_", "", "spec/"]:
                    for suffix in [".py", ".ts", ".js", ".go", ".rs"]:
                        candidate = str(Path(test_dir) / f"{prefix}{stem}_test{suffix}")
                        candidates.append(candidate)
                        candidate2 = str(Path(test_dir) / f"test_{stem}{suffix}")
                        candidates.append(candidate2)

        # Check which candidates exist
        for c in candidates:
            if self.repo_path:
                full = Path(self.repo_path) / c
                if full.exists():
                    return c
            elif os.path.exists(c):
                return c
        return None

    def _guess_tests_from_files(self, files: list[str]) -> list[str]:
        tests = []
        for f in files:
            t = self._guess_test_file(f)
            if t:
                tests.append(t)
        return tests

    # ─── LLM Summary ────────────────────────────────────────────

    def generate_llm_summary(
        self,
        diff_stat: str,
        changed_files: list[str],
        changed_entities: list[ChangedEntity],
        impact: dict[str, Any],
    ) -> tuple[str, str]:
        """Generate a natural-language summary using DeepSeek LLM.
        Returns (summary_text, risk_level).
        """
        from search.llm import get_llm

        entities_desc = "\n".join(
            f"  - {e.change_type}: {e.entity_name} ({e.entity_type}) in {e.file_path}"
            for e in changed_entities[:20]
        ) or "  (no entities detected)"

        prompt = f"""Analyze the following code change and assess its impact:

CHANGED FILES ({len(changed_files)}):
{chr(10).join(f'  - {f}' for f in changed_files[:15])}

CHANGED ENTITIES:
{entities_desc}

DIFF STATS:
{diff_stat[:2000]}

IMPACT (from graph):
- Direct dependents: {len(impact.get('direct_dependents', []))}
- Cascading impact: {len(impact.get('cascading_impact', []))}
- Total affected files: {impact.get('total_affected_files', len(changed_files))}
- Related tests: {', '.join(impact.get('related_tests', [])[:10]) or 'none detected'}

Please provide:
1. A 2-3 sentence summary of what changed
2. Risk assessment (low/medium/high/critical)
3. Recommended actions (which tests to run, what to watch for)

Format as:
SUMMARY: <summary>
RISK: <risk_level>
ACTIONS: <recommended actions>"""

        try:
            llm = get_llm()
            if llm.available:
                result = llm.answer_question(
                    prompt,
                    context="",  # No extra context needed
                )
                return self._parse_llm_response(result)
        except Exception as e:
            return (f"LLM unavailable: {e}", "unknown")

        return ("LLM not available. Run with DEEPSEEK_API_KEY set.", "unknown")

    def _parse_llm_response(self, text: str) -> tuple[str, str]:
        """Parse LLM response into summary + risk level."""
        summary = text
        risk = "unknown"

        for line in text.split("\n"):
            if line.upper().startswith("SUMMARY:"):
                summary = line.split(":", 1)[-1].strip()
            elif line.upper().startswith("RISK:"):
                risk_raw = line.split(":", 1)[-1].strip().lower()
                for level in ["critical", "high", "medium", "low"]:
                    if level in risk_raw:
                        risk = level
                        break

        return summary, risk

    # ─── Main Entry Point ───────────────────────────────────────

    def analyze(
        self,
        diff_text: str = "",
        commit_range: str = "HEAD~1..HEAD",
    ) -> ImpactResult:
        """Run full impact analysis: diff → parse → graph → LLM summary."""
        result = ImpactResult()

        # 1. Run or parse diff
        if diff_text:
            raw_diff = diff_text
        elif self.repo_path:
            raw_diff = self.run_git_diff(commit_range)
        else:
            result.raw_errors.append("No repo_path set and no diff text provided")
            return result

        if not raw_diff.strip():
            result.summary = "No changes to analyze."
            result.risk_level = "low"
            return result

        # 2. Get metadata
        changed_files = (
            self.run_git_changed_files(commit_range)
            if self.repo_path and not diff_text
            else self._extract_files_from_diff(raw_diff)
        )
        diff_stat = (
            self.run_git_diffstat(commit_range)
            if self.repo_path and not diff_text
            else self._extract_diffstat_from_diff(raw_diff)
        )

        result.changed_files = changed_files
        result.diff_summary = diff_stat

        # 3. Parse entities
        result.changed_entities = self.parse_diff(raw_diff)

        # 4. Graph impact
        impact = self.analyze_impact(result.changed_entities)
        result.direct_dependents = impact.get("direct_dependents", [])
        result.cascading_impact = impact.get("cascading_impact", [])
        result.total_affected_files = impact.get("total_affected_files", len(changed_files))
        result.total_affected_nodes = impact.get("total_affected_nodes", 0)
        result.related_tests = impact.get("related_tests", [])

        # 5. LLM summary
        summary, risk = self.generate_llm_summary(
            diff_stat, changed_files, result.changed_entities, impact
        )
        result.summary = summary
        result.risk_level = risk

        return result

    def _extract_files_from_diff(self, diff_text: str) -> list[str]:
        """Extract changed file names from diff text (no git call)."""
        files: set[str] = set()
        for line in diff_text.split("\n"):
            if line.startswith("diff --git "):
                parts = line.split(" ")
                if len(parts) >= 4:
                    b_path = parts[-1]
                    if b_path.startswith("b/"):
                        files.add(b_path[2:])
        return sorted(files)

    def _extract_diffstat_from_diff(self, diff_text: str) -> str:
        """Make a rough diffstat from raw diff text."""
        files = self._extract_files_from_diff(diff_text)
        added = diff_text.count("\n+") - diff_text.count("\n+++")
        removed = diff_text.count("\n-") - diff_text.count("\n---")
        lines = [f" {len(files)} files changed, {added} insertions(+), {removed} deletions(-)"]
        return "\n".join(lines)
