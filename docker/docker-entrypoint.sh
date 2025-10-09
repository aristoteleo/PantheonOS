#!/bin/bash
set -e

echo "========================================="
echo "Pantheon ChatRoom Docker Entrypoint"
echo "========================================="

# Default ID_HASH if not provided
ID_HASH=${ID_HASH:-"default"}

echo "Environment:"
echo "  ID_HASH: ${ID_HASH}"
echo "  PANTHEON_REMOTE_BACKEND: ${PANTHEON_REMOTE_BACKEND}"
echo "  NATS_SERVERS: ${NATS_SERVERS}"
echo "  QDRANT_LOCATION: ${QDRANT_LOCATION}"
echo "  WORKSPACE: $(pwd)"
echo ""

# Wait for services to be ready
echo "Waiting for NATS server..."
timeout 30 bash -c 'until curl -sf http://nats:8222/healthz > /dev/null 2>&1; do sleep 1; done' || {
    echo "ERROR: NATS server is not ready"
    exit 1
}
echo "✓ NATS is ready"

echo "Waiting for Qdrant server..."
timeout 30 bash -c 'until curl -sf http://qdrant:6333/healthz > /dev/null 2>&1; do sleep 1; done' || {
    echo "ERROR: Qdrant server is not ready"
    exit 1
}
echo "✓ Qdrant is ready"

echo ""
echo "========================================="
echo "Starting Pantheon ChatRoom"
echo "========================================="

# Execute the command with ID_HASH parameter
if [ $# -eq 0 ]; then
    # No arguments provided, use default command with ID_HASH
    exec python -m pantheon.chatroom --id_hash="${ID_HASH}"
else
    # Arguments provided, execute them
    exec "$@"
fi
