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
            if (
                not Path(f"/proc/{child}").exists()
                or "Z" in Path(f"/proc/{child}/stat").read_text().split()[2]
            ):
                break
            time.sleep(0.05)
        alive = (
            Path(f"/proc/{child}").exists()
            and "Z" not in Path(f"/proc/{child}/stat").read_text().split()[2]
        )
        assert not alive
    finally:
        parent.kill()
