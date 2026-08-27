#!/bin/sh
set -eu
cd "$(dirname "$0")"
PYTHONPYCACHEPREFIX=.pycache exec python3 -m unittest discover -s tests -v
