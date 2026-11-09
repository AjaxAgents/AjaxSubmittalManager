#!/usr/bin/env bash
# Ajax Submittal Manager - one-command start for macOS/Linux.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
[ -x .venv/bin/python ] || "$PY" -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env - add your Anthropic API key (or set it in Settings)."; }
[ -f frontend/dist/index.html ] || echo "Prebuilt frontend missing: (cd frontend && npm install && npm run build)"
cd backend && exec python -m asm.main "$@"
