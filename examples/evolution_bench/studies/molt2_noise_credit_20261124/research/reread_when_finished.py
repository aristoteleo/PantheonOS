"""Finite post-search public checks; never restarts optimization."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pid = json.loads((ROOT/'CONTROLLER_HANDLE.json').read_text())['pid']
deadline = time.monotonic()+73*3600
while not (ROOT/'CAMPAIGN_COMPLETE.json').exists():
    try:
        alive = b'runner/controller.py' in Path(f'/proc/{pid}/cmdline').read_bytes() and Path(f'/proc/{pid}/cwd').resolve() == ROOT
    except OSError:
        alive = False
    if not alive or time.monotonic() > deadline:
        raise RuntimeError('Controller unavailable or deadline exceeded; no search or rereads started')
    time.sleep(30)
subprocess.run([sys.executable, str(ROOT/'research/reread_finalists.py')], check=True, timeout=44100)
