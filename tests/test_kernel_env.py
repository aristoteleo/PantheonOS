"""Regression: a Jupyter kernel running a DIFFERENT Python (e.g. a conda env the
user selected) must NOT inherit the endpoint interpreter's sys.path / PYTHONPATH.

Injecting the endpoint's (.venv) import paths into a foreign-Python kernel makes it
load C extensions (re/_sre, numpy, …) compiled for the wrong Python version, and it
dies on startup with "Kernel died before replying to kernel_info". The same-Python
default kernel still shares sys.path (legacy behavior). See
jupyter_kernel._build_kernel_env / _is_same_interpreter.
"""
import os
import sys

from pantheon.apps.builtin.notebook.jupyter_kernel import JupyterKernelToolSet


def _bare_toolset():
    # Bypass __init__ (heavy setup) — the methods under test only need these.
    ts = JupyterKernelToolSet.__new__(JupyterKernelToolSet)
    ts._current_context_dict = lambda: {}
    ts._get_effective_workdir = lambda: None
    ts.workdir = "/tmp"
    return ts


def test_is_same_interpreter():
    ts = _bare_toolset()
    assert ts._is_same_interpreter(sys.executable) is True
    assert ts._is_same_interpreter("/opt/conda/envs/sc/bin/python3.11") is False
    # Unknown / relative paths default to True (legacy in-process default kernel).
    assert ts._is_same_interpreter(None) is True
    assert ts._is_same_interpreter("") is True


def test_foreign_kernel_env_drops_pythonpath(monkeypatch):
    ts = _bare_toolset()
    monkeypatch.setenv("PYTHONPATH", "/endpoint/site-packages")
    monkeypatch.setenv("PYTHONHOME", "/endpoint")
    env = ts._build_kernel_env("/opt/conda/envs/sc/bin/python3.11")  # foreign interpreter
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env


def test_same_interpreter_shares_syspath():
    ts = _bare_toolset()
    env = ts._build_kernel_env(sys.executable)  # same interpreter
    assert "PYTHONPATH" in env
    real_paths = [p for p in sys.path if p]
    if real_paths:
        assert real_paths[0] in env["PYTHONPATH"].split(os.pathsep)
