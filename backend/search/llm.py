"""
LLM integration — DeepSeek API for code explanation, impact analysis,
and convention summarization.

All LLM calls in CodeKG use DeepSeek API exclusively.
Configure via DEEPSEEK_API_KEY environment variable.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class LLMClient:
    """Lightweight DeepSeek API client for code understanding tasks."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
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
            print(f"  ⚠ DeepSeek API error: {e}")
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
            {"role": "system", "content": """Extract coding conventions from code samples. Output valid YAML:
conventions:
  <language>:
    naming:
      <category>: <pattern>
    structure:
      <category>: <pattern>
    anti_patterns:
      - "<pattern>"
Be concise. Only report conventions you can clearly observe."""},
            {"role": "user", "content": f"Analyze these code samples:\n{samples_text}"},
        ], max_tokens=1024, temperature=0.2)

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
