"""The agent-facing surfaces have to describe themselves, arguments included.

The MCP tools and the control commands they sit on have no documentation
anywhere but their own docstrings -- no README, no schema, nothing a caller can
read instead. An undescribed argument fails in the worst place available: an
agent calling the tool wrongly, in a session with no human watching.

For an MCP tool the arguments are the typed parameters. For a control command
they are *the JSON keys the handler reads out of the request* -- the parameter
is called `request` and documenting that name would say nothing, so the keys are
found by walking the handler for subscripts and .get() calls on it.

A surface that matches nothing is reported as a failure rather than passing
vacuously: a renamed decorator would otherwise turn this check off silently.

Run alone:  python -m scripts.lint.agent_docs
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

from scripts.lint.common import ROOT, Finding, parse, report

CHECK = "agent-docs"


@dataclass(frozen=True)
class Subject:
    """One documented thing: where it is, what it says, what it takes."""

    name: str
    line: int
    doc: str | None
    arguments: tuple[str, ...]


def control_commands(tree: ast.Module) -> list[Subject]:
    """Methods named cmd_* on the control server, with the request keys they read."""
    return [
        Subject(node.name, node.lineno, ast.get_docstring(node), request_keys(node))
        for klass in tree.body
        if isinstance(klass, ast.ClassDef)
        for node in klass.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_")
    ]


def request_keys(handler: ast.FunctionDef) -> tuple[str, ...]:
    """The JSON keys a handler reads: request["path"], request.get("enabled")."""
    if len(handler.args.args) < 2:
        return ()
    request = handler.args.args[1].arg
    keys: list[str] = []
    for node in ast.walk(handler):
        target = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            target, key = node.value, node.slice.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            target, key = node.func.value, node.args[0].value
        if isinstance(target, ast.Name) and target.id == request and isinstance(key, str):
            keys.append(key)
    return tuple(dict.fromkeys(keys))


def mcp_tools(tree: ast.Module) -> list[Subject]:
    """Functions carrying @server.tool(), with their typed parameters."""
    return [
        Subject(node.name, node.lineno, ast.get_docstring(node),
                tuple(arg.arg for arg in node.args.args))
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and any(is_tool(d) for d in node.decorator_list)
    ]


def is_tool(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Attribute) and target.attr == "tool"


@dataclass(frozen=True)
class Surface:
    """A row: a file, how to find its documented things, what to call them."""

    rule: str
    path: str
    find: Callable[[ast.Module], list[Subject]]
    noun: str
    argument: str


SURFACES: list[Surface] = [
    Surface("control-commands", "kinesis/control.py", control_commands,
            "control command", "JSON key"),
    Surface("mcp-tools", "kinesis/mcp_server.py", mcp_tools, "MCP tool", "parameter"),
]


def check(surface: Surface) -> list[Finding]:
    path = ROOT / surface.path
    rule = f"{CHECK}/{surface.rule}"
    subjects = surface.find(parse(path)[1])
    if not subjects:
        return [Finding(path, 1, rule,
                        f"no {surface.noun}s found here -- this check has stopped checking")]
    findings = []
    for subject in subjects:
        if not subject.doc:
            findings.append(Finding(path, subject.line, rule,
                                    f"{surface.noun} {subject.name} has no docstring; it is the "
                                    "only documentation this surface has"))
            continue
        for argument in subject.arguments:
            if not re.search(rf"\b{re.escape(argument)}\b", subject.doc):
                findings.append(Finding(
                    path, subject.line, rule,
                    f"{surface.noun} {subject.name}: docstring never mentions the "
                    f'{surface.argument} "{argument}"'))
    return findings


def main() -> int:
    findings = [finding for surface in SURFACES for finding in check(surface)]
    return report(CHECK, findings, len(SURFACES))


if __name__ == "__main__":
    sys.exit(main())
