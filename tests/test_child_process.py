import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def nap(pid_file: str) -> None:
    Path(pid_file).write_text(str(os.getpid()))
    time.sleep(60)


def running(pid: int) -> bool:
    """Alive and not a zombie. The process can go between looking and reading its stat."""
    try:
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().split()[2]
    except OSError:
        return False


@pytest.mark.skipif(sys.platform != "linux", reason="parent-death signal is Linux only")
def test_a_model_worker_dies_with_its_parent(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = (
        "from agentic_editor.router import in_child_process\n"
        "from tests.test_child_process import nap\n"
        f"in_child_process(nap, {str(pid_file)!r})\n"
    )
    root = Path(__file__).resolve().parent.parent
    parent = subprocess.Popen([sys.executable, "-c", script], cwd=root)
    try:
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.05)
        child = int(pid_file.read_text())
        parent.send_signal(signal.SIGKILL)
        parent.wait()
        for _ in range(100):
            if not running(child):
                break
            time.sleep(0.05)
        assert not running(child)
    finally:
        parent.kill()


def test_without_a_death_signal_a_worker_still_notices_its_parent_is_gone(tmp_path):
    # What macOS relies on. The worker is a grandchild here so its parent can be killed.
    pid_file = tmp_path / "worker.pid"
    worker = (
        "import os, time\n"
        "from pathlib import Path\n"
        "from agentic_editor.router import exit_with\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "exit_with(os.getppid(), every=0.05)\n"
        "time.sleep(60)\n"
    )
    parent_script = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {worker!r}])\n"
        "time.sleep(60)\n"
    )
    root = Path(__file__).resolve().parent.parent
    parent = subprocess.Popen([sys.executable, "-c", parent_script], cwd=root)
    try:
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.05)
        child = int(pid_file.read_text())
        parent.send_signal(signal.SIGKILL)
        parent.wait()
        for _ in range(100):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGKILL)
            raise AssertionError("the worker outlived its parent")
    finally:
        parent.kill()
