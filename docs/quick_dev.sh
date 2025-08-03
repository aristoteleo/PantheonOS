#!/bin/bash
# Quick development server - uses current environment

# Check sphinx-autobuild is installed
if ! command -v sphinx-autobuild &> /dev/null; then
    echo "Installing sphinx-autobuild..."
    pip install sphinx-autobuild
fi

# Start dev server
echo "Starting docs server at http://localhost:8080"
sphinx-autobuild source build/html --host 127.0.0.1 --port 8080