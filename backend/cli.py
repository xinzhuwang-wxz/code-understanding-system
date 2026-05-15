"""CLI entry point for Code Understanding System.

Usage:
    code-kg mcp-config      Generate MCP config for Claude Code / Hermes Agent
    code-kg analyze <repo>  Analyze a repository
    code-kg search <query>  Search the knowledge graph
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _get_project_root() -> Path:
    """Find the project root (where this package is installed)."""
    return Path(__file__).resolve().parent.parent


def cmd_mcp_config() -> int:
    """Generate MCP server configuration for Claude Desktop / Codex / Hermes Agent.

    Auto-detects the installation path and outputs ready-to-paste JSON config.
    """
    project_root = _get_project_root()
    mcp_server = project_root / "scripts" / "mcp-server.sh"
    standalone_server = project_root / "backend" / "mcp" / "server_standalone.py"

    # Determine which server script to use
    if mcp_server.exists():
        server_cmd = str(mcp_server)
    elif standalone_server.exists():
        server_cmd = f"cd {project_root} && PYTHONPATH=backend python3 backend/mcp/server_standalone.py"
    else:
        print("Error: MCP server script not found.", file=sys.stderr)
        return 1

    config = {
        "mcpServers": {
            "code-kg": {
                "command": server_cmd,
            }
        }
    }

    # Also generate Hermes Agent config format
    hermes_config = {
        "mcp_servers": [
            {
                "name": "code-kg",
                "transport": "stdio",
                "command": server_cmd,
            }
        ]
    }

    print("=" * 60)
    print(" Claude Desktop / Codex / OpenClaw MCP Config")
    print("=" * 60)
    print()
    print("Add to ~/Library/Application Support/Claude/claude_desktop_config.json")
    print("or ~/.config/claude/claude_desktop_config.json")
    print()
    print(json.dumps(config, indent=2))
    print()
    print("=" * 60)
    print(" Hermes Agent MCP Config")
    print("=" * 60)
    print()
    print("Add to ~/.hermes/config.yaml under mcp_servers:")
    print()
    print(json.dumps(hermes_config, indent=2))
    print()

    # Try to auto-write if the file exists
    claude_paths = [
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        Path.home() / ".config" / "claude" / "claude_desktop_config.json",
    ]

    for p in claude_paths:
        if p.exists():
            print(f"Found existing config at {p}")
            print("To merge, run: code-kg mcp-config --merge")
            break
    else:
        print("No existing Claude Desktop config found.")
        print("Save the JSON above to one of the paths listed.")

    return 0


def cmd_analyze(repo_path: str, persist: bool = True) -> int:
    """Analyze a repository."""
    from analyzer.orchestrator_v2 import analyze_repo_universal

    path = Path(repo_path).resolve()
    if not path.is_dir():
        print(f"Error: {repo_path} is not a directory", file=sys.stderr)
        return 1

    print(f"Analyzing {path}...")
    import time
    t0 = time.time()
    result = analyze_repo_universal(str(path), persist=persist)
    elapsed = time.time() - t0

    kg_stats = result.get("kg_stats", {})
    print(f"✅ Done in {elapsed:.1f}s")
    print(f"   Symbols: {result.get('symbol_count', 0)}")
    print(f"   KG nodes: {kg_stats.get('nodes', 0)}")
    print(f"   KG edges: {kg_stats.get('edges', 0)}")
    return 0


def cmd_search(query: str, node_type: str = "function", max_results: int = 20) -> int:
    """Search the knowledge graph."""
    from search.engine import get_search_engine

    engine = get_search_engine()
    response = engine.search(query, node_type, max_results)

    print(f"Found {response.total_found} results in {response.total_latency_ms}ms")
    print(f"Layers: {response.layers_consulted}")
    print(f"Escalation: {response.escalation_path}")
    print()

    for i, r in enumerate(response.results, 1):
        print(f"{i}. {r.label} ({r.node_type})")
        print(f"   📁 {r.file_path}:{r.line_number}")
        if r.signature:
            print(f"   📝 {r.signature[:100]}")
        print(f"   🎯 score={r.score:.3f} [{r.source_layer}]")
        print()

    return 0


def main() -> int:
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        return 0

    cmd = sys.argv[1]

    if cmd == "mcp-config":
        return cmd_mcp_config()

    elif cmd == "analyze":
        if len(sys.argv) < 3:
            print("Usage: code-kg analyze <repo_path>", file=sys.stderr)
            return 1
        return cmd_analyze(sys.argv[2])

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: code-kg search <query> [--type function] [--limit 20]", file=sys.stderr)
            return 1
        node_type = "function"
        max_results = 20
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--type" and i + 1 < len(args):
                node_type = args[i + 1]
                i += 2
            elif args[i] == "--limit" and i + 1 < len(args):
                max_results = int(args[i + 1])
                i += 2
            else:
                i += 1
        query = " ".join(a for a in args if not a.startswith("--") and a not in (node_type, str(max_results)))
        return cmd_search(query, node_type, max_results)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
