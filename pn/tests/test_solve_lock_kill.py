"""A solve killed outright (kill -9: no finally runs) leaves its pn.solve.lock markers behind;
they must not block the next solve. The flock dies with the process and markers of dead pids
are dropped by the next holder that looks at them."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from pn.solve.lock import PRIORITY_DIR, WAITING_DIR, file_lock

HOLD = """
import sys, time
from pathlib import Path
from pn.solve.lock import file_lock
with file_lock(Path(sys.argv[1]), priority=sys.argv[2] == "priority"):
    print("held", flush=True)
    time.sleep(60)
"""


def _spawn(root: Path, kind: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", HOLD, str(root), kind], stdout=subprocess.PIPE, text=True)


def test_killed_holders_do_not_block_the_next_solve(tmp_path: Path) -> None:
    holder = _spawn(tmp_path, "priority")
    assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
    waiter = _spawn(tmp_path, "ordinary")  # waits behind the priority holder: leaves a marker
    deadline = time.monotonic() + 10
    while not list((tmp_path / WAITING_DIR).glob(f"{waiter.pid}-*")) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert list((tmp_path / PRIORITY_DIR).iterdir()) and list((tmp_path / WAITING_DIR).iterdir())
    for p in (waiter, holder):
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
    got = threading.Event()

    def take() -> None:
        with file_lock(tmp_path, background=True):  # yields to live priority and ordinary holders
            got.set()

    t = threading.Thread(target=take, daemon=True)
    t.start()
    assert got.wait(5), "a dead process's lock or marker blocked the next solve"
    t.join(5)
    assert not list((tmp_path / PRIORITY_DIR).iterdir()) and not list((tmp_path / WAITING_DIR).iterdir())
