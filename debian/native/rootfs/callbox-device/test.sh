#!/bin/sh
# Run the device layer's tests (Python 3.12 or later, pytest). Used by CI and by hand.
set -eu
cd "$(dirname "$0")"
python3 -m pip install --quiet pytest==8.3.5
exec python3 -m pytest -q tests "$@"
