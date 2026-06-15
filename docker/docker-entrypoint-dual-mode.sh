#!/bin/bash
set -e

echo "========================================="
echo "Pantheon Docker Container"
echo "Mode: ${PANTHEON_MODE:-hub}"
echo "========================================="

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
    python -m pantheon ui \
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

    # Initialize workspace structure
    mkdir -p /workspace/.pantheon
    echo "✓ Ensured .pantheon directory exists"

    # Clear stale project-level templates before forcing an explicit factory
    # materialization. Normal startup uses runtime factory fallback and does not
    # copy factory templates.
    if [ "${PANTHEON_RESET_TEMPLATES}" = "true" ]; then
        echo "Clearing stale project-level templates..."
        rm -rf /workspace/.pantheon/agents /workspace/.pantheon/teams /workspace/.pantheon/prompts /workspace/.pantheon/skills /workspace/.pantheon/.factory_hashes.json
        echo "✓ Stale templates cleared"
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

    # Execute the command with ID_HASH parameter
    if [ $# -eq 0 ]; then
        # No arguments provided, use default command with ID_HASH
        exec python -m pantheon.chatroom --id_hash="${ID_HASH}" ${SYNC_FLAG}
    else
        # Arguments provided, pass them to pantheon.chatroom with ID_HASH
        # This ensures ID_HASH is always used for stable service_id generation
        exec python -m pantheon.chatroom --id_hash="${ID_HASH}" ${SYNC_FLAG} "$@"
    fi
fi
