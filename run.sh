#!/bin/sh
set -eu
cd "$(dirname "$0")"
PYTHONPYCACHEPREFIX=.pycache exec python3 server/main.py --host 127.0.0.1 --port 8000 "$@"
