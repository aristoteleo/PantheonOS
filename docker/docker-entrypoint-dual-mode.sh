#!/bin/bash
set -e

echo "========================================="
echo "Pantheon Docker Container"
echo "Mode: ${PANTHEON_MODE:-hub}"
echo "========================================="

# Everything this script starts in the background runs at a lower priority than
# the agent it is starting. A sandbox has TWO cores, and by the time the real
# interpreter begins importing there are up to four other jobs on them: this
# import prewarm, the userspace re-derivation, the analysis-env repair, and the
# user's own on-start hook. Measured 2026-08-28 across six boots: with a hook
# that finished in 2 s the agent subscribed at 4.7 s, with the same hook taking
# 5 s it subscribed at 8.2 s — and the entrypoint had reached `exec python` at
# t=0 in both, so nothing was WAITING on the hook. It was competing with it.
#
# None of the background work is on anyone's critical path; the agent's import
# is on everyone's. `nice` says exactly that and costs nothing when the cores
# are free.
NICE_BG="nice -n 10"

# Warm the agent's import set in the background while the shell below does its
# volume work. Modal lazy-loads image layers, so the first read of every .py
# and .so pays a network fetch — measured as the difference between a 3-5.5 s
# first import and a 0.84 s second one. Pulling the files now means the real
# `python -m pantheon.chatroom` at the end imports from hot cache. Best effort
# and fully detached: the boot never waits on it, and a failure costs nothing.
# Niced with the rest: what it is worth is the network fetch, not the CPU, and
# the CPU is the one thing it can take from the interpreter it is warming for.
( timeout 90 $NICE_BG "${PANTHEON_RUNTIME_PYTHON:-python}" -c "import pantheon.chatroom" >/dev/null 2>&1 & )

# Point pip, npm, R and apt at the Volume, so software a user installs is still
# there after the sandbox is recreated. Sourced (not run) so the exports reach
# the agent and every pty it spawns; see docker/pantheon-userspace.sh.
#
# Guarded on both sides: an older image may not carry the script, and a failure
# inside it must cost the user a package manager, never their sandbox — `set -e`
# is suspended for the whole of an `||` list, including the sourced body.
if [ -f /usr/local/bin/pantheon-userspace.sh ]; then
    . /usr/local/bin/pantheon-userspace.sh || echo "[userspace] skipped (non-fatal)"
    echo "  User prefix:   ${PANTHEON_USER_PREFIX:-unset}"
    echo "  Analysis env:  ${PANTHEON_ANALYSIS_PYTHON:-not built yet}"
    # The sourcing above may have replayed the snapshot (fast path). Re-derive
    # it from scratch in the background so drift the snapshot's own validity
    # guard cannot see still converges by the next boot. Detached; the boot
    # never waits on it.
    ( PANTHEON_USERSPACE_REBUILD=1 $NICE_BG bash -c '. /usr/local/bin/pantheon-userspace.sh' >/dev/null 2>&1 & )
fi

# Build or repair the analysis env. In the BACKGROUND, and deliberately so: it
# costs ~20 s of network the first time and almost nothing after, and startup
# latency is the one thing users feel every session. Until the env is ready the
# sandbox runs on /venv with the flat prefix, which is a working sandbox.
#
# EVERY boot, not only when the env is missing. Skipping it once the env exists
# was wrong twice over: an env whose creation was interrupted — a sandbox
# reclaimed mid-build — would keep its python and therefore never be repaired,
# and the kernelspec that lets the notebook toolset reach the env is written
# into the image, which is ephemeral, so it has to be re-registered each boot
# or jupyter silently loses the env after every rebuild. The script is
# idempotent and takes ~2 s when there is nothing to do (measured), against a
# first build of ~20 s.
if [ -x /usr/local/bin/pantheon-analysis-env ]; then
    ( $NICE_BG /usr/local/bin/pantheon-analysis-env > /tmp/pantheon-analysis-env.log 2>&1 || true ) &
