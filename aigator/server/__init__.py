"""Aigator local server and MCP integration."""

from aigator.server.daemon import run_daemon
from aigator.server.mcp import run_mcp_server

__all__ = ["run_daemon", "run_mcp_server"]
