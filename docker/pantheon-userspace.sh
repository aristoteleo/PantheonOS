#!/bin/bash
# Root every package manager in the Volume, so what a user installs survives.
#
# A sandbox is recreated often — on restart, after an idle reclaim, and on every
# agent-image update — and only the Volume survives that. Anything written to
# /usr, /opt or the venv is part of the image and is gone with it, which is why
# "I installed vim yesterday and today it is missing" kept happening.
#
# Rather than photographing the container and replaying it, this points the
# package managers themselves at the half of the filesystem that persists. The
# packages are then just files on the Volume: they need no snapshot, they cannot
# drift out of alignment with the base image, and an image upgrade leaves them
# untouched instead of erasing them.
#
# Measured on staging, each one installed and then verified in a *different*
# sandbox after a release/recreate cycle:
#
#   pip install cowsay          → /workspace/.local/pylibs      SURVIVED
#   npm install -g left-pad     → /workspace/.local/lib          SURVIVED
#   install.packages("jsonlite")→ /workspace/.local/Rlib         SURVIVED
#   apt install samtools        → /workspace/.local/opt          SURVIVED
#
# Sourced by the entrypoint (so the agent and every pty it spawns inherit it),
# by /etc/bash.bashrc (which is what an INTERACTIVE shell reads — the pty runs
# `bash -i`, which never reads /etc/profile.d, so without this the terminal
# only ever sees the environment as it was at boot), and by /etc/profile.d for
# login shells.
#
# Re-sourcing therefore happens constantly and must be harmless. It is also the
# point: the analysis env is built in the background AFTER boot, so the shell
# that re-evaluates this is the one that finds it, without waiting for a
# restart. Every PATH entry is added only if it is not already there.

PANTHEON_USER_PREFIX="${PANTHEON_USER_PREFIX:-${WORKSPACE:-/workspace}/.local}"
export PANTHEON_USER_PREFIX

# Prepend, but only once. This file is sourced repeatedly — every interactive
# shell re-reads it — and a plain PATH="$new:$PATH" would grow without bound.
_pantheon_prepend_path() {
    case ":$PATH:" in
        *":$1:"*) ;;
        *) PATH="$1:$PATH" ;;
    esac
}

# Captured BEFORE anything below touches PATH, and it has to be: the analysis
# env goes on the front of PATH further down, which would otherwise make bare
# `python` mean the env — and the entrypoint launches the agent with bare
# `python`. The agent would then be running on the very environment the user is
# free to upgrade and break, which is the exact arrangement all of this exists
# to prevent. Everything that must run on the runtime uses this name instead.
PANTHEON_RUNTIME_PYTHON="${PANTHEON_RUNTIME_PYTHON:-$(command -v python || command -v python3)}"
export PANTHEON_RUNTIME_PYTHON

mkdir -p "$PANTHEON_USER_PREFIX"/{bin,pylibs,Rlib,opt,aptcache/archives/partial,aptcache/lists/partial} 2>/dev/null

# ---------------------------------------------------------------- executables
# Two roots: `bin` for things that install a binary directly (npm, cargo, go,
# and pip's console scripts), `opt` for the unpacked-.deb tree, which keeps the
# distribution's own /usr/bin layout.
_pantheon_prepend_path "$PANTHEON_USER_PREFIX/opt/bin"
_pantheon_prepend_path "$PANTHEON_USER_PREFIX/opt/usr/bin"
_pantheon_prepend_path "$PANTHEON_USER_PREFIX/bin"
export PATH

# A .deb's shared objects are not in the loader's search path, so a binary
# unpacked from one finds its libraries only if we say where they are.
_ARCH_LIB="$PANTHEON_USER_PREFIX/opt/usr/lib/$(uname -m)-linux-gnu"
export LD_LIBRARY_PATH="$_ARCH_LIB:$PANTHEON_USER_PREFIX/opt/usr/lib:$PANTHEON_USER_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MANPATH="$PANTHEON_USER_PREFIX/opt/usr/share/man:${MANPATH:-}"
unset _ARCH_LIB

