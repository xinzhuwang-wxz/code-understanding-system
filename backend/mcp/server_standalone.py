"""
from log import get_logger; logger = get_logger(__name__)
Self-contained MCP Server — stdio JSON-RPC 2.0, class-based Tool architecture.

Uses agent-toolkit compatible pattern: Tool base class with __init_subclass__
auto-registration via ToolRegistry. All tools are in backend/mcp/tool_impls.py.

Compatible with Claude Code, Codex, OpenClaw, Hermes Agent, and any
MCP-compliant client.

Usage:
    python3 -m mcp.server_standalone
    PYTHONPATH=backend python3 backend/mcp/server_standalone.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure backend in PYTHONPATH
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# ─── Load all tools (auto-registered via __init_subclass__) ─────────

from mcp.tools import ToolRegistry  # noqa: E402
import mcp.tool_impls  # noqa: E402, F401 — triggers Tool registration


# ─── JSON-RPC 2.0 Server ───────────────────────────────────────────

def _send_response(id_val: Any, result: Any) -> None:
    """Send a JSON-RPC success response to stdout."""
    response = json.dumps({
        "jsonrpc": "2.0",
        "id": id_val,
        "result": result,
    }, ensure_ascii=False, default=str)
    sys.stdout.write(response + "\n")
    sys.stdout.flush()


def _send_error(id_val: Any, code: int, message: str) -> None:
    """Send a JSON-RPC error response."""
    response = json.dumps({
        "jsonrpc": "2.0",
        "id": id_val,
        "error": {"code": code, "message": message},
    }, ensure_ascii=False)
    sys.stdout.write(response + "\n")
    sys.stdout.flush()


def main() -> None:
    """Main MCP server loop — reads JSON-RPC from stdin, writes to stdout."""
    tool_count = len(ToolRegistry._tools)
    print(f"[code-kg MCP] Server starting on stdio... ({tool_count} tools registered)", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        try:
            if method == "tools/list":
                _send_response(req_id, {"tools": ToolRegistry.list_tools()})
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                result = ToolRegistry.call(tool_name, tool_args)
                _send_response(req_id, {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                })
            elif method == "initialize":
                _send_response(req_id, {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "code-kg", "version": "0.3.0"},
                    "capabilities": {"tools": {}},
                })
            elif method == "notifications/initialized":
                pass  # No response needed for notifications
            else:
                _send_error(req_id, -32601, f"Method not found: {method}")
        except Exception as e:
            _send_error(req_id, -32603, str(e))
            print(f"[code-kg MCP] Error: {e}", file=sys.stderr, flush=True)

    print("[code-kg MCP] Server stopped.", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
