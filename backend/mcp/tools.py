"""
agent-toolkit compatible Tool base class with auto-registration.

All tools inherit from Tool. No manual registration needed —
__init_subclass__ automatically adds each tool to ToolRegistry.

Usage:
    class MyTool(Tool):
        name = "my_tool"
        description = "Does something useful."
        input_schema = {
            "type": "object",
            "properties": {"arg": {"type": "string"}},
            "required": ["arg"],
        }

        def execute(self, arg: str) -> dict:
            return {"result": arg}
"""

from __future__ import annotations


class ToolError(Exception):
    """Base error for tool failures. Subclass for specific error codes."""

    def __init__(self, message: str, code: str = "TOOL_ERROR") -> None:
        super().__init__(message)
        self.code = code


class ToolRegistry:
    """Holds all registered Tools. Tools are auto-added via __init_subclass__."""

    _tools: dict[str, type] = {}

    @classmethod
    def register(cls, tool_cls: type) -> None:
        """Register a tool class. Called automatically by __init_subclass__."""
        if not hasattr(tool_cls, "name") or not tool_cls.name:
            raise ValueError(f"Tool {tool_cls.__name__} must define 'name'")
        if tool_cls.name in cls._tools:
            raise ValueError(f"Tool '{tool_cls.name}' is already registered")
        cls._tools[tool_cls.name] = tool_cls

    @classmethod
    def list_tools(cls) -> list[dict]:
        """Return all registered tools as MCP-compatible schemas."""
        return [
            {
                "name": t.name,
                "description": t.description if hasattr(t, "description") else "",
                "inputSchema": getattr(t, "input_schema", {"type": "object", "properties": {}, "required": []}),
            }
            for t in cls._tools.values()
        ]

    @classmethod
    def get(cls, name: str) -> type | None:
        """Get a tool class by name."""
        return cls._tools.get(name)

    @classmethod
    def call(cls, name: str, arguments: dict) -> dict:
        """Instantiate and execute a tool by name."""
        tool_cls = cls.get(name)
        if tool_cls is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            instance = tool_cls()
            return instance.execute(**arguments)
        except TypeError as e:
            return {"error": f"Invalid arguments: {e}"}
        except ToolError as e:
            return {"error": e.args[0], "code": e.code}
        except Exception as e:
            return {"error": str(e)}


class AutoRegisteringToolMeta(type):
    """Metaclass that auto-registers tool subclasses."""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        # Only register concrete tool classes (skip base classes)
        if name != "Tool" and "Tool" in [b.__name__ for b in bases]:
            ToolRegistry.register(cls)
        return cls


class Tool(metaclass=AutoRegisteringToolMeta):
    """Base class for all tools. Subclass this and define name + execute()."""

    name: str = ""
    description: str = ""
    input_schema: dict = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> dict:
        """Override in subclasses. Must return a JSON-serializable dict."""
        raise NotImplementedError(f"{self.__class__.__name__}.execute() not implemented")
