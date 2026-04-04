#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BRANCH="${BRANCH:-master}"
SERVICE_NAME="${SERVICE_NAME:-proj25-dash}"

git fetch origin
git checkout "$BRANCH" >/dev/null 2>&1 || true
git pull --ff-only origin "$BRANCH"

if command -v docker >/dev/null 2>&1; then
  if [ -f "docker-compose.yml" ] || [ -f "compose.yaml" ] || [ -f "compose.yml" ]; then
    if docker compose version >/dev/null 2>&1; then
      docker compose up -d --build
    else
      docker-compose up -d --build
    fi
    exit 0
  fi
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

if command -v systemctl >/dev/null 2>&1; then
  systemctl restart "$SERVICE_NAME"
  systemctl status "$SERVICE_NAME" --no-pager || true
fi