# ------------------------------------------------------------- conda-forge
# The one thing the package managers above cannot do. Debian's
# `install.packages()` builds R packages from source, so anything with a C
# dependency needs headers that are not there — and the natural fix does not
# work either:
#
#   install.packages("XML")   → installation had non-zero exit status
#   apt install libxml2-dev   → unpacked into <prefix>/opt   (the shim works)
#   install.packages("XML")   → STILL fails; R's configure does not look there
#
# conda-forge ships that same package already built. Measured here: 39 s and
# `library(XML)` works, against "cannot be installed at all". Bioconda gets the
# same treatment for command-line tools — samtools in 5 s, and a newer build
# than Debian carries (1.24 vs 1.16.1).
#
# Envs live on the Volume, so they persist for the same reason everything else
# here does.
export MAMBA_ROOT_PREFIX="$PANTHEON_USER_PREFIX/micromamba"

# The package cache stays on the Volume, unlike the pip/HF/torch caches the
# entrypoint sends to ephemeral /root. Those are throwaway; this one is the
# working set that makes a second env cheap to build, and micromamba copies
# rather than hardlinks out of it (verified: nlink=1), so an env does not
# depend on it afterwards. Point MAMBA_PKGS_DIRS elsewhere to change that.
export MAMBA_PKGS_DIRS="${MAMBA_PKGS_DIRS:-$MAMBA_ROOT_PREFIX/pkgs}"

# `micromamba activate` is a shell function, not the binary — without the hook
# the terminal answers "run 'micromamba shell init' first" and the env cannot
# be entered at all. Non-fatal on both counts: the binary may be missing (its
# download in the Dockerfile is best-effort) and a bad eval must not take the
# boot with it.
if command -v micromamba >/dev/null 2>&1; then
    eval "$(micromamba shell hook -s posix 2>/dev/null)" || true
    # `conda` and `mamba` are the names in every tutorial and in muscle memory,
    # and there is no second implementation here for them to be confused with.
    # Functions rather than symlinks because `activate` has to run in the
    # caller's shell; /usr/local/bin carries script shims for the rest.
    conda() { micromamba "$@"; }
    mamba() { micromamba "$@"; }
fi

# -------------------------------------------------------------------- python
# /venv is the agent's runtime and stays that: what PantheonOS needs to run,
# nothing else. Analysis packages go in a conda env on the Volume instead, so
# that upgrading scanpy cannot take the sandbox down with it and two projects
# with incompatible pins can have an env each. See pantheon-analysis-env.
export PANTHEON_ANALYSIS_ENV="${PANTHEON_ANALYSIS_ENV:-analysis}"
_ANALYSIS_DIR="$MAMBA_ROOT_PREFIX/envs/$PANTHEON_ANALYSIS_ENV"

