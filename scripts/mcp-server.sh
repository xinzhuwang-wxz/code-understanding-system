#!/usr/bin/env bash
# CodeKG MCP Server — Crash-Restart Wrapper
#
# Pattern: Peekaboo/agent-toolkit crash-backoff wrapper
# - Restarts on crash with exponential backoff
# - Caps restarts at 5 per 60 seconds
# - Forwards signals for graceful shutdown
#
# Usage (Claude Code / MCP config):
#   {
#     "mcpServers": {
#       "code-kg": {
#         "command": "/path/to/code-understanding-system/scripts/mcp-server.sh"
#       }
#     }
#   }

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MAX_RESTARTS=5
RESTART_WINDOW_SEC=60
INITIAL_DELAY_SEC=1
MAX_DELAY_SEC=30

# Activate virtual environment
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
fi

RESTART_TIMESTAMPS=()
DELAY=$INITIAL_DELAY_SEC
SHUTTING_DOWN=false

cleanup() {
    SHUTTING_DOWN=true
    if [ -n "${CHILD_PID:-}" ]; then
        kill -0 "$CHILD_PID" 2>/dev/null && kill "$CHILD_PID" 2>/dev/null || true
        wait "$CHILD_PID" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup SIGINT SIGTERM

start_server() {
    local now
    now=$(date +%s)
    local new_timestamps=()
    for ts in "${RESTART_TIMESTAMPS[@]}"; do
        if (( now - ts < RESTART_WINDOW_SEC )); then
            new_timestamps+=("$ts")
        fi
    done
    RESTART_TIMESTAMPS=("${new_timestamps[@]}")

    if (( ${#RESTART_TIMESTAMPS[@]} >= MAX_RESTARTS )); then
        echo "[code-kg MCP] Aborting: $MAX_RESTARTS restarts in ${RESTART_WINDOW_SEC}s." >&2
        exit 1
    fi

    echo "[code-kg MCP] Starting server..." >&2
    PYTHONPATH="$PROJECT_DIR/backend" python3 -u backend/mcp/server_standalone.py &
    CHILD_PID=$!
    wait "$CHILD_PID" || true
    EXIT_CODE=$?

    if $SHUTTING_DOWN; then
        exit 0
    fi

    # Clean exits — don't restart
    if [ "$EXIT_CODE" -eq 0 ] || [ "$EXIT_CODE" -eq 130 ] || [ "$EXIT_CODE" -eq 143 ]; then
        echo "[code-kg MCP] Server exited cleanly (code $EXIT_CODE)" >&2
        exit "$EXIT_CODE"
    fi

    # Crash — restart with backoff
    echo "[code-kg MCP] Server crashed (code $EXIT_CODE). Restarting in ${DELAY}s..." >&2
    RESTART_TIMESTAMPS+=("$(date +%s)")
    sleep "$DELAY"
    DELAY=$(( DELAY * 2 > MAX_DELAY_SEC ? MAX_DELAY_SEC : DELAY * 2 ))
}

while ! $SHUTTING_DOWN; do
    start_server
done
