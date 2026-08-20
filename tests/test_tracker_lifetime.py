"""The tracker must not outlive the UI.

It owns the camera, so an orphaned tracker is a webcam light left on with no
window to explain it. `daemon=True` only covers the parent leaving through
Python's own shutdown, so the parentage check is what covers a crash, a Force
Quit or a SIGTERM. None of this needs a camera: the signal is process
parentage, and it can be reproduced with three plain processes.
"""

import os
import subprocess
import sys
import time

from kinesis.tracking.worker import _parent_gone

# Records the ppid it started under, then loops. On noticing it has been
# reparented it drops a marker and exits -- the marker is what proves it left
# because of _parent_gone rather than for some unrelated reason.
WATCHER = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from kinesis.tracking.worker import _parent_gone
start = os.getppid()
deadline = time.time() + 20
while time.time() < deadline:
    if _parent_gone(start):
        open(sys.argv[2], "w").write("orphan detected")
        sys.exit(7)
    time.sleep(0.02)
sys.exit(1)
"""

# Spawns the watcher and then blocks. Killing THIS is what orphans the watcher.
MIDDLE = """
import subprocess, sys, time
w = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])
print(w.pid, flush=True)
time.sleep(60)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def test_live_parent_is_not_gone():
    assert _parent_gone(os.getppid()) is False


def test_any_other_parent_means_gone():
    """The recorded ppid changing is the signal, whatever it changed to."""
    assert _parent_gone(os.getppid() + 1) is True
    assert _parent_gone(1) is True


def test_an_orphaned_watcher_exits_on_its_own(tmp_path):
    """pytest -> middle -> watcher. Kill the middle; the watcher must leave."""
    watcher_py = tmp_path / "watcher.py"
    watcher_py.write_text(WATCHER)
    middle_py = tmp_path / "middle.py"
    middle_py.write_text(MIDDLE)
    marker = tmp_path / "orphaned.txt"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    middle = subprocess.Popen(
        [sys.executable, str(middle_py), str(watcher_py), repo_root, str(marker)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        watcher_pid = int(middle.stdout.readline().strip())
        time.sleep(0.5)
        assert _alive(watcher_pid), "watcher died before anything was killed"
        assert not marker.exists(), "watcher called itself orphaned while its parent lived"

        middle.kill()
        middle.wait(timeout=10)

        for _ in range(400):
            if not _alive(watcher_pid):
                break
            time.sleep(0.02)
        else:
            os.kill(watcher_pid, 9)
            raise AssertionError("orphaned watcher never noticed its parent had died")

        assert marker.exists(), "watcher exited, but not because it detected the orphaning"
    finally:
        if middle.poll() is None:
            middle.kill()
