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

# ------------------------------------------------------------ snapshot fast path
# The full evaluation below costs seconds on a network-backed Volume: every
# stat, glob and mkdir is a round trip, and it spawns interpreters
# (`python -c 'import fire'`, the micromamba hook, `micromamba activate` at
# ~520 ms) — measured 2-12 s per boot depending on the Volume's contents, and
# re-paid by every shell that re-sources this file. The result, though, is just
# a set of exports and three shell functions, so the full run writes them to a
# snapshot on the Volume and later sourcings replay that one file instead.
#
# The snapshot guards its own validity (first line checks that the analysis
# env's readiness marker still matches what the snapshot was generated
# against): building or removing the env flips the check, the replay fails,
# and this falls through to the full evaluation — which is exactly the
# "next shell discovers the new env" behaviour documented above, kept intact.
# PANTHEON_USERSPACE_REBUILD=1 forces the full path (the entrypoint uses it to
# refresh the snapshot in the background on every boot).
_PANTHEON_SNAPSHOT="$PANTHEON_USER_PREFIX/.userspace-snapshot.sh"
if [ -z "${PANTHEON_USERSPACE_REBUILD:-}" ] && [ -r "$_PANTHEON_SNAPSHOT" ]; then
    if . "$_PANTHEON_SNAPSHOT" 2>/dev/null; then
        unset _PANTHEON_SNAPSHOT
        return 0 2>/dev/null || true
    fi
fi

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
# The runtime venv by path, not by asking PATH — and corrected if it is wrong.
#
# `command -v python` finds whatever PATH happens to hold, and PATH is not
# reliable here: /etc/profile rebuilds it for a login shell and drops the
# /venv/bin that Docker's ENV put there. This resolved to /usr/local/bin/python
# — the base image's interpreter, which has none of the agent's packages — and
# `pantheon --help` died on `No module named 'fire'` with fire sitting in /venv
# all along.
#
# Not guarded on "is it unset". This file is sourced repeatedly, and the first
# sourcing may already have recorded the wrong answer; a guard that only fills
# in a blank would preserve it forever. A value that cannot import the agent is
# replaced.
_pantheon_runtime_ok() {
    [ -x "$1" ] && "$1" -c 'import fire' >/dev/null 2>&1
}
if ! _pantheon_runtime_ok "${PANTHEON_RUNTIME_PYTHON:-}"; then
    for _c in "${VIRTUAL_ENV:-}/bin/python" /venv/bin/python \
              "$(command -v python 2>/dev/null)" "$(command -v python3 2>/dev/null)"; do
        if _pantheon_runtime_ok "$_c"; then
            PANTHEON_RUNTIME_PYTHON="$_c"
            break
        fi
    done
    unset _c
fi
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
# Remove an activation hook an earlier version of this setup wrote onto the
# Volume, BEFORE activating anything — activating is what runs it.
#
# The hook put the baseline's lib on LD_LIBRARY_PATH so borrowed conda
# packages could find libmkl. That directory also holds libpython3.12.so.1.0,
# so the system python loaded conda's libpython, reported conda's sys.version,
# and cloudpickle died parsing it before the agent could start: rc=1, every
# boot.
#
# pantheon-analysis-env also deletes it, and that was not enough — it runs in
# the background, long after this file has been sourced, the environment
# activated and the agent exec'd. A cleanup that only takes effect next boot
# does not help the boot it is needed on. This is the last moment before the
# damage is done.
for _stale in "$PANTHEON_USER_PREFIX"/micromamba/envs/*/etc/conda/activate.d/zzz-pantheon-baseline.sh \
              "$PANTHEON_USER_PREFIX"/micromamba/envs/*/etc/conda/deactivate.d/zzz-pantheon-baseline.sh; do
    [ -f "$_stale" ] && rm -f "$_stale"
done
unset _stale

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

# The read-only stack in the image that the personal environment inherits from.
# Its bin goes on PATH BEHIND the personal env's, so anything the user installs
# shadows the baseline copy rather than the other way round.
export PANTHEON_BASELINE_ENV="${PANTHEON_BASELINE_ENV:-/opt/pantheon/envs/pantheon-base}"
if [ -x "$PANTHEON_BASELINE_ENV/bin/python" ]; then
    _pantheon_prepend_path "$PANTHEON_BASELINE_ENV/bin"
    export PATH

    # The baseline's lib is published, and deliberately NOT put on any
    # search path. Every attempt to do so broke something else: given to the
    # agent, the system python loaded conda's libpython and cloudpickle killed
    # the sandbox; given to interactive shells, htop loaded conda's libncursesw
    # and segfaulted. A directory holding a whole second copy of libc's
    # neighbours cannot be made globally visible safely.
    #
    # pantheon-analysis-env instead links the few libraries the borrowed
    # packages actually need INTO the personal environment's own lib, where
    # that python already looks and nothing else does.
    export PANTHEON_BASELINE_LIB="$PANTHEON_BASELINE_ENV/lib"

