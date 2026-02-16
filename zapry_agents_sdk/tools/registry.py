"""
ToolRegistry — 工具注册表、@tool 装饰器、JSON schema 自动生成。

LLM-agnostic：不绑定任何特定 LLM provider。
通过 to_json_schema() / to_openai_schema() 导出后可对接任意 LLM。
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Union,
    get_type_hints,
)

logger = logging.getLogger("zapry_agents_sdk.tools")

# ──────────────────────────────────────────────
# Type mapping: Python type → JSON Schema type
# ──────────────────────────────────────────────

_PY_TO_JSON_TYPE: Dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json(py_type: Any) -> str:
    """Convert a Python type annotation to a JSON Schema type string."""
    # Handle Optional, Union, etc.
    origin = getattr(py_type, "__origin__", None)
    if origin is Union:
        args = [a for a in py_type.__args__ if a is not type(None)]
        if args:
            return _python_type_to_json(args[0])
    return _PY_TO_JSON_TYPE.get(py_type, "string")


# ──────────────────────────────────────────────
# ToolContext
# ──────────────────────────────────────────────


@dataclass
class ToolContext:
    """Context passed to tool handlers during execution.

    Attributes:
        tool_name: Name of the tool being invoked.
        call_id: Optional caller-provided call ID (e.g. from OpenAI tool_call).
        extra: Arbitrary shared state.
    """

    tool_name: str = ""
    call_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────
# ToolParam
# ──────────────────────────────────────────────


@dataclass
class ToolParam:
    """Description of a single tool parameter."""

    name: str
    type: str  # JSON Schema type
    description: str = ""
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None


# ──────────────────────────────────────────────
# ToolDef
# ──────────────────────────────────────────────


@dataclass
class ToolDef:
    """A registered tool definition.

    Attributes:
        name: Unique tool name.
        description: Human-readable description (shown to LLM).
        parameters: List of parameter definitions.
        handler: The actual callable to execute.
        is_async: Whether the handler is async.
        raw_json_schema: Optional raw JSON Schema for parameters (used by MCP
            tools to preserve nested/oneOf/enum fidelity).
    """

    name: str
    description: str
    parameters: List[ToolParam] = field(default_factory=list)
    handler: Optional[Callable] = None
    is_async: bool = True
    raw_json_schema: Optional[Dict[str, Any]] = None

    def to_json_schema(self) -> Dict[str, Any]:
        """Export this tool as a JSON Schema object.

        If *raw_json_schema* is set (e.g. from MCP), it is used as the
        ``parameters`` value to preserve nested/oneOf/enum fidelity.
        Otherwise, parameters are built from :class:`ToolParam`.
        """
        if self.raw_json_schema is not None:
            return {
                "name": self.name,
                "description": self.description,
                "parameters": self.raw_json_schema,
            }

        properties: Dict[str, Any] = {}
        required: List[str] = []

        for p in self.parameters:
            prop: Dict[str, Any] = {"type": p.type}
            if p.description:
                prop["description"] = p.description
            if p.default is not None:
                prop["default"] = p.default
            if p.enum:
                prop["enum"] = p.enum
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        schema: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            schema["parameters"]["required"] = required

        return schema

    def to_openai_schema(self) -> Dict[str, Any]:
        """Export in OpenAI function calling format.

        Returns::

            {
                "type": "function",
                "function": { "name": ..., "description": ..., "parameters": ... }
            }
        """
        base = self.to_json_schema()
        return {
            "type": "function",
            "function": base,
        }


# ──────────────────────────────────────────────
# @tool decorator
# ──────────────────────────────────────────────


def _parse_docstring_args(docstring: str) -> Dict[str, str]:
    """Extract parameter descriptions from Google-style docstring Args section."""
    descriptions: Dict[str, str] = {}
    if not docstring:
        return descriptions

    lines = docstring.split("\n")
    in_args = False
    current_name = ""
    current_desc_parts: List[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped.lower().startswith("args:"):
            in_args = True
            continue

        if in_args:
            # New section header (Returns:, Raises:, etc.)
            if stripped and not stripped.startswith("-") and stripped.endswith(":") and ":" not in stripped[:-1]:
                # Save last param
                if current_name:
                    descriptions[current_name] = " ".join(current_desc_parts).strip()
                break

            # Param line: "name: description" or "name (type): description"
            if ":" in stripped and not stripped.startswith(" "):
                # Save previous
                if current_name:
                    descriptions[current_name] = " ".join(current_desc_parts).strip()

                name_part, _, desc_part = stripped.partition(":")
                # Handle "name (type)" format
                name_part = name_part.strip().split("(")[0].strip()
                current_name = name_part
                current_desc_parts = [desc_part.strip()]
            elif current_name and stripped:
                # Continuation line
                current_desc_parts.append(stripped)

    # Save last param
    if current_name:
        descriptions[current_name] = " ".join(current_desc_parts).strip()

    return descriptions


def _extract_tool_def(
    fn: Callable,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> ToolDef:
    """Build a ToolDef from a function's signature and docstring."""
    func_name = name or fn.__name__
    sig = inspect.signature(fn)

    # Get type hints (may fail on some edge cases)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    # Description from docstring
    docstring = inspect.getdoc(fn) or ""
    func_desc = description or ""
    if not func_desc and docstring:
        # First line of docstring as description
        func_desc = docstring.split("\n")[0].strip()

    # Parse Args from docstring
    arg_descs = _parse_docstring_args(docstring)

    # Build params
    params: List[ToolParam] = []
    for param_name, param in sig.parameters.items():
        # Skip 'self', 'cls', and ToolContext params
        if param_name in ("self", "cls"):
            continue
        param_type = hints.get(param_name, str)
        if param_type is ToolContext:
            continue

        has_default = param.default is not inspect.Parameter.empty
        params.append(
            ToolParam(
                name=param_name,
                type=_python_type_to_json(param_type),
                description=arg_descs.get(param_name, ""),
                required=not has_default,
                default=param.default if has_default else None,
            )
        )

    return ToolDef(
        name=func_name,
        description=func_desc,
        parameters=params,
        handler=fn,
        is_async=inspect.iscoroutinefunction(fn),
    )


