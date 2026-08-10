#!/bin/bash
# 150 official test inputs + the AHC039 tester binary (~38MB), from the SimpleTES release.
# Not committed: it is large, it is not ours, and it never changes.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d cache/public_inputs_150 ] && [ -f cache/tester_binaries/ahc039_tester ]; then
    echo "cache already present -> $(pwd)/cache"
    exit 0
fi

echo "fetching AHC cache (~38MB)..."
curl -L --fail -o cache_ahc.zip \
  "https://drive.google.com/uc?export=download&id=1bA044QSbhsQWLjgs467ygoCpoxH3NevD"
python3 -c "import zipfile; zipfile.ZipFile('cache_ahc.zip').extractall('.')"
rm -f cache_ahc.zip
chmod +x cache/tester_binaries/* 2>/dev/null || true

echo "done. Now pull the image once:"
echo "  docker pull --platform linux/amd64 yimjk/ale-bench:cpp20-202301"
ls cache/
