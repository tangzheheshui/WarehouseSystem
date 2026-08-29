#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"
PYTHONPYCACHEPREFIX=.pycache exec python3 -m unittest discover -s tests/cases -v
