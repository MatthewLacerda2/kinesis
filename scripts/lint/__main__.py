"""Run every check, then report. One command, because ./run.sh check is one gate.

All three run even after one fails -- a file over the size cap should not hide
an undocumented MCP argument, the same reason ./run.sh check runs its gates to
the end.
"""

from __future__ import annotations

import sys

from scripts.lint import agent_docs, architecture, size

CHECKS = [size.main, architecture.main, agent_docs.main]


def main() -> int:
    return max([check() for check in CHECKS])


if __name__ == "__main__":
    sys.exit(main())