fi

# ========== MODE DETECTION ==========
if [ "${PANTHEON_MODE}" = "standalone" ]; then
    echo "[STANDALONE MODE] Starting with auto-start-nats and auto-ui"

    # Standalone mode: for end users, starts NATS internally
    WORKSPACE=${WORKSPACE:-/workspace}
    FRONTEND_URL=${FRONTEND_URL:-https://pantheon-ui.aristoteleo.com}

    echo "Configuration:"
    echo "  Workspace: ${WORKSPACE}"
    echo "  Frontend URL: ${FRONTEND_URL}"
    echo ""

    # Initialize workspace
    mkdir -p "${WORKSPACE}/.pantheon"

    # Create .env template if not exists
    if [ ! -f "${WORKSPACE}/.env" ]; then
        cat > "${WORKSPACE}/.env.example" << 'EOF'
# ========================================
# Pantheon API Keys Configuration
# ========================================
#
# This is a template file. Your actual config is in .env
# If you need to reset your configuration, copy this file to .env
#
# After editing .env, restart the container to apply changes.
# Priority: .env > System defaults > settings.json
#
# ========================================

# OpenAI API Key (GPT-4, GPT-3.5, etc.)
# Uncomment and set your own key to use your OpenAI account
#OPENAI_API_KEY=sk-your-openai-key-here

# Anthropic API Key (Claude models)
# Uncomment and set your own key to use your Anthropic account
#ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here

# Google Gemini API Key
# Uncomment and set your own key to use your Google account
#GEMINI_API_KEY=your-gemini-key-here

# DeepSeek API Key
#DEEPSEEK_API_KEY=your-deepseek-key-here

# ========================================
# Advanced Configuration (Optional)
# ========================================

# Custom LiteLLM endpoint
#LITELLM_BASE_URL=https://your-litellm-proxy.com

# Debug mode
#DEBUG=false

# ========================================
# Notes:
# - If you don't set these keys, system default keys will be used
# - Using default keys will deduct quota from your account
# - After editing .env, restart the container to apply changes
# - .env is gitignored and won't be committed
# ========================================
EOF
        cp "${WORKSPACE}/.env.example" "${WORKSPACE}/.env"
        echo "✓ Created .env template at ${WORKSPACE}/.env"
        echo "  → Edit ${WORKSPACE}/.env to configure your API keys"
    else
        echo "✓ .env configuration file already exists"
    fi

    # Detect external port mapping (from environment variable or default 8080)
    # Users can specify via -e NATS_EXTERNAL_PORT=9000
    NATS_EXTERNAL_PORT="${NATS_EXTERNAL_PORT:-8080}"

    # Skip interactive configuration wizard (auto-skip in Docker environment)
    # Users should provide API keys via environment variables
    export SKIP_SETUP_WIZARD=1

    echo ""
    echo "========================================="
    echo "Starting Pantheon UI (Standalone Mode)"
    echo "========================================="
    echo ""
    echo "📡 NATS WebSocket will be available at:"
    echo "   ws://localhost:${NATS_EXTERNAL_PORT} (from host machine)"
    echo "   ws://<your-ip>:${NATS_EXTERNAL_PORT} (from external network)"
    echo ""
    echo "⏳ Starting services... (this may take a few seconds)"
    echo ""

    # Create temporary log file for URL capture
    LOG_FILE="/tmp/pantheon-startup.log"

    # Start command: use pantheon ui instead of pantheon.chatroom
    # Run in background with tee to display logs and capture to file
    "${PANTHEON_RUNTIME_PYTHON:-python}" -m pantheon ui \
        --workspace_path="${WORKSPACE}" \
        --auto-start-nats \
        --auto-ui="${FRONTEND_URL}" \
        "$@" 2>&1 | tee "${LOG_FILE}" &

    PANTHEON_PID=$!

    # Wait for service startup and capture connection URL
    echo "Waiting for connection URL..."
    MAX_WAIT=60  # Maximum wait time: 60 seconds
    WAIT_COUNT=0
    CONNECTION_URL=""

    while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
        # Check if process is still running
        if ! kill -0 $PANTHEON_PID 2>/dev/null; then
            echo ""
            echo "❌ ERROR: Pantheon process exited unexpectedly"
            echo "Check logs above for error details"
            exit 1
        fi

        # Try to extract connection URL from logs
        if [ -f "${LOG_FILE}" ]; then
            # Find lines containing full connection URL (with #/?nats=)
            CONNECTION_URL=$(grep -oP 'https?://[^/]+/.*#/\?nats=ws://[^&]+&service=[^&]+&auto=true' "${LOG_FILE}" | tail -1)

            if [ -n "$CONNECTION_URL" ]; then
                # Replace port in URL with user-specified external port
                # Replace ws://localhost:8080 or ws://0.0.0.0:8080 with user-specified port
                CONNECTION_URL=$(echo "$CONNECTION_URL" | sed "s|ws://[^:]*:8080|ws://localhost:${NATS_EXTERNAL_PORT}|g")

                # URL found, display prominent message
                echo ""
                echo "╔════════════════════════════════════════════════════════════════╗"
                echo "║                    🎉 Pantheon UI Ready!                       ║"
                echo "╚════════════════════════════════════════════════════════════════╝"
                echo ""
                echo "📋 Connection Information:"
                echo ""

                # Extract components
                NATS_WS=$(echo "$CONNECTION_URL" | grep -oP 'nats=\K[^&]+')
                SERVICE_ID=$(echo "$CONNECTION_URL" | grep -oP 'service=\K[^&]+')

                echo "  🌐 Frontend URL:"
                echo "     ${FRONTEND_URL}"
                echo ""
                echo "  📡 NATS WebSocket:"
                echo "     ${NATS_WS}"
                echo ""
                echo "  🔑 Service ID:"
                echo "     ${SERVICE_ID}"
                echo ""
                echo "  🔗 Full Connection URL (click to open):"
                echo "     ${CONNECTION_URL}"
                echo ""
                echo "╔════════════════════════════════════════════════════════════════╗"
                echo "║  👉 Copy the URL above and paste it in your browser           ║"
                echo "╚════════════════════════════════════════════════════════════════╝"
                echo ""
                echo "💡 Tips:"
                echo "  - To access from another device, replace 'localhost' with your machine's IP"
                echo "  - NATS monitoring dashboard: http://localhost:8222"
                echo "  - Press Ctrl+C to stop the container"
                echo ""

                break
            fi
        fi

        sleep 1
        WAIT_COUNT=$((WAIT_COUNT + 1))

        # Show progress every 10 seconds
        if [ $((WAIT_COUNT % 10)) -eq 0 ]; then
            echo "Still waiting for services to start... (${WAIT_COUNT}s)"
        fi
    done

    if [ -z "$CONNECTION_URL" ]; then
        echo ""
        echo "⚠️  WARNING: Could not detect connection URL automatically"
        echo "   The service may still be starting. Check the logs above."
        echo ""
    fi

    # Wait for main process
    wait $PANTHEON_PID

else
    # ========== HUB MODE (original logic) ==========
    echo "[HUB MODE] Starting as agent pod for Pantheon Hub"

    # Structural "default workspace" layout — GATED during rollout via
    # PANTHEON_DEFAULT_WORKSPACE so a build is a no-op until the hub opts in.
    # When enabled: the Volume (/workspace) becomes the user's HOME so ~/.pantheon
    # (projects.json, .env, settings, oauth) persists, and the Volume root is a
    # CONTAINER for many independent workspaces (default = default_workspace).
    # Off = legacy layout (HOME=/root, single workspace at the Volume root).
    DW_ENABLED="${PANTHEON_DEFAULT_WORKSPACE:-false}"
    if [ "$DW_ENABLED" = "true" ]; then
        export HOME=/workspace
        # Caches must NOT land on the Volume (bloat) — redirect to ephemeral /root.
        export XDG_CACHE_HOME=/root/.cache
        export HF_HOME=/root/.cache/huggingface
        export HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface
        export TORCH_HOME=/root/.cache/torch
        export PIP_CACHE_DIR=/root/.cache/pip
        export UV_CACHE_DIR=/root/.cache/uv
        export MPLCONFIGDIR=/root/.cache/matplotlib
        mkdir -p /root/.cache
    fi

    # Default ID_HASH if not provided
    ID_HASH=${ID_HASH:-"default"}

    echo "Environment:"
    echo "  ID_HASH: ${ID_HASH}"
    echo "  PANTHEON_REMOTE_BACKEND: ${PANTHEON_REMOTE_BACKEND}"
    echo "  NATS_SERVERS: ${NATS_SERVERS}"
    echo "  WORKSPACE: $(pwd)"
    echo ""

    # Wait for NATS server (if NATS_MONITOR_URL is set)
    if [ -n "$NATS_MONITOR_URL" ]; then
        echo "Waiting for NATS server at $NATS_MONITOR_URL..."
        timeout 30 bash -c "until curl -sf $NATS_MONITOR_URL/healthz > /dev/null 2>&1; do sleep 0.2; done" || {
            echo "ERROR: NATS server is not ready"
            exit 1
        }
        echo "✓ NATS is ready"
    else
        echo "Skipping NATS health check (NATS_MONITOR_URL not set)"
    fi

    echo ""
    echo "========================================="
    echo "Initializing Workspace"
    echo "========================================="

    DEFAULT_WS=/workspace/default_workspace

    if [ "$DW_ENABLED" = "true" ]; then
        MIGRATION_MARKER=/workspace/.pantheon/.migrated_to_default_workspace_v2
        # One-time migration (v2). The OLD layout used the Volume ROOT itself as the
        # single workspace; the NEW layout makes the root a pure CONTAINER. Move the
        # ENTIRE root workspace into default_workspace — loose files AND existing
        # project dirs — so default_workspace holds all your prior work, and the
        # root has only default_workspace + the global .pantheon (+ future SIBLING
        # workspaces you create). v2 also corrects an earlier build that wrongly
        # left project dirs at the root as siblings; it is guarded only by the
        # marker (not by default_workspace existing) so it re-runs to fix a v1 layout.
        if [ ! -f "$MIGRATION_MARKER" ]; then
            echo "Migrating Volume-root content into default_workspace (v2) ..."
            mkdir -p "$DEFAULT_WS"
            moved=0
            for entry in /workspace/*; do   # unquoted glob: matches NON-dotfiles only
                [ -e "$entry" ] || continue
                [ "$(basename "$entry")" = "default_workspace" ] && continue
                if mv "$entry" "$DEFAULT_WS/" 2>/dev/null; then moved=$((moved+1)); else echo "  (could not move $(basename "$entry"))"; fi
            done
            # The old root .pantheon (chats/brain/memory) becomes the default
            # workspace's — unless it already has one (a prior v1 migration moved it).
            if [ -d /workspace/.pantheon ] && [ ! -d "$DEFAULT_WS/.pantheon" ]; then
                mv /workspace/.pantheon "$DEFAULT_WS/.pantheon" 2>/dev/null || echo "  (could not move root .pantheon)"
            fi
            mkdir -p /workspace/.pantheon
            touch "$MIGRATION_MARKER"
            echo "✓ Migration v2 complete ($moved item(s) now under default_workspace)"
        fi
        mkdir -p /workspace/.pantheon "$DEFAULT_WS/.pantheon"
        echo "✓ Global store /workspace/.pantheon + default workspace $DEFAULT_WS ready"

        # The global factory cache is kept fresh by template_manager's
        # smart-overwrite: .factory_hashes.json records what the factory last
        # shipped, so an image upgrade updates exactly the files whose factory
        # content changed, preserves user edits, and skips the rest — an image
        # upgrade does NOT need this cache cleared. Clearing it here every boot
        # (the previous behaviour) cost ~7 s of network-volume rm plus ~4 s
        # re-materializing 180 files, on every cold start. Kept only as an
        # escape hatch for a corrupted cache.
        if [ "${PANTHEON_FORCE_FACTORY_REFRESH:-}" = "true" ]; then
            echo "Refreshing global factory template cache (forced)..."
            rm -rf /workspace/.pantheon/agents /workspace/.pantheon/teams /workspace/.pantheon/prompts /workspace/.pantheon/skills /workspace/.pantheon/.factory_hashes.json /workspace/.pantheon/.factory_fingerprint
            echo "✓ Factory cache cleared (re-materializes on startup)"
        fi
    else
        # Legacy layout: single workspace at the Volume root, ephemeral HOME.
        mkdir -p /workspace/.pantheon
        echo "✓ Ensured .pantheon directory exists"
        if [ "${PANTHEON_RESET_TEMPLATES}" = "true" ]; then
            echo "Clearing stale project-level templates..."
            rm -rf /workspace/.pantheon/agents /workspace/.pantheon/teams /workspace/.pantheon/prompts /workspace/.pantheon/skills /workspace/.pantheon/.factory_hashes.json
            echo "✓ Stale templates cleared"
        fi
    fi

    # Create .env.example template if not exists
    if [ ! -f /workspace/.env.example ]; then
        cat > /workspace/.env.example << 'EOF'
# ========================================
# Pantheon API Keys Configuration
# ========================================
#
# This is a template file. Your actual config is in .env
# If you need to reset your configuration, copy this file to .env
#
# After editing .env, click the reload button (🔄) to apply changes without restarting.
# Priority: .env > System defaults > settings.json
#
# ========================================

# OpenAI API Key (GPT-4, GPT-3.5, etc.)
# Uncomment and set your own key to use your OpenAI account
#OPENAI_API_KEY=sk-your-openai-key-here

# Anthropic API Key (Claude models)
# Uncomment and set your own key to use your Anthropic account
#ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here

# Google Gemini API Key
# Uncomment and set your own key to use your Google account
#GEMINI_API_KEY=your-gemini-key-here

# DeepSeek API Key
#DEEPSEEK_API_KEY=your-deepseek-key-here

# ========================================
# Advanced Configuration (Optional)
# ========================================

# Custom LiteLLM endpoint
#LITELLM_BASE_URL=https://your-litellm-proxy.com

# Debug mode
#DEBUG=false

# ========================================
# Notes:
# - If you don't set these keys, system default keys will be used
# - Using default keys will deduct quota from your account
# - After editing .env, click reload (🔄) to apply changes
# - .env is gitignored and won't be committed
# ========================================
EOF
        echo "✓ Created .env.example template"
    else
        echo "✓ .env.example already exists"
    fi

    # Auto-create .env from .env.example if not exists
    if [ ! -f /workspace/.env ]; then
        cp /workspace/.env.example /workspace/.env
        echo "✓ Created .env from template (auto-copied from .env.example)"
        echo "  → Edit /workspace/.env to configure your API keys"
        echo "  → Click reload button (🔄) after editing to apply changes"
    else
        echo "✓ .env configuration file already exists"
    fi

    echo ""
    echo "========================================="
    echo "Starting Pantheon ChatRoom"
    echo "========================================="

    # Build sync-templates flag
    SYNC_FLAG=""
    if [ "${PANTHEON_RESET_TEMPLATES}" = "true" ]; then
        SYNC_FLAG="--sync-templates"
    fi

    # Pantheon-Fleet: when this sandbox is wired to a fleet, join it as a Node in
    # the background so the agent can transfer files to/from it over the data plane
    # via a single transfer() interface (dst_node="local" resolves to this node).
    if [ -n "${FLEET_CONTROLLER_URL:-}" ] && command -v fleet >/dev/null 2>&1; then
        echo "[fleet] joining fleet as node sandbox-${ID_HASH} ..."
        mkdir -p /tmp/fleet-node
        fleet up --controller "${FLEET_CONTROLLER_URL}" --key "${FLEET_KEY}" \
            --name "sandbox-${ID_HASH}" --state-dir /tmp/fleet-node \
            > /tmp/fleet-node.log 2>&1 &
    fi

    # ── User setup hook ───────────────────────────────────────────────────
    #
    # Everything a user installs with apt lands in /usr, which is the image,
    # not the Volume — so it is gone the next time the sandbox is recreated,
    # and sandboxes are recreated often: on restart, after an idle reclaim,
    # and on every agent-image update. "I installed vim yesterday and today it
    # is missing" is the whole shape of the problem.
    #
    # The persistent half of the filesystem cannot hold binaries in system
    # paths, but it can hold the *instructions* for putting them back. This
    # runs one script the user owns, from the Volume, on every boot — so the
    # environment is declared rather than accumulated, which also means it
    # survives an image update instead of being erased by one.
    #
    #   <Volume>/.pantheon/on-start.sh
    #
    # Keyed off the Volume, NOT off HOME. Under the default_workspace layout
    # HOME *is* the Volume, but in the legacy layout HOME is /root, which is
    # ephemeral — a hook stored there would vanish with the container it was
    # meant to outlive, which is the exact bug this fixes. (Checked on a live
    # staging sandbox: HOME=/root, and the Volume is elsewhere.)
    #
    # Deliberately: not fatal, so a broken line cannot cost the user their
    # sandbox; bounded, so an accidental `read` cannot hang the boot forever;
    # and logged where both the user and support can find it.
    # WORKSPACE only gets its default in the standalone branch above, so it
    # may be unset here; /workspace is the Volume mount in both hub layouts.
    VOLUME_ROOT="${WORKSPACE:-/workspace}"
    SETUP_HOOK="${VOLUME_ROOT}/.pantheon/on-start.sh"
    if [ -f "$SETUP_HOOK" ]; then
        SETUP_LOG=/tmp/pantheon-on-start.log
        _run_setup_hook() {
            if timeout "${PANTHEON_SETUP_TIMEOUT:-300}" $NICE_BG bash "$SETUP_HOOK" > "$SETUP_LOG" 2>&1; then
                echo "[setup] ✓ finished"
            else
                rc=$?
                [ $rc -eq 124 ] && echo "[setup] ✗ timed out; continuing without it" \
                                || echo "[setup] ✗ exited $rc; continuing without it"
                tail -n 20 "$SETUP_LOG" 2>/dev/null | sed 's/^/[setup]   /'
            fi
            cp "$SETUP_LOG" "${VOLUME_ROOT}/.pantheon/on-start.log" 2>/dev/null || true
            # Tells the agent the guarantee below is satisfied. Written on
            # every outcome, including failure: "the hook is not going to
            # finish" is what a waiter needs to know, and a hook that failed
            # is not going to install anything by being waited for.
            : > /tmp/pantheon-on-start.done
        }
        # ALONGSIDE the boot, not before it. "The hook has finished before the
        # agent starts" is a guarantee users rely on — install a tool, then
        # ask the agent to use it — but only the caller that USES what it
        # installs can observe it, and putting it ahead of the boot cost every
        # boot four seconds (measured; the hook was re-installing a tool that
        # was already on the Volume). The wait moved to the shell and the
        # interpreter, which are where that guarantee is spent:
        # pantheon/utils/start_hook.py.
        #
        # PANTHEON_SETUP_HOOK_SERIAL=true restores the old order for a hook
        # that something outside those two paths depends on.
        rm -f /tmp/pantheon-on-start.done
        if [ "${PANTHEON_SETUP_HOOK_SERIAL:-}" = "true" ]; then
            echo "[setup] running $SETUP_HOOK (log: $SETUP_LOG) ..."
            _run_setup_hook
        else
            echo "[setup] running $SETUP_HOOK alongside the boot (log: $SETUP_LOG) ..."
            : > /tmp/pantheon-on-start.running
            ( _run_setup_hook & )
        fi
    fi

    # Run the endpoint IN the default workspace so work_dir (=cwd at import) makes
    # default_workspace the active project, while HOME=/workspace keeps ~/.pantheon
    # (global store) at the Volume root. Users create sibling workspaces under
    # /workspace and switch to them from the UI. (Legacy layout stays in /workspace.)
    if [ "$DW_ENABLED" = "true" ]; then
        cd "$DEFAULT_WS" || cd /workspace
    fi

    # Which interpreter is about to run the agent, and what it thinks it is.
    #
    # A baseline image had the agent crash on import with "failed to parse
    # CPython sys.version: '3.12.13 | packaged by conda-forge | ...'" —
    # cloudpickle cannot read conda's version string — so something was
    # selecting conda's python over the runtime venv. Reproducing it outside a
    # real sandbox failed: in the image alone, PANTHEON_RUNTIME_PYTHON resolves
    # correctly. This prints the answer where the failure actually happens.
    RUNPY="${PANTHEON_RUNTIME_PYTHON:-python}"
    echo "[launch] interpreter : $RUNPY -> $(command -v "$RUNPY" 2>/dev/null || echo '(not on PATH)')"
    # The sys.version/sys.prefix/ldd probes each spawn an interpreter (~0.3 s
    # apiece on a cold boot, ~1 s together), so the ones that cost a process
    # are gated. The env echoes below stay: they are free and they were the
    # lines that actually caught the libpython mixup.
    if [ "${PANTHEON_BOOT_DIAG:-}" = "true" ]; then
        echo "[launch] sys.version : $("$RUNPY" -c 'import sys; print(sys.version.replace(chr(10)," "))' 2>&1 | head -1)"
        echo "[launch] sys.prefix  : $("$RUNPY" -c 'import sys; print(sys.prefix)' 2>&1 | head -1)"
        echo "[launch] libpython   : $(ldd "$(readlink -f "$RUNPY")" 2>/dev/null | grep -i libpython | head -1)"
    fi
    echo "[launch] PATH head   : $(echo "$PATH" | cut -c1-120)"
    # Everything that can make a binary load a different libpython. Three
    # guesses at which one it was — a stale activate.d hook, LD_LIBRARY_PATH,
    # the activation itself — were all wrong, each disproved only after a
    # rebuild. The interpreter reports conda's sys.version, which lives in
    # libpython, so one of these is pointing it somewhere else; this prints
    # them at the moment it happens instead of guessing again.
    echo "[launch] LD_LIBRARY  : ${LD_LIBRARY_PATH:-unset}"
    echo "[launch] PYTHONHOME  : ${PYTHONHOME:-unset}"
    echo "[launch] PYTHONPATH  : ${PYTHONPATH:-unset}"
    echo "[launch] CONDA_PREFIX: ${CONDA_PREFIX:-unset}"

    # Execute the command with ID_HASH parameter
    if [ $# -eq 0 ]; then
        # No arguments provided, use default command with ID_HASH
        exec "${PANTHEON_RUNTIME_PYTHON:-python}" -m pantheon.chatroom --id_hash="${ID_HASH}" ${SYNC_FLAG}
    else
        # Arguments provided, pass them to pantheon.chatroom with ID_HASH
        # This ensures ID_HASH is always used for stable service_id generation
        exec "${PANTHEON_RUNTIME_PYTHON:-python}" -m pantheon.chatroom --id_hash="${ID_HASH}" ${SYNC_FLAG} "$@"
    fi
fi