fi
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

    # Activate it properly, so `conda env list` marks it and the prompt says
    # which env you are in — those read CONDA_PREFIX and CONDA_DEFAULT_ENV,
    # which the PATH entry above does not set.
    #
    # ONCE, and inherited from there. Activation reads the env's metadata off
    # the Volume, which is network-backed: measured at ~520 ms a time, and
    # doing it per interactive shell put that on every terminal a person
    # opened. The entrypoint sources this before the agent starts, so the
    # agent, every pty and every `bash -c` inherit the result, and the guard
    # below turns each of those into a no-op.
    if [ "${CONDA_DEFAULT_ENV:-}" != "$PANTHEON_ANALYSIS_ENV" ] \
       && command -v micromamba >/dev/null 2>&1; then
        micromamba activate "$PANTHEON_ANALYSIS_ENV" 2>/dev/null || true
    fi

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

# R's own search path has to reach the baseline too, or Seurat and the
# Bioconductor packages in the image are invisible from the personal
# environment — the same inheritance the .pth gives Python, for R.
# R_LIBS is searched after R_LIBS_USER, so a package the user installs still
# wins over the image's copy.
if [ -d "$PANTHEON_BASELINE_ENV/lib/R/library" ]; then
    export R_LIBS="$PANTHEON_BASELINE_ENV/lib/R/library${R_LIBS:+:$R_LIBS}"
fi

# ------------------------------------------------------------- rust / go
# The binary persists; the build and module caches deliberately do not. They are
# large, they are write-heavy, and the Volume is network-backed — the same
# reason the entrypoint already sends pip/HF/torch caches to ephemeral /root.
export CARGO_INSTALL_ROOT="$PANTHEON_USER_PREFIX"
export GOBIN="$PANTHEON_USER_PREFIX/bin"

# ------------------------------------------------- snapshot generation
# The full evaluation just ran; record its outcome so the next sourcing can
# replay one file instead (see the fast path at the top). Atomic write, and
# best-effort throughout: a failure here costs the fast path, never the shell.
_pantheon_write_userspace_snapshot() {
    local tmp="$_PANTHEON_SNAPSHOT.tmp.$$"
    local ana_marker="$MAMBA_ROOT_PREFIX/envs/${PANTHEON_ANALYSIS_ENV:-analysis}/.pantheon-ready"
    local ana_state="absent"
    [ -f "$ana_marker" ] && ana_state="present"
    {
        printf '# Generated by pantheon-userspace.sh — do not edit; delete to force re-evaluation.\n'
        # Validity: the one piece of state whose change must be discovered by
        # the next shell (the whole point of re-sourcing). A mismatch makes
        # this replay return non-zero, and the caller falls through to the
        # full evaluation.
        if [ "$ana_state" = "present" ]; then
            printf '[ -f %q ] || return 1\n' "$ana_marker"
        else
            printf '[ ! -f %q ] || return 1\n' "$ana_marker"
        fi
        printf '[ "${PANTHEON_USER_PREFIX:-%s}" = %q ] || return 1\n' "$PANTHEON_USER_PREFIX" "$PANTHEON_USER_PREFIX"
        # PATH entries as guarded prepends (idempotent, same as the live code),
        # emitted in the order the full run prepends them so the final ordering
        # matches. Everything else is a frozen export.
        local d
        for d in "$PANTHEON_USER_PREFIX/opt/bin" "$PANTHEON_USER_PREFIX/opt/usr/bin" \
                 "$PANTHEON_USER_PREFIX/bin" "$PANTHEON_BASELINE_ENV/bin" \
                 "$MAMBA_ROOT_PREFIX/envs/${PANTHEON_ANALYSIS_ENV:-analysis}/bin"; do
            case ":$PATH:" in
                *":$d:"*) printf 'case ":$PATH:" in *%q*) ;; *) PATH=%q:"$PATH";; esac\n' ":$d:" "$d" ;;
            esac
        done
        printf 'export PATH\n'
        local v
        for v in PANTHEON_USER_PREFIX PANTHEON_RUNTIME_PYTHON PANTHEON_ANALYSIS_ENV \
                 PANTHEON_ANALYSIS_PYTHON PANTHEON_BASELINE_ENV PANTHEON_BASELINE_LIB \
                 MAMBA_ROOT_PREFIX MAMBA_PKGS_DIRS LD_LIBRARY_PATH MANPATH PIP_TARGET \
                 CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_SHLVL npm_config_prefix NODE_PATH \
                 R_LIBS_USER R_LIBS CARGO_INSTALL_ROOT GOBIN; do
            if [ -n "${!v+x}" ]; then printf 'export %s=%q\n' "$v" "${!v}"; fi
        done
        # The shell functions the hook installed (micromamba/conda/mamba).
        declare -f micromamba conda mamba 2>/dev/null || true
        printf 'return 0 2>/dev/null || true\n'
    } > "$tmp" 2>/dev/null && mv -f "$tmp" "$_PANTHEON_SNAPSHOT" 2>/dev/null || rm -f "$tmp" 2>/dev/null
}
_pantheon_write_userspace_snapshot 2>/dev/null || true
unset -f _pantheon_write_userspace_snapshot
unset _PANTHEON_SNAPSHOT
