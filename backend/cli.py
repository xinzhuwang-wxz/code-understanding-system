"""CLI for CodeKG — Human + Agent friendly codebase understanding.

Usage:
    code-kg analyze /path/to/repo
    code-kg search "JWT middleware" --semantic
    code-kg explain <node_id>
    code-kg conventions --export
    code-kg diff /path/to/repo
    code-kg impact --file auth.ts --lines 42-89
    code-kg status
    code-kg mcp-config
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import click


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_url() -> str:
    """Return the server URL from CODE_KG_URL env or the default."""
    return os.environ.get("CODE_KG_URL", "http://localhost:8765")


def _api_request(
    method: str,
    path: str,
    data: Optional[dict[str, Any]] = None,
    url: str = "",
) -> dict[str, Any]:
    """Make an HTTP request to the CodeKG API and return parsed JSON."""
    if not url:
        url = _default_url()
    full_url = f"{url.rstrip('/')}{path}"

    body: Optional[bytes] = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(
        full_url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        raise click.ClickException(
            f"API error {e.code}: {detail or e.reason}"
        ) from e
    except urllib.error.URLError as e:
        raise click.ClickException(
            f"Cannot connect to {url}. Is the server running?\n  {e.reason}"
        ) from e


def _format_output(data: Any, pretty: bool) -> str:
    """Format data as JSON. When pretty=True, human-readable with indentation."""
    if pretty:
        return json.dumps(data, indent=2, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)


def _print_output(data: Any, pretty: bool) -> None:
    """Print output data as JSON to stdout."""
    click.echo(_format_output(data, pretty))


# ---------------------------------------------------------------------------
# Common options / decorators
# ---------------------------------------------------------------------------

def _url_option(f):
    """Decorator adding --url option to a command."""
    return click.option(
        "--url",
        default=_default_url(),
        show_default=True,
        envvar="CODE_KG_URL",
        help="CodeKG server URL.",
    )(f)


def _pretty_option(f):
    """Decorator adding --pretty flag to a command."""
    return click.option(
        "--pretty/--no-pretty",
        default=False,
        help="Human-readable indented output.",
    )(f)


def _common_options(f):
    """Combined --url and --pretty options."""
    f = _url_option(f)
    f = _pretty_option(f)
    return f


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.3.0", prog_name="code-kg")
def main() -> None:
    """CodeKG — Understand any codebase. For humans and AI agents."""


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--method", default="tree-sitter", type=click.Choice(["tree-sitter", "original"]),
              help="Analysis method.")
@_common_options
def analyze(repo_path: str, method: str, url: str, pretty: bool) -> None:
    """Analyze a repository and build the knowledge graph.

    REPO_PATH: Path to the repository to analyze.
    """
    click.echo(f"Analyzing {repo_path} ...", err=True)
    result = _api_request("POST", "/api/analyze",
                          data={"repo_path": repo_path, "method": method}, url=url)
    _print_output(result, pretty)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@main.command()
@click.argument("query", required=False)
@click.option("--query", "-q", "query_opt", help="Search query.")
@click.option("--type", "-t", "node_type", default="function",
              help="Node type filter (function, class, method, module, etc.).")
@click.option("--limit", "-n", "max_results", default=20, type=int,
              help="Maximum results.")
@_common_options
def search(query: Optional[str], query_opt: Optional[str], node_type: str,
           max_results: int, url: str, pretty: bool) -> None:
    """Search the knowledge graph for code symbols by pattern.

    QUERY: Keyword or regex pattern to search for.
    """
    q = query or query_opt
    if not q:
        raise click.UsageError("A search query is required.")
    result = _api_request("POST", "/api/search",
                          data={"query": q, "node_type": node_type,
                                "max_results": max_results}, url=url)
    _print_output(result, pretty)


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------

@main.command()
@click.argument("node_id")
@_common_options
def explain(node_id: str, url: str, pretty: bool) -> None:
    """Get an LLM-powered explanation of a code symbol.

    NODE_ID: The ID of the node to explain (from search results).
    """
    result = _api_request("POST", "/api/explain",
                          data={"node_id": node_id}, url=url)
    _print_output(result, pretty)


# ---------------------------------------------------------------------------
# conventions
# ---------------------------------------------------------------------------

@main.command()
@click.option("--repo-path", "-r", default="", help="Repository path for auto-generation.")
@click.option("--export", "-o", "export_path", default=None, type=click.Path(),
              help="Export conventions to a YAML file.")
@_common_options
def conventions(repo_path: str, export_path: Optional[str], url: str, pretty: bool) -> None:
    """Get, generate, or export coding conventions.

    If conventions exist, returns them. Otherwise auto-generates from
    analyzed code. Use --export to save to a file.
    """
    result = _api_request("POST", "/api/conventions",
                          data={"repo_path": repo_path}, url=url)

    if export_path:
        conventions_text = result.get("conventions", "")
        if conventions_text:
            Path(export_path).write_text(conventions_text, encoding="utf-8")
            click.echo(f"Conventions exported to {export_path}", err=True)
        else:
            click.echo("No conventions to export.", err=True)

    _print_output(result, pretty)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--commit-range", "-c", default="HEAD~1..HEAD",
              help="Git commit range to diff (e.g., HEAD~3..HEAD, main..feature).")
@_common_options
def diff(repo_path: str, commit_range: str, url: str, pretty: bool) -> None:
    """Analyze a git diff and assess impact.

    REPO_PATH: Path to the git repository.
    """
    result = _api_request("POST", "/api/diff",
                          data={"repo_path": repo_path,
                                "commit_range": commit_range}, url=url)
    _print_output(result, pretty)


# ---------------------------------------------------------------------------
# impact
# ---------------------------------------------------------------------------

@main.command()
@click.option("--node-id", "-n", default="", help="Entity node ID for impact analysis.")
@click.option("--repo-path", "-r", type=click.Path(exists=True, file_okay=False, resolve_path=True),
              default="", help="Repository path for git diff impact analysis.")
@click.option("--commit-range", "-c", default="HEAD~1..HEAD",
              help="Git commit range (with --repo-path).")
@_common_options
def impact(node_id: str, repo_path: str, commit_range: str, url: str, pretty: bool) -> None:
    """Analyze impact of a change — entity-level or git diff.

    Use --node-id for entity impact (from search results).
    Use --repo-path for git diff impact analysis.

    Examples:
        code-kg impact --node-id func_authMiddleware
        code-kg impact --repo-path . --commit-range HEAD~3..HEAD
    """
    if not node_id and not repo_path:
        raise click.UsageError("Provide either --node-id or --repo-path.")

    data: dict[str, Any] = {}
    if node_id:
        data["node_id"] = node_id
    if repo_path:
        data["repo_path"] = repo_path
        data["commit_range"] = commit_range

    result = _api_request("POST", "/api/impact", data=data, url=url)
    _print_output(result, pretty)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@main.command()
@_common_options
def status(url: str, pretty: bool) -> None:
    """Check server health and knowledge graph statistics."""
    result = _api_request("GET", "/api/status", url=url)
    _print_output(result, pretty)


# ---------------------------------------------------------------------------
# mcp-config
# ---------------------------------------------------------------------------

def _get_project_root() -> Path:
    """Find the project root (where this package is installed)."""
    return Path(__file__).resolve().parent.parent


@main.command()
@click.option("--merge", is_flag=True, default=False,
              help="Attempt to merge into an existing Claude Desktop config.")
@click.option("--format", "output_format", type=click.Choice(["claude", "hermes", "all"]),
              default="all", help="Config format to output.")
@click.option("--pretty", "pretty_flag", is_flag=True, default=False,
              help="Pretty-print JSON output.")
def mcp_config(merge: bool, output_format: str, pretty_flag: bool) -> None:
    """Generate MCP server configuration for Claude Desktop / Codex / Hermes Agent.

    Outputs ready-to-paste JSON for configuring the CodeKG MCP server
    in your AI agent's MCP settings.
    """
    project_root = _get_project_root()
    mcp_server_script = project_root / "scripts" / "mcp-server.sh"
    standalone_server = project_root / "backend" / "mcp" / "server_standalone.py"

    # Determine the server command
    if mcp_server_script.exists():
        server_cmd = str(mcp_server_script)
    elif standalone_server.exists():
        server_cmd = (
            f"cd {project_root} && "
            f"PYTHONPATH=backend python3 backend/mcp/server_standalone.py"
        )
    else:
        raise click.ClickException(
            "MCP server script not found. "
            "Expected scripts/mcp-server.sh or backend/mcp/server_standalone.py"
        )

    claude_config = {
        "mcpServers": {
            "code-kg": {
                "command": server_cmd,
            }
        }
    }

    hermes_config = {
        "mcp_servers": [
            {
                "name": "code-kg",
                "transport": "stdio",
                "command": server_cmd,
            }
        ]
    }

    indent = 2 if pretty_flag else None

    if output_format in ("claude", "all"):
        if output_format == "all":
            click.echo("=" * 60)
            click.echo(" Claude Desktop / Codex / OpenClaw MCP Config")
            click.echo("=" * 60)
            click.echo()
            click.echo("Add to ~/Library/Application Support/Claude/claude_desktop_config.json")
            click.echo("or ~/.config/claude/claude_desktop_config.json")
            click.echo()
        click.echo(json.dumps(claude_config, indent=indent))
        if output_format == "all":
            click.echo()

    if output_format in ("hermes", "all"):
        if output_format == "all":
            click.echo("=" * 60)
            click.echo(" Hermes Agent MCP Config")
            click.echo("=" * 60)
            click.echo()
            click.echo("Add to ~/.hermes/config.yaml under mcp_servers:")
            click.echo()
        click.echo(json.dumps(hermes_config, indent=indent))
        if output_format == "all":
            click.echo()

    # Auto-detect existing Claude config for merge hint
    if output_format == "all" or merge:
        claude_paths = [
            Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            Path.home() / ".config" / "claude" / "claude_desktop_config.json",
        ]
        found = False
        for p in claude_paths:
            if p.exists():
                click.echo(f"Found existing config at {p}", err=True)
                found = True
                if merge:
                    click.echo("Merge not yet implemented. "
                               "Manually add the 'code-kg' entry to your config.", err=True)
                else:
                    click.echo("To merge, run: code-kg mcp-config --merge", err=True)
                break
        if not found:
            click.echo("No existing Claude Desktop config found.", err=True)
            click.echo("Save the JSON above to one of the paths listed.", err=True)


# ---------------------------------------------------------------------------
# Entry point (for pyproject.toml [project.scripts])
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
