"""
Code review engine — LLM-powered code review with graph-aware context.

Analyzes diffs or code snippets and produces structured reviews:
  - Issues found (severity, location, description, suggestion)
  - Strengths (what's done well)
  - Risk assessment
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReviewIssue:
    """A single issue found during code review."""
    severity: str  # "error", "warning", "info"
    line: int = 0
    column: int = 0
    message: str = ""
    suggestion: str = ""
    rule_id: str = ""
    file_path: str = ""


@dataclass
class ReviewResult:
    """Complete review result."""
    issues: list[ReviewIssue] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    summary: str = ""
    risk_level: str = "low"  # "low", "medium", "high", "critical"
    files_reviewed: list[str] = field(default_factory=list)
    total_lines: int = 0
    score: int = 100  # 0-100


class CodeReviewer:
    """Review code changes with LLM + static analysis."""

    def __init__(self, repo_path: str = ""):
        self.repo_path = repo_path
        self._static_checks: list[tuple[str, str, str, str]] = [
            ("hardcoded_secret", r'(?:password|secret|api_key|token|credential)\s*[:=]\s*["\'][^"\']+["\']', "error", "SEC-001"),
            ("security_hazard", r'(?:eval|exec|os\.system|subprocess\.call|subprocess\.Popen)\s*\(', "warning", "SEC-002"),
            ("sql_injection", r'(?:execute|executemany)\s*\(\s*f["\']', "warning", "SEC-003"),
            ("print_statement", r'^\s*print\s*\(', "info", "STYLE-001"),
            ("debug_breakpoint", r'(?:pdb\.set_trace|breakpoint)\s*\(', "error", "SEC-004"),
            ("todo_comment", r'#\s*(?:TODO|FIXME|HACK|XXX)\b', "info", "STYLE-002"),
            ("commented_code", r'^\s*#.*(?:def |class |import |return |if |for )', "info", "STYLE-003"),
            ("console_log", r'console\.(?:log|debug|warn|error)\s*\(', "info", "STYLE-004"),
            ("mutable_default", r'def \w+\(.*=\s*(?:\[\]|\{\}|set\(\))', "warning", "BUG-001"),
            ("bare_except", r'except\s*:', "warning", "BUG-002"),
            ("relative_import", r'from\s+\.\.?\s*import', "warning", "STYLE-005"),
        ]

    def add_static_checks(self, checks: list[tuple[str, str, str, str]]) -> None:
        """Add custom static check rules."""
        self._static_checks.extend(checks)

    def run_static_analysis(self, file_path: str, content: str) -> list[ReviewIssue]:
        """Run static analysis rules on a code snippet."""
        issues: list[ReviewIssue] = []
        for name, pattern, severity, rule_id in self._static_checks:
            for m in re.finditer(pattern, content, re.MULTILINE):
                line_num = content[:m.start()].count("\n") + 1
                issues.append(ReviewIssue(
                    severity=severity,
                    line=line_num,
                    message=self._check_message(name),
                    suggestion=self._check_suggestion(name),
                    rule_id=rule_id,
                    file_path=file_path,
                ))
        return issues

    @staticmethod
    def _check_message(name: str) -> str:
        msgs = {
            "hardcoded_secret": "Potential hardcoded secret detected",
            "security_hazard": "Use of potentially dangerous function",
            "sql_injection": "Possible SQL injection (f-string in query)",
            "print_statement": "Print statement left in production code",
            "debug_breakpoint": "Debug breakpoint left in code",
            "todo_comment": "TODO/FIXME comment left in code",
            "commented_code": "Commented-out code detected",
            "console_log": "Console log statement left in code",
            "mutable_default": "Mutable default argument (can cause shared state bugs)",
            "bare_except": "Bare except clause (catches all exceptions)",
            "relative_import": "Relative import detected",
        }
        return msgs.get(name, name.replace("_", " ").title())

    @staticmethod
    def _check_suggestion(name: str) -> str:
        suggestions = {
            "hardcoded_secret": "Use environment variables or a secrets manager",
            "security_hazard": "Validate inputs and use safe alternatives",
            "sql_injection": "Use parameterized queries instead of f-strings",
            "print_statement": "Use a proper logging framework (logging module)",
            "debug_breakpoint": "Remove before committing",
            "mutable_default": "Use None as default and initialize inside the function",
            "bare_except": "Catch specific exceptions instead of all exceptions",
            "relative_import": "Use absolute imports for better clarity",
        }
        return suggestions.get(name, "")

    def review_diff(
        self,
        diff_text: str,
        repo_path: str = "",
    ) -> ReviewResult:
        """Review a git diff or code diff.

        Args:
            diff_text: Unified diff text.
            repo_path: Repository path for graph context.

        Returns:
            ReviewResult with issues, strengths, and summary.
        """
        result = ReviewResult()
        repo = repo_path or self.repo_path

        changed_files: set[str] = set()
        current_file = ""
        for line in diff_text.split("\n"):
            if line.startswith("diff --git "):
                parts = line.split(" ")
                if len(parts) >= 4:
                    b_path = parts[-1]
                    current_file = b_path[2:] if b_path.startswith("b/") else b_path
                    changed_files.add(current_file)
                    result.files_reviewed.append(current_file)
            elif line.startswith("+") and not line.startswith("+++") and current_file:
                result.total_lines += 1
                issues = self.run_static_analysis(current_file, line)
                for issue in issues:
                    result.issues.append(issue)

        # LLM-powered review
        llm_review = self._llm_review(diff_text, list(changed_files), repo)
        if llm_review:
            result.summary = llm_review.get("summary", "")
            result.risk_level = llm_review.get("risk_level", "low")
            result.strengths = llm_review.get("strengths", [])
            for issue_data in llm_review.get("issues", []):
                result.issues.append(ReviewIssue(**issue_data))

        # Deduplicate
        seen: set[str] = set()
        unique_issues: list[ReviewIssue] = []
        for issue in result.issues:
            key = f"{issue.file_path}:{issue.line}:{issue.message}"
            if key not in seen:
                seen.add(key)
                unique_issues.append(issue)
        result.issues = unique_issues

        severity_weights = {"error": 10, "warning": 5, "info": 1}
        total_penalty = sum(
            severity_weights.get(i.severity, 1) for i in result.issues
        )
        result.score = max(0, min(100, 100 - total_penalty))

        return result

    def review_code(
        self,
        code: str,
        language: str = "",
        file_path: str = "",
    ) -> ReviewResult:
        """Review a code snippet (not a diff).

        Args:
            code: Source code to review.
            language: Programming language hint.
            file_path: Optional file path for context.

        Returns:
            ReviewResult with issues and suggestions.
        """
        result = ReviewResult()
        if file_path:
            result.files_reviewed.append(file_path)

        lines = code.split("\n")
        result.total_lines = len(lines)

        issues = self.run_static_analysis(file_path or "snippet", code)
        result.issues.extend(issues)

        llm_review = self._llm_review(code, [file_path] if file_path else [], self.repo_path, is_diff=False)
        if llm_review:
            result.summary = llm_review.get("summary", "")
            result.risk_level = llm_review.get("risk_level", "low")
            result.strengths = llm_review.get("strengths", [])
            for issue_data in llm_review.get("issues", []):
                result.issues.append(ReviewIssue(**issue_data))

        severity_weights = {"error": 10, "warning": 5, "info": 1}
        total_penalty = sum(
            severity_weights.get(i.severity, 1) for i in result.issues
        )
        result.score = max(0, min(100, 100 - total_penalty))

        return result

    def _llm_review(
        self,
        text: str,
        changed_files: list[str],
        repo_path: str,
        is_diff: bool = True,
    ) -> dict[str, Any] | None:
        """Get LLM-powered review assessment."""
        try:
            from search.llm import get_llm
            llm = get_llm()
            if not llm.available:
                return None

            # Gather graph context
            context = ""
            if repo_path:
                try:
                    from graph.kuzu_store import KnowledgeGraph, get_default_db_path
                    db_path = get_default_db_path()
                    if db_path.exists():
                        kg = KnowledgeGraph(str(db_path))
                        related_nodes = kg.query(
                            "MATCH (n:Node) WHERE n.file_path IN $files "
                            "RETURN n.label, n.type, n.signature LIMIT 30",
                            {"files": changed_files},
                        )
                        if related_nodes:
                            context = "\n".join(
                                f"- {r['n.label']} ({r['n.type']}): {r['n.signature'][:80]}"
                                for r in related_nodes if r.get('n.label')
                            )
                        kg.close()
                except Exception:
                    pass

            files_str = "\n".join(f"  - {f}" for f in changed_files[:20])
            change_type = "diff" if is_diff else "code snippet"
            prompt = f"""Review this code {change_type} and provide structured feedback.

