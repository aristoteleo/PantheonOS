#!/bin/bash

# Development server for Pantheon Agents documentation
# This script starts a live-reload server for documentation development

echo "🚀 Starting Pantheon Agents documentation development server..."

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in the docs directory
if [ ! -f "requirements.txt" ]; then
    echo "Error: Please run this script from the docs directory"
    exit 1
fi

# Show current Python environment
echo -e "${YELLOW}Using Python environment:${NC}"
which python
python --version
echo ""

# Install/upgrade dependencies in current environment
echo "📚 Installing documentation dependencies in current environment..."
pip install -U -r requirements.txt

# Clean previous builds (optional)
echo "🧹 Cleaning previous builds..."
make clean 2>/dev/null || true

# Configuration
HOST="${DOC_HOST:-127.0.0.1}"
PORT="${DOC_PORT:-8080}"
DELAY="${DOC_DELAY:-5}"

echo -e "${BLUE}Configuration:${NC}"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Delay: ${DELAY}s"
echo ""

# Start sphinx-autobuild with live reload
echo -e "${GREEN}✨ Documentation server starting...${NC}"
echo -e "${GREEN}📍 URL: http://$HOST:$PORT${NC}"
echo ""
echo "Features:"
echo "  • Live reload on file changes"
echo "  • Automatic browser refresh"
echo "  • Error display in browser"
echo "  • Incremental builds"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run sphinx-autobuild with options
sphinx-autobuild \
    --host "$HOST" \
    --port "$PORT" \
    --delay "$DELAY" \
    --watch "../pantheon" \
    --ignore "*.pyc" \
    --ignore "*.swp" \
    --ignore "*~" \
    --ignore ".git/*" \
    --ignore "build/*" \
    source build/html