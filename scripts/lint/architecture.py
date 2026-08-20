"""The dependency directions that must not invert, as rows instead of prose.

Every architectural invariant in CLAUDE.md that constrains this package is
import-shaped -- the tracker is a separate process, so it may not reach into the
canvas or the UI; gestures and filters are the only honestly testable code, so
nothing heavy may follow them into a test run; MediaPipe and OpenCV belong at
the capture edge and nowhere else. So one AST pass over the package checks all
of them, and adding an invariant is adding a row to RULES.

Imports are read from the AST rather than matched as text, which matters here:
worker.py imports MediaPipe and cv2 inside a function, to keep the parent
process light, and a grep-shaped check would miss exactly the imports the rules
are about.

Run alone:  python -m scripts.lint.architecture
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.lint.common import ROOT, Finding, parse, python_files, report

CHECK = "architecture"


@dataclass(frozen=True)
class Rule:
    """One row: which modules it covers, and what they may not import.

    `forbids` is a denylist of import prefixes. `permits`, when set, is the
    opposite -- an allowlist of the only non-stdlib imports the module may have,
    which is how "depends on nothing but protocol.py" is stated. `unless_in`
    exempts the modules that are deliberately the exception.
    """

    rule: str
    applies_to: tuple[str, ...]
    why: str
    forbids: tuple[str, ...] = ()
    permits: tuple[str, ...] = ()
    unless_in: tuple[str, ...] = ()


RULES: list[Rule] = [
    Rule(
        rule="tracking-is-standalone",
        applies_to=("kinesis.tracking",),
        forbids=("kinesis.canvas", "kinesis.ui"),
        why="the tracker is a separate process and may not depend on canvas/ or ui/",
    ),
    Rule(
        # An allowlist rather than a list of (Qt, cv2, mediapipe): the point of
        # the pure layer is that a *new* heavy dependency is a violation too.
        # filters is in the allowlist because gestures builds on it and both
        # sides of that import are the pure layer itself.
        rule="pure-layer",
        applies_to=("kinesis.tracking.gestures", "kinesis.tracking.filters"),
        permits=("kinesis.tracking.protocol", "kinesis.tracking.filters"),
        why="gestures and filters stay pure so they can be tested without a camera or Qt",
    ),
    Rule(
        rule="mediapipe-in-the-tracker",
        applies_to=("kinesis",),
        forbids=("mediapipe",),
        unless_in=("kinesis.tracking.worker", "kinesis.tracking.model"),
        why="inference lives in the tracker process; only worker.py and model.py touch it",
    ),
    Rule(
        rule="cv2-at-the-capture-edge",
        applies_to=("kinesis",),
        forbids=("cv2",),
        unless_in=("kinesis.tracking.worker", "kinesis.ui.camera_feed"),
        why="only the two capture loops open a camera",
    ),
]


def module_name(path: Path) -> str:
    """Dotted name of the module a file defines, e.g. kinesis.tracking.worker."""
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def under(name: str, prefixes: tuple[str, ...]) -> bool:
    """Is this module the named one, or inside it?"""
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def imports(tree: ast.Module, module: str) -> list[tuple[str, int]]:
    """Every module imported anywhere in the file, relative imports resolved."""
    package = module.rsplit(".", 1)[0] if "." in module else module
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if not node.level:            # absolute: `from mediapipe.tasks import ...`
                found.append((node.module or "", node.lineno))
                continue
            base = package.split(".")[: len(package.split(".")) - node.level + 1]
            found.append((".".join(base + ([node.module] if node.module else [])), node.lineno))
    return found


def violations(path: Path, module: str, tree: ast.Module) -> list[Finding]:
    findings = []
    for rule in RULES:
        if not under(module, rule.applies_to) or module in rule.unless_in:
            continue
        for imported, line in imports(tree, module):
            top = imported.split(".")[0]
            bad = under(imported, rule.forbids) or (
                bool(rule.permits)
                and top not in sys.stdlib_module_names
                and not under(imported, rule.permits)
            )
            if bad:
                findings.append(Finding(path, line, f"{CHECK}/{rule.rule}",
                                        f"imports {imported}: {rule.why}"))
    return findings


def main() -> int:
    paths = python_files("kinesis/**/*.py")
    findings: list[Finding] = []
    for path in paths:
        module = module_name(path)
        findings += violations(path, module, parse(path)[1])
    return report(CHECK, findings, paths)


if __name__ == "__main__":
    sys.exit(main())
