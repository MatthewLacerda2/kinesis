"""Fail when the shared venv's editable install points somewhere that no longer exists.

This repo runs agents in parallel git worktrees against one shared venv, because
PySide6 + MediaPipe is ~500MB and a venv per worktree costs minutes and gigabytes
for a project with no build step. The cost of sharing is that `pip install -e`
run from any worktree rewrites the mapping for everybody -- and `run.sh` used to
do exactly that on every first invocation in a fresh checkout.

What that breaks is narrower than it looks. pytest, the import sweep and
`python -m kinesis` all run with a checkout at the front of sys.path, so they
import the tree they are standing in and never consult the mapping. The damage
lands later: the mapping is left pointing at a worktree, that worktree is
deleted, and the venv is broken for anything that imports kinesis from an
unrelated directory -- with nothing on screen connecting the two events.

So this checks the symptom that is actually observable rather than asserting
which tree a gate graded (measured: that assertion passes unconditionally and
proves nothing). Dangling mapping, loud failure, repair instruction.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV = REPO / ".venv"

# setuptools writes one of two shapes: a _finder module holding a MAPPING dict
# for the strict/static layout, or a plain .pth listing directories. Both are in
# use across setuptools versions, so both are read rather than pinning a version.
MAPPING_RE = re.compile(r"""['"]kinesis['"]\s*:\s*['"]([^'"]+)['"]""")


def mapped_paths(site_packages: Path) -> list[Path]:
    """Every directory the editable install claims 'kinesis' lives in.

    Empty when there is no editable install at all, which is not a failure --
    a plain (non-editable) install has nothing to dangle.
    """
    found: list[Path] = []
    for finder in site_packages.glob("__editable___kinesis*_finder.py"):
        found += [Path(m) for m in MAPPING_RE.findall(finder.read_text())]
    for pth in site_packages.glob("__editable__*kinesis*.pth"):
        for line in pth.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("import ") and Path(line).is_absolute():
                found.append(Path(line) / "kinesis")
    return found


def broken(paths: list[Path]) -> list[Path]:
    """Those that no longer hold an importable kinesis package."""
    return [p for p in paths if not (p / "__init__.py").is_file()]


def main() -> int:
    site = next(iter(VENV.glob("lib/python3.*/site-packages")), None)
    if site is None:
        print("[check-venv] no .venv yet — nothing to check.")
        return 0

    paths = mapped_paths(site)
    if not paths:
        print("[check-venv] no editable install found — nothing to check.")
        return 0

    bad = broken(paths)
    for p in paths:
        print(f"[check-venv] editable kinesis -> {p}{'  (DANGLING)' if p in bad else ''}")
    if bad:
        print(
            "\n[check-venv] The shared venv's editable install points at a tree that is "
            "gone.\n"
            "             Most likely a `pip install -e` was run from a git worktree "
            "that has since\n"
            "             been removed. Repair it from the MAIN checkout:\n"
            f"                 cd {REPO} && .venv/bin/python -m pip install -e '.[dev]'",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
