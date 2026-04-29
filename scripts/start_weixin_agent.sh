#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs

x-cmd weixin bot service start

if [ -f logs/agent-service.pid ]; then
  pid="$(cat logs/agent-service.pid)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "already running: $pid"
    exit 0
  fi
fi

nohup scripts/run_weixin_agent.sh > logs/agent-service.log 2>&1 &
echo "$!" > logs/agent-service.pid
echo "started: $(cat logs/agent-service.pid)"
