#!/bin/bash
set -euo pipefail
cd /Users/dongzusheng/Projects/vermes-electron/electron
echo "=== Prebuild ==="
bash ../scripts/sync-version.sh
echo ""
echo "=== electron-builder --win ==="
npx electron-builder --win --publish=never
echo ""
echo "=== Done ==="
