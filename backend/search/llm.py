"""
LLM integration — DeepSeek API for code explanation, impact analysis,
and convention summarization.

All LLM calls in CodeKG use DeepSeek API exclusively.
Configure via DEEPSEEK_API_KEY environment variable.
"""

from __future__ import annotations

from log import get_logger; logger = get_logger(__name__)

import json
import os
import time
from pathlib import Path
from typing import Any


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-coder")


class LLMClient:
    """Lightweight DeepSeek API client for code understanding tasks."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        # Always try loading from project .env — it overrides env var
        _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if _env_path.exists():
            with open(_env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        if k.strip() == "DEEPSEEK_API_KEY":
                            env_val = v.strip().strip('"').strip("'")
                            # .env trumps stale/placeholder env var
                            if env_val and env_val != "sk-your-key-here":
                                self.api_key = env_val
                            break
        self.model = model
        self._base_url = DEEPSEEK_BASE_URL

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str:
        """Send a chat completion request to DeepSeek.

        Args:
            messages: List of {"role": "user"|"system"|"assistant", "content": "..."}
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature (0-1).

        Returns:
            Response text, or empty string on failure.
        """
        if not self.available:
            return ""

        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"  ⚠ DeepSeek API error: {e}")
            return ""

    def explain_code(
        self, symbol_name: str, signature: str, docstring: str,
        relations: list[str] | None = None,
    ) -> str:
        """Explain what a code symbol does and how it connects."""
        if not self.available:
            return self._fallback_explain(symbol_name, signature, docstring)

        ctx = f"Function: {signature}\n"
        if docstring:
            ctx += f"Docstring: {docstring}\n"
        if relations:
            ctx += f"Relations: {', '.join(relations)}\n"

        return self.chat([
            {"role": "system", "content": "You are a code explanation assistant. Explain code concisely in 2-3 sentences. Focus on purpose and connections."},
            {"role": "user", "content": f"Explain this code:\n{ctx}"},
        ], max_tokens=256)

    def summarize_impact(self, node_id: str, affected_count: int) -> str:
        """Summarize the impact of modifying a code entity."""
        if not self.available:
            return f"Modifying {node_id} may affect {affected_count} files."

        return self.chat([
            {"role": "system", "content": "Summarize code change impact in one sentence."},
            {"role": "user", "content": f"Changing '{node_id}' affects {affected_count} dependent files. Summarize."},
        ], max_tokens=128)

    def extract_conventions(self, code_samples: list[dict]) -> str:
        """Extract coding conventions from code samples.

        Args:
            code_samples: List of {"name": str, "content": str, "language": str}

        Returns:
            YAML-formatted conventions string.
        """
        if not self.available:
            return ""

        samples_text = "\n\n".join(
            f"// {s['name']} ({s['language']})\n{s['content'][:500]}"
            for s in code_samples[:10]
        )

        return self.chat([
            {"role": "system", "content": """You are analyzing code samples to extract coding conventions for AI agents. Output valid YAML only.

Rules:
1. Name conventions by language: python, javascript, typescript, etc.
2. For each language, report OBSERVABLE patterns only — not guesses
3. For naming: report the actual case used (snake_case, camelCase, PascalCase)
4. For structure: report directory patterns, file naming conventions
5. Anti-patterns: code smells or deprecated patterns you notice
6. Use full, clear names — never abbreviate (e.g., "tensor" not "tenso")
7. If you see less than 3 samples of a pattern, mark it as tentative
8. Keep descriptions concise — one line per observation

Format:
conventions:
  python:
    naming:
      functions: "snake_case (e.g., train_model)"
      classes: "PascalCase (e.g., ModelTrainer)"
      constants: "UPPER_SNAKE_CASE (e.g., MAX_EPOCHS)"
    structure:
      models: "src/models/<name>.py"
      tests: "tests/test_<module>.py"
    anti_patterns:
      - "Avoid wildcard imports (from X import *)"
    architecture:
      state_management: "Uses PyTorch Lightning for training orchestration"
  javascript:
    naming:
      components: "PascalCase (e.g., UserProfile)"
      hooks: "camelCase with useXxx prefix (e.g., useAuth)"
"""},
            {"role": "user", "content": f"Analyze these code samples:\n{samples_text}"},
        ], max_tokens=1024, temperature=0.2)

    def summarize_diff(self, diff_text: str, changed_files: list[str]) -> str:
        """Summarize a git diff and its impact on the codebase."""
        if not self.available:
            return f"Diff changes {len(changed_files)} files."

        files_text = "\n".join(f"- {f}" for f in changed_files[:20])
        return self.chat([
            {"role": "system", "content": "Summarize this code diff and its impact in 2-3 sentences. Focus on what changed and why it matters."},
            {"role": "user", "content": f"Files changed:\n{files_text}\n\nDiff:\n{diff_text[:3000]}"},
        ], max_tokens=256)

    def answer_question(self, question: str, context: str) -> str:
        """Answer a natural language question about the codebase."""
        if not self.available:
            return f"LLM unavailable. Context: {context[:500]}"

        return self.chat([
            {"role": "system", "content": "Answer questions about a codebase using the provided context. Be concise and specific."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ], max_tokens=512)

    @staticmethod
    def _fallback_explain(name: str, signature: str, docstring: str) -> str:
        """Fallback explanation without LLM."""
        parts = [f"**{name}**"]
        if docstring:
            parts.append(f"\n{docstring[:200]}")
        parts.append(f"\n`{signature}`")
        return "".join(parts)


# Global singleton
_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    """Get or create the global LLM client."""
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
