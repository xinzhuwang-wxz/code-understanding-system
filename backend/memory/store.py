"""
Memory layer — episodic and semantic memory for code understanding.

Uses Mnemosyne-inspired patterns:
- Atomic writes (os.replace) for .agent-conventions.yaml
- Git-backed versioning for all memory files
- Supersede chain for convention evolution

Storage:
- ~/.code-kg/memory/  — all memory files
- ~/.code-kg/memory/conventions.yaml  — semantic memory (human-editable)
- ~/.code-kg/memory/episodes/  — episodic memory (auto-generated)
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _get_memory_dir() -> Path:
    """Get the memory directory, respecting CODE_KG_DATA env var."""
    code_kg_data = os.environ.get("CODE_KG_DATA")
    if code_kg_data:
        return Path(code_kg_data) / "memory"
    return Path.home() / ".code-kg" / "memory"


MEMORY_DIR = _get_memory_dir()
CONVENTIONS_FILE = MEMORY_DIR / "conventions.yaml"
EPISODES_DIR = MEMORY_DIR / "episodes"


def _ensure_dirs() -> None:
    """Ensure memory directories exist."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, content: str) -> None:
    """Atomic write via temp file + os.replace (Mnemosyne pattern)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_conventions(yaml_content: str) -> Path:
    """Save coding conventions to the semantic memory file.

    Uses atomic write to prevent partial writes.
    Old version is preserved via git history.
    """
    _ensure_dirs()
    atomic_write(CONVENTIONS_FILE, yaml_content)
    return CONVENTIONS_FILE


def load_conventions() -> str:
    """Load the current coding conventions."""
    if CONVENTIONS_FILE.exists():
        return CONVENTIONS_FILE.read_text(encoding="utf-8")
    return ""


def record_episode(
    event_type: str,
    description: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Record an episodic memory entry.

    Args:
        event_type: "analysis", "search", "change", "error"
        description: Human-readable description.
        metadata: Additional structured data.

    Returns:
        Path to the episode file.
    """
    _ensure_dirs()
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    episode_file = EPISODES_DIR / f"{date_str}.md"

    # Build entry
    entry = f"## {ts} | {event_type}\n\n{description}\n"
    if metadata:
        entry += f"\n```json\n{_safe_json(metadata)}\n```\n"
    entry += "\n---\n\n"

    # Append or create
    if episode_file.exists():
        content = episode_file.read_text(encoding="utf-8")
        # Insert after file header, before first entry
        if content.startswith("# "):
            lines = content.split("\n")
            # Find end of header (first blank line after title)
            insert_at = 0
            for i, line in enumerate(lines):
                if i > 0 and line.strip() == "" and insert_at == 0:
                    insert_at = i + 1
                    break
            if insert_at > 0:
                new_content = "\n".join(lines[:insert_at]) + "\n" + entry + "\n".join(lines[insert_at:])
                atomic_write(episode_file, new_content)
                return episode_file

        atomic_write(episode_file, content + entry)
    else:
        header = f"# Episodes — {date_str}\n\n"
        atomic_write(episode_file, header + entry)

    return episode_file


def compact_episodes(max_age_days: int = 30) -> int:
    """Compact old episodic memories (Mnemosyne pattern).

    Archives episodes older than max_age_days into summary files.

    Returns:
        Number of episodes compacted.
    """
    _ensure_dirs()
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
    compacted = 0

    for f in sorted(EPISODES_DIR.glob("*.md")):
        if f.stat().st_mtime < cutoff:
            # Rename to archive
            archive_name = f.stem + ".archived.md"
            f.rename(EPISODES_DIR / archive_name)
            compacted += 1

    return compacted


def get_recent_episodes(days: int = 7) -> list[dict]:
    """Get recent episodic memories.

    Returns:
        List of {"date": str, "type": str, "description": str, "metadata": dict}
    """
    _ensure_dirs()
    episodes: list[dict] = []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

    for f in sorted(EPISODES_DIR.glob("*.md"), reverse=True):
        if f.stat().st_mtime < cutoff:
            continue
        content = f.read_text(encoding="utf-8")
        # Parse entries
        for entry in content.split("---"):
            entry = entry.strip()
            if not entry or entry.startswith("# "):
                continue
            lines = entry.strip().split("\n")
            if len(lines) >= 2:
                header = lines[0].strip("# ")
                parts = header.split("|")
                episodes.append({
                    "date": parts[0].strip() if parts else "",
                    "type": parts[1].strip() if len(parts) > 1 else "",
                    "description": lines[1].strip() if len(lines) > 1 else "",
                })

    return episodes[:50]  # Limit to 50 most recent


def _safe_json(obj: dict) -> str:
    """Safe JSON serialization."""
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ─── Convenience API ──────────────────────────────────────────────


def remember_analysis(repo_path: str, node_count: int, edge_count: int) -> None:
    """Record an analysis event in episodic memory."""
    record_episode(
        "analysis",
        f"Analyzed {Path(repo_path).name}: {node_count} nodes, {edge_count} edges",
        {"repo": repo_path, "nodes": node_count, "edges": edge_count},
    )


def remember_search(query: str, result_count: int) -> None:
    """Record a search event."""
    record_episode(
        "search",
        f"Search '{query[:80]}': {result_count} results",
        {"query": query, "results": result_count},
    )


def remember_change(file_path: str, description: str) -> None:
    """Record a code change event."""
    record_episode(
        "change",
        f"Changed {file_path}: {description}",
        {"file": file_path, "description": description},
    )
