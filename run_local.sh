#!/usr/bin/env bash
set -euo pipefail

export DATABASE_URL="${DATABASE_URL:-sqlite:///fraternity.db}"
export HOST="${HOST:-127.0.0.1}"
export LOCAL_PORT="${LOCAL_PORT:-8080}"
export DEBUG="${DEBUG:-True}"

python app.py
