#!/usr/bin/env bash
# Docker を使わずこのPC上で直接起動する（開発・動作確認用）
# 既定では 127.0.0.1 待受なので、このPCのブラウザからのみ接続できる。
set -euo pipefail
cd "$(dirname "$0")/.."

exec python3 -m agent.server \
    --root "${1:-./workspace}" \
    --bind "${POCKETDEV_BIND:-127.0.0.1}" \
    --port "${POCKETDEV_PORT:-8765}"
