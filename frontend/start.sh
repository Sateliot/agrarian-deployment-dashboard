#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8025}"
echo "Starting AGRARIAN Deployment Dashboard frontend on port $PORT"
cd "$(dirname "$0")"
python3 -m http.server "$PORT"





