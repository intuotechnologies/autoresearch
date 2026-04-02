#!/usr/bin/env bash
# Avvia il server MCP autoresearch.
# Si auto-localizza via BASH_SOURCE — funziona da qualsiasi cwd.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run "$DIR/mcp/server.py"
