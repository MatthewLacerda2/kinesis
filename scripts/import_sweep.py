"""Import every module in the kinesis package, so a circular import fails here
instead of at launch.

The suite deliberately keeps away from most of Qt, so a cycle between app, view
and hand_control can survive a green pytest run. This walks the package with
pkgutil rather than checking a hand-written list of modules, because a list
silently stops covering whatever gets added after it was written.

Runs headless -- offscreen Qt, no GL. Importing a module must not open a window
or a camera; if this script hangs or pops something up, that is the finding.

Sweeps the checkout this file lives in, not whatever an editable install points
at -- otherwise a run from a git worktree would quietly grade the main checkout.
"""

from __future__ import annotations

import os
import pkgutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

import kinesis  # noqa: E402


def main() -> int:
    names = [kinesis.__name__] + [
        info.name
        for info in pkgutil.walk_packages(kinesis.__path__, prefix=f"{kinesis.__name__}.")
    ]
    failed = []
    for name in names:
        try:
            __import__(name)
        except Exception:
            failed.append(name)
            traceback.print_exc()

    print(f"[import-sweep] package at {kinesis.__file__}")
    print(f"[import-sweep] {len(names) - len(failed)}/{len(names)} modules imported")
    if failed:
        print("[import-sweep] failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