# The marker, not the presence of a python binary: an interrupted build leaves
# an interpreter that cannot import `encodings` and dies on startup, and
# putting that first on PATH would be worse than having no env. Only
# pantheon-analysis-env writes it, and only after the env has proved it starts.
if [ -f "$_ANALYSIS_DIR/.pantheon-ready" ] && [ -x "$_ANALYSIS_DIR/bin/python" ]; then
    # `python` and `pip` mean the analysis env — for the person at the terminal
    # and for anything the agent shells out to, which is the point: work lands
    # where it persists and where it cannot reach the runtime.
    _pantheon_prepend_path "$_ANALYSIS_DIR/bin"
    export PATH

    # Actually activate it, for an interactive shell. The PATH entry above is
    # enough to make python/pip/R resolve correctly, and that is what the agent
    # and every script need — but a person at a terminal reasonably expects
    # `conda env list` to mark the env active and the prompt to say so, and
    # neither happens without a real activation (CONDA_PREFIX, CONDA_DEFAULT_ENV
    # and the rest are what those read).
    #
    # Interactive only: activation rewrites PATH and defines shell state, which
    # is unwanted noise in the entrypoint and in every `bash -c` the agent runs.
    # Guarded against re-activation because this file is sourced by each new
    # interactive shell.
    case "$-" in
        *i*)
            if [ "${CONDA_DEFAULT_ENV:-}" != "$PANTHEON_ANALYSIS_ENV" ] \
               && command -v micromamba >/dev/null 2>&1; then
                micromamba activate "$PANTHEON_ANALYSIS_ENV" 2>/dev/null || true
            fi
            ;;
    esac

    # Read by PantheonOS to spawn its Python interpreters here rather than in
    # /venv. Absent or unreadable, it falls back to the runtime, so a broken
    # env costs analysis isolation and never the sandbox.
    export PANTHEON_ANALYSIS_PYTHON="$_ANALYSIS_DIR/bin/python"

    # PIP_TARGET is deliberately NOT set in this case. The env is already on
    # the Volume, so its own site-packages is the persistent place, and a
    # --target pointing elsewhere would quietly divert every install out of the
    # env the user just activated.
else
    # No env yet — first boot, or creation failed. `pip install X` still has to
    # persist, so fall back to the flat prefix. --target rather than --user:
    # the runtime is a virtualenv and a virtualenv disables --user outright
    # ("User site-packages are not visible in this virtualenv").
    export PIP_TARGET="$PANTHEON_USER_PREFIX/pylibs"

    # Made importable with a .pth, NOT with PYTHONPATH. PYTHONPATH is searched
    # BEFORE site-packages, so one `pip install --upgrade pydantic` would
    # shadow the agent's own dependency with a version it was never tested
    # against, inside the process that has to keep the sandbox alive. `site`
    # processes a .pth by APPENDING, so the runtime still wins.
    _SITE="$(python3 -c 'import site,sys; p=site.getsitepackages(); sys.stdout.write(p[0] if p else "")' 2>/dev/null)"
    if [ -n "$_SITE" ] && [ -d "$_SITE" ]; then
        # addsitedir() on a path that does not exist yet is a no-op, so this is
        # written once and stays correct whether or not anything is installed.
        printf 'import site; site.addsitedir(%s)\n' "\"$PANTHEON_USER_PREFIX/pylibs\"" \
            > "$_SITE/zzz-pantheon-userspace.pth" 2>/dev/null || true
    fi
    unset _SITE
fi
unset _ANALYSIS_DIR

# ----------------------------------------------------------------- node / npm
export npm_config_prefix="$PANTHEON_USER_PREFIX"
export NODE_PATH="$PANTHEON_USER_PREFIX/lib/node_modules${NODE_PATH:+:$NODE_PATH}"

# ---------------------------------------------------------------------- R
# Per-environment, NOT one shared library directory. Once the analysis env
# provides R, `R` means conda's build, and a package compiled against Debian's
# R will not load in it — sharing one R_LIBS_USER between the two is a silent
# way to break packages that used to work. Kept outside the env so that
# rebuilding the env does not take the installed R packages with it.
if [ -n "${PANTHEON_ANALYSIS_PYTHON:-}" ]; then
    export R_LIBS_USER="$PANTHEON_USER_PREFIX/Rlib/$PANTHEON_ANALYSIS_ENV"
else
    export R_LIBS_USER="$PANTHEON_USER_PREFIX/Rlib/system"
fi
mkdir -p "$R_LIBS_USER" 2>/dev/null

# ------------------------------------------------------------- rust / go
# The binary persists; the build and module caches deliberately do not. They are
# large, they are write-heavy, and the Volume is network-backed — the same
# reason the entrypoint already sends pip/HF/torch caches to ephemeral /root.
export CARGO_INSTALL_ROOT="$PANTHEON_USER_PREFIX"
export GOBIN="$PANTHEON_USER_PREFIX/bin"
