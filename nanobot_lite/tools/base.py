"""Base tool class and tool registry."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    """Result returned by a tool execution."""
    content: str
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class Tool:
    """
    A tool that the agent can use.

    Each tool has:
    - name: unique identifier
    - description: human-readable description for the LLM
    - parameters: JSON schema for the tool's parameters
      (pass as input_schema= keyword for compat with existing tools)
    - handler: async function that executes the tool
    """
    name: str
    description: str
    input_schema: dict[str, Any] | None = None  # alias — tools pass input_schema=
    handler: Callable[..., Any] | None = None   # type: ignore
    enabled: bool = True

    def __post_init__(self) -> None:
        # Bridge: tools pass input_schema=, we store as parameters
        # NOTE: this field is named 'parameters' in to_schema() output for OpenAI compat
        self.parameters: dict[str, Any] = self.input_schema or {}
        if self.handler is None and hasattr(self, "execute"):
            self.handler = self.execute  # type: ignore

    def to_schema(self) -> dict[str, Any]:
        """Return the tool schema for the LLM."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def validate_arguments(self, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Validate arguments against the schema. Returns (valid, error_message)."""
        # Simple type validation
        props = self.parameters.get("properties", {})
        required = self.parameters.get("required", [])

        for key in required:
            if key not in arguments:
                return False, f"Missing required parameter: {key}"

        for key, value in arguments.items():
            if key not in props:
                return False, f"Unknown parameter: {key}"

            expected_type = props[key].get("type")
            if expected_type:
                if expected_type == "string" and not isinstance(value, str):
                    return False, f"{key} must be a string"
                elif expected_type == "integer" and not isinstance(value, int):
                    return False, f"{key} must be an integer"
                elif expected_type == "number" and not isinstance(value, (int, float)):
                    return False, f"{key} must be a number"
                elif expected_type == "boolean" and not isinstance(value, bool):
                    return False, f"{key} must be a boolean"
                elif expected_type == "array" and not isinstance(value, list):
                    return False, f"{key} must be an array"
                elif expected_type == "object" and not isinstance(value, dict):
                    return False, f"{key} must be an object"

        return True, ""


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._context: dict[str, Any] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_all(self) -> list[Tool]:
        """Get all enabled tools."""
        return [t for t in self._tools.values() if t.enabled]

    def get_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas for the LLM."""
        return [t.to_schema() for t in self.get_all()]

    def set_context(self, **kwargs) -> None:
        """Set context data available to all tools."""
        self._context.update(kwargs)

    def get_context(self, key: str, default: Any = None) -> Any:
        """Get context data."""
        return self._context.get(key, default)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self):
        return len(self._tools)


# Global registry instance
_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return _registry


def register_tool(name: str, description: str, parameters: dict[str, Any]) -> Callable:
    """Decorator to register a tool."""
    def decorator(func: Callable) -> Callable:
        tool = Tool(
            name=name,
            description=description,
            input_schema=parameters,  # bridge: Tool stores as 'parameters' in __post_init__
            handler=func,
        )
        _registry.register(tool)
        return func
    return decorator
