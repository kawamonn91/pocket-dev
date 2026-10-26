#!/usr/bin/env bash
# 固定トークンを .env に書き出す（URL が起動ごとに変わらなくなる）
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ] && grep -q POCKETDEV_TOKEN .env; then
    echo ".env に POCKETDEV_TOKEN が既にあります:"
    grep POCKETDEV_TOKEN .env
    exit 0
fi

token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
echo "POCKETDEV_TOKEN=${token}" >> .env
echo "生成しました: ${token}"
echo "docker compose up -d --build で反映されます"
