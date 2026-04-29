#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f logs/agent-service.pid ]; then
  echo "not running: missing logs/agent-service.pid"
  exit 0
fi

pid="$(cat logs/agent-service.pid)"
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "stopped: $pid"
else
  echo "not running: $pid"
fi
