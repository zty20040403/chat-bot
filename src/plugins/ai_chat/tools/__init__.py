"""Tool capability boundary.

The existing tool catalog and executors remain source-compatible while callers
migrate behind this package boundary.
"""

from ..ai_tools import ToolDefinition, available_tools

__all__ = ["ToolDefinition", "available_tools"]
