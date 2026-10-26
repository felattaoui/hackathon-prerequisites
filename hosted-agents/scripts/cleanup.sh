#!/usr/bin/env bash
# Explicit cleanup of objects recorded in the kit ledger. Dry-run unless --execute --confirm <rg-name> is given.
#   scripts/cleanup.sh                                   # dry-run
#   scripts/cleanup.sh --env A --execute --confirm rg-hackathon-prerequisites-eaf --purge hapq-a-lab01
#   add --include-resource-group to delete the kit RG too (only if empty)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="./.conda/python.exe"; [ -x "$PY" ] || PY=python
exec "$PY" -m hapq cleanup "$@"
