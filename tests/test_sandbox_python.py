"""SandboxPythonToolSet: fresh process per call, hard time limit, scrubbed environment."""
import asyncio
import os
import time

from pantheon.toolsets.python.sandbox import SandboxPythonToolSet


def _run(ts, code, **kw):
    return asyncio.run(ts.run_python_script(code, **kw))


def test_runs_numpy_and_captures_output():
    r = _run(SandboxPythonToolSet("s"), "import numpy as np\nprint(np.arange(4).sum())\n")
    assert r["exit_code"] == 0 and r["stdout"].strip() == "6" and not r["timed_out"]


def test_time_limit_kills_the_process_group():
    ts = SandboxPythonToolSet("s")
    t0 = time.time()
    code = ("import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print('started', flush=True)\ntime.sleep(60)\n")
    r = _run(ts, code, timeout_s=2)
    assert r["timed_out"] and time.time() - t0 < 15
    assert "started" in r["stdout"]


def test_environment_is_scrubbed_and_state_is_not_kept(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    ts = SandboxPythonToolSet("s")
    r = _run(ts, "import os\nprint(os.environ.get('OPENAI_API_KEY'))\nx = 5\n")
    assert r["stdout"].strip() == "None"
    r2 = _run(ts, "print('x' in globals())\n")
    assert r2["stdout"].strip() == "False"


def test_errors_are_returned_not_raised():
    r = _run(SandboxPythonToolSet("s"), "raise ValueError('boom')\n")
    assert r["exit_code"] != 0 and "ValueError: boom" in r["stderr"]


def test_log_records_each_run():
    ts = SandboxPythonToolSet("s")
    _run(ts, "print(1)\n")
    assert len(ts.log) == 1 and ts.log[0]["exit_code"] == 0
