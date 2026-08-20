"""Shared plumbing, so a check is a rule table plus a walk and nothing else.

Every check reports the same way -- `path:line: [rule] message`, the
compiler-style form a terminal turns into a clickable link -- and every check
names the rule that failed, because "this file is too long" without the rule
behind it sends the reader back to CLAUDE.md to find out what the number means.

Files are found under the checkout this file lives in, not under the working
directory: a run from a git worktree must grade that worktree.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Finding:
    """One violation, at one line, of one named rule."""

    path: Path
    line: int
    rule: str
    message: str

    def render(self) -> str:
        return f"{rel(self.path)}:{self.line}: [{self.rule}] {self.message}"


def rel(path: Path) -> str:
    """Path relative to the checkout root -- shorter, and still clickable."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def python_files(*patterns: str) -> list[Path]:
    """Every .py file matching any of the glob patterns, root-relative."""
    found: set[Path] = set()
    for pattern in patterns:
        found.update(p for p in ROOT.glob(pattern) if p.is_file())
    return sorted(found)


def parse(path: Path) -> tuple[str, ast.Module]:
    """Source text and its AST. A syntax error is the caller's to report."""
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=str(path))


def report(check: str, findings: Iterable[Finding], scanned: Sequence[Path] | int) -> int:
    """Print findings and a one-line verdict; return a process exit code.

    The scanned count is printed on success too: a check that quietly stopped
    matching anything looks exactly like a check that passes.
    """
    count = scanned if isinstance(scanned, int) else len(scanned)
    findings = sorted(findings, key=lambda f: (rel(f.path), f.line, f.rule))
    for finding in findings:
        print(finding.render(), file=sys.stderr)
    if findings:
        print(f"[{check}] {len(findings)} violation(s) across {count} file(s)", file=sys.stderr)
        return 1
    print(f"[{check}] ok -- {count} file(s)")
    return 0
