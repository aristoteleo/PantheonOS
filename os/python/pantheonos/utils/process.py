"""Cross-platform process utilities.

Provides utilities for managing child processes, particularly ensuring
child processes are properly terminated when the parent process dies.
"""

import os
import sys
import signal


def make_child_die_with_parent():
    """
    Ensure child process terminates when parent process dies.
    
    This function should be passed as `preexec_fn` to subprocess/asyncio
    process creation functions.
    
    Platform support:
    - Linux: Uses prctl(PR_SET_PDEATHSIG) for kernel-level guarantee
    - macOS: Uses process groups (limited effectiveness)
    - Windows: Not applicable (preexec_fn not supported)
    
    Usage:
        import asyncio
        from pantheonos.utils.process import make_child_die_with_parent
        
        process = await asyncio.create_subprocess_exec(
            "bash", "-c", "sleep 1000",
            preexec_fn=make_child_die_with_parent if sys.platform != "win32" else None,
        )
    """
    if sys.platform == "linux":
        _set_pdeathsig_linux()
    elif sys.platform == "darwin":
        _set_pgrp_macos()


def _set_pdeathsig_linux():
    """Linux: Set PR_SET_PDEATHSIG so child receives SIGTERM when parent dies."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        result = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        if result != 0:
            errno = ctypes.get_errno()
            # Log warning but don't fail - this is best effort
            pass
    except Exception:
        # Silently fail - this is a best-effort feature
        pass


def _set_pgrp_macos():
    """macOS: Set process group (limited effectiveness)."""
    try:
        os.setpgrp()
    except Exception:
        pass
