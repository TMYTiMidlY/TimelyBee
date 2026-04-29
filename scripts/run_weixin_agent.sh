#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f /home/timidly/.hermes/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /home/timidly/.hermes/.env
  set +a
fi

export AGENT_PROVIDER="${AGENT_PROVIDER:-minimax}"
export AGENT_MODEL="${AGENT_MODEL:-MiniMax-M2.7}"
export ENABLED_CHANNELS="${ENABLED_CHANNELS:-weixin}"

x-cmd weixin bot service start

exec uv run agent-service run --channels weixin