CHANGED FILES:
{files_str}

{change_type.upper()}:
{text[:4000]}

CODEBASE CONTEXT:
{context[:1500] if context else "(no graph context available)"}

Please provide:
1. Summary (2-3 sentences)
2. Risk level (low/medium/high/critical)
3. Specific issues found (if any) with severity (error/warning/info), line number, message, and suggestion
4. What's done well (strengths)

Respond in JSON format:
{{
  "summary": "...",
  "risk_level": "low",
  "issues": [{{"severity": "warning", "line": 0, "message": "...", "suggestion": "..."}}],
  "strengths": ["..."]
}}
"""
            response = llm.answer_question(prompt, context="")
            return self._parse_json_response(response)
        except Exception:
            return None

    @staticmethod
    def _parse_json_response(text: str) -> dict[str, Any] | None:
        """Parse JSON from LLM response (handles markdown code blocks)."""
        import json
        # Try to extract JSON from markdown code block
        m = re.search(r'```(?:json)?\s*\n({.*?})\s*\n```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # Try direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try to find JSON object in text
        m = re.search(r'\{.*"summary".*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None


def review_diff(diff_text: str, repo_path: str = "") -> dict[str, Any]:
    """Convenience: review a diff and return dict."""
    reviewer = CodeReviewer(repo_path)
    result = reviewer.review_diff(diff_text, repo_path)
    return {
        "issues": [
            {
                "severity": i.severity,
                "line": i.line,
                "message": i.message,
                "suggestion": i.suggestion,
                "rule_id": i.rule_id,
                "file_path": i.file_path,
            }
            for i in result.issues
        ],
        "strengths": result.strengths,
        "summary": result.summary,
        "risk_level": result.risk_level,
        "files_reviewed": result.files_reviewed,
        "total_lines": result.total_lines,
        "score": result.score,
    }


def review_code(code: str, language: str = "", file_path: str = "") -> dict[str, Any]:
    """Convenience: review code snippet and return dict."""
    reviewer = CodeReviewer()
    result = reviewer.review_code(code, language, file_path)
    return {
        "issues": [
            {
                "severity": i.severity,
                "line": i.line,
                "message": i.message,
                "suggestion": i.suggestion,
                "rule_id": i.rule_id,
                "file_path": i.file_path,
            }
            for i in result.issues
        ],
        "strengths": result.strengths,
        "summary": result.summary,
        "risk_level": result.risk_level,
        "score": result.score,
    }