def tool(
    fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Union[ToolDef, Callable[[Callable], ToolDef]]:
    """Decorator that turns a function into a :class:`ToolDef`.

    Can be used with or without arguments::

        @tool
        async def my_tool(x: str) -> str: ...

        @tool(name="custom_name", description="override desc")
        async def another(x: int) -> int: ...

    The resulting ``ToolDef`` can be registered with :class:`ToolRegistry`.
    """

    def decorator(func: Callable) -> ToolDef:
        return _extract_tool_def(func, name=name, description=description)

    if fn is not None:
        # @tool without parentheses
        return decorator(fn)
    # @tool(...) with arguments
    return decorator


# ──────────────────────────────────────────────
# ToolRegistry
# ──────────────────────────────────────────────


class ToolRegistry:
    """Central registry for tools.

    Manages tool registration, schema export, and execution dispatch.

    Usage::

        registry = ToolRegistry()

        @tool
        async def greet(name: str) -> str:
            return f"Hello {name}"

        registry.register(greet)
        schema = registry.to_json_schema()
        result = await registry.execute("greet", {"name": "World"})
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDef] = {}

    def register(self, tool_def: Union[ToolDef, Callable]) -> ToolDef:
        """Register a tool.

        Accepts a ``ToolDef`` (from ``@tool`` decorator) or a plain
        callable (will be auto-wrapped).
        """
        if not isinstance(tool_def, ToolDef):
            tool_def = _extract_tool_def(tool_def)
        if tool_def.name in self._tools:
            logger.warning("Tool %r already registered, overwriting", tool_def.name)
        self._tools[tool_def.name] = tool_def
        logger.debug("Tool registered: %s", tool_def.name)
        return tool_def

    def get(self, name: str) -> Optional[ToolDef]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list(self) -> List[ToolDef]:
        """Return all registered tools."""
        return list(self._tools.values())

    def names(self) -> List[str]:
        """Return all tool names."""
        return list(self._tools.keys())

    def remove(self, name: str) -> None:
        """Remove a tool by name."""
        self._tools.pop(name, None)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ─── Schema export ───

    def to_json_schema(self) -> List[Dict[str, Any]]:
        """Export all tools as a list of JSON Schema objects."""
        return [t.to_json_schema() for t in self._tools.values()]

    def to_openai_schema(self) -> List[Dict[str, Any]]:
        """Export all tools in OpenAI function calling format.

        Returns a list suitable for the ``tools`` parameter of
        ``openai.chat.completions.create()``.
        """
        return [t.to_openai_schema() for t in self._tools.values()]

    # ─── Execution ───

    async def execute(
        self,
        name: str,
        args: Optional[Dict[str, Any]] = None,
        ctx: Optional[ToolContext] = None,
    ) -> Any:
        """Execute a tool by name.

        Parameters:
            name: Tool name.
            args: Keyword arguments for the tool handler.
            ctx: Optional ToolContext (auto-created if not provided).

        Returns:
            The tool handler's return value.

        Raises:
            KeyError: If the tool is not registered.
            TypeError: If required arguments are missing.
        """
        tool_def = self._tools.get(name)
        if tool_def is None:
            raise KeyError(f"Tool not found: {name!r}")

        if tool_def.handler is None:
            raise RuntimeError(f"Tool {name!r} has no handler")

        if ctx is None:
            ctx = ToolContext(tool_name=name)
        else:
            ctx.tool_name = name

        call_args = dict(args or {})

        # Fill defaults for missing optional params
        for p in tool_def.parameters:
            if p.name not in call_args and not p.required and p.default is not None:
                call_args[p.name] = p.default

        # Check required params
        for p in tool_def.parameters:
            if p.required and p.name not in call_args:
                raise TypeError(
                    f"Tool {name!r} missing required argument: {p.name!r}"
                )

        # Check if handler accepts ToolContext
        sig = inspect.signature(tool_def.handler)
        handler_params = list(sig.parameters.keys())
        try:
            handler_hints = get_type_hints(tool_def.handler)
        except Exception:
            handler_hints = {}

        # Inject ctx if first param is ToolContext
        inject_ctx = False
        if handler_params:
            first_param = handler_params[0]
            if first_param not in ("self", "cls"):
                hint = handler_hints.get(first_param)
                if hint is ToolContext:
                    inject_ctx = True

        if tool_def.is_async:
            if inject_ctx:
                result = await tool_def.handler(ctx, **call_args)
            else:
                result = await tool_def.handler(**call_args)
        else:
            if inject_ctx:
                result = tool_def.handler(ctx, **call_args)
            else:
                result = tool_def.handler(**call_args)

        return result
