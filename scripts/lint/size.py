"""File size cap, counted so that explaining yourself is free.

CLAUDE.md caps files and excludes comments from the count, and the reason it
gives is that the house style puts the *why* in module docstrings -- a cap that
taxed prose would put those two rules against each other. A count that skips
`#` lines but not docstrings does exactly that, so this one is taken off the
AST: blank lines, `#` comments and docstrings are all excluded, and everything
else is a line of code.

The number that mattered was never the count, it was having one definition of
it rather than one per person who runs it.

Run alone:  python -m scripts.lint.size
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass

from scripts.lint.common import Finding, parse, python_files, report

CHECK = "size"


@dataclass(frozen=True)
class Cap:
    """A cap is a row: what it is called, what it covers, how many lines."""

    rule: str
    patterns: tuple[str, ...]
    limit: int


CAPS: list[Cap] = [
    Cap("source-300", ("kinesis/**/*.py", "scripts/**/*.py"), 300),
    Cap("tests-250", ("tests/**/*.py",), 250),
]


def code_lines(source: str, tree: ast.Module) -> list[int]:
    """Line numbers that count: not blank, not a `#` comment, not a docstring."""
    skip: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            skip.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return [
        number
        for number, text in enumerate(source.splitlines(), start=1)
        if number not in skip and text.strip() and not text.lstrip().startswith("#")
    ]


def main() -> int:
    findings: list[Finding] = []
    scanned = set()
    for cap in CAPS:
        for path in python_files(*cap.patterns):
            scanned.add(path)
            source, tree = parse(path)
            counted = code_lines(source, tree)
            if len(counted) > cap.limit:
                findings.append(Finding(
                    path=path,
                    line=counted[cap.limit],
                    rule=f"{CHECK}/{cap.rule}",
                    message=(
                        f"{len(counted)} lines of code, cap is {cap.limit} "
                        "(blanks, # comments and docstrings not counted) -- split it"
                    ),
                ))
    return report(CHECK, findings, len(scanned))


if __name__ == "__main__":
    sys.exit(main())
