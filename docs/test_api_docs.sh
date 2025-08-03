#!/bin/bash

# Test script for API documentation build

echo "Testing API documentation build..."

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# Change to docs directory
cd "$(dirname "$0")"

# Clean previous builds
echo "Cleaning previous builds..."
make clean

# Try to build just the API docs
echo "Building API documentation..."
sphinx-build -b html -W source build/html 2>&1 | tee build.log

# Check if build succeeded
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ API documentation built successfully!${NC}"
    echo "View at: file://$(pwd)/build/html/api/index.html"
else
    echo -e "${RED}✗ API documentation build failed${NC}"
    echo "Check build.log for errors"
    
    # Show autodoc errors
    echo -e "\n${RED}Autodoc errors:${NC}"
    grep -i "autodoc" build.log | grep -i "error\|warning"
    
    # Show import errors
    echo -e "\n${RED}Import errors:${NC}"
    grep -i "import" build.log | grep -i "error\|failed"
fi

# Check which modules were successfully documented
echo -e "\nChecking generated API files..."
if [ -d "build/html/api" ]; then
    echo "Generated API documentation files:"
    ls -la build/html/api/*.html 2>/dev/null | awk '{print "  - " $9}'
else
    echo -e "${RED}No API documentation files generated${NC}"
fi