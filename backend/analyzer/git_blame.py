"""
Git blame data extraction for code knowledge graph enrichment.

Extracts author/date info per line to populate the git_blame Node field.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def get_git_blame_for_file(file_path: str, repo_root: str) -> dict[str, str]:
    """Get git blame per line for a file.

    Args:
        file_path: Path to the file (relative to repo_root).
        repo_root: Root of the git repository.

    Returns:
        Dict mapping "L{start}-{end}" → "author <date>" for each blame block,
        plus a whole-file summary under key "*".
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "blame", "--line-porcelain", file_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}

    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.split("\n"):
        if line.startswith("\t"):
            continue  # Actual code line, skip
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        key, _, val = line.partition(" ")
        if key in ("author", "author-mail", "author-time", "summary"):
            current[key] = val

    if current:
        blocks.append(current)

    if not blocks:
        return {}

    # Deduplicate consecutive blocks with same author
    blame_lines: list[str] = []
    prev_author = ""
    start_line = 1
    for i, block in enumerate(blocks):
        author = block.get("author", "unknown")
        if author != prev_author and blame_lines:
            blame_lines.append(f"L{start_line}-{i}: {prev_author}")
            start_line = i + 1
            prev_author = author
        elif not blame_lines:
            prev_author = author

    if prev_author:
        blame_lines.append(f"L{start_line}-{len(blocks)}: {prev_author}")

    return {"*": "; ".join(blame_lines[:6])}  # Limit to 6 blocks to stay compact


def enrich_nodes_with_blame(
    nodes: list[dict[str, Any]],
    repo_root: str,
    max_files: int = 50,
) -> list[dict[str, Any]]:
    """Add git blame data to nodes.

    Only processes up to max_files unique files to keep analysis fast.
    """
    if not repo_root or not Path(repo_root).is_dir():
        return nodes

    # Check if this is a git repo
    git_dir = Path(repo_root) / ".git"
    if not git_dir.exists():
        return nodes

    # Group nodes by file
    file_blames: dict[str, dict[str, str]] = {}
    files_seen = 0

    for node in nodes:
        file_path = node.get("file_path", "")
        if not file_path or file_path in file_blames:
            continue
        if files_seen >= max_files:
            break
        blame_data = get_git_blame_for_file(file_path, repo_root)
        if blame_data:
            file_blames[file_path] = blame_data
        files_seen += 1

    # Attach blame to nodes
    for node in nodes:
        file_path = node.get("file_path", "")
        blame = file_blames.get(file_path, {})
        if blame:
            node["git_blame"] = blame.get("*", "")

    return nodes
