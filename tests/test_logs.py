import subprocess
import sys


def test_verbose_logs_debug_lines_of_this_app_only():
    # A fresh interpreter: logging can only be set up once per process.
    script = (
        "import logging; from agentic_editor import logs; logs.setup(logging.DEBUG); "
        "print(logging.getLogger('agentic_editor.pipeline').isEnabledFor(logging.DEBUG), "
        "logging.getLogger('numba.core').isEnabledFor(logging.DEBUG), "
        "logging.getLogger('huey').isEnabledFor(logging.INFO))"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["True", "False", "True"]
