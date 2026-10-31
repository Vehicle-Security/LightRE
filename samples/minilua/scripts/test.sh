#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
reports=reports
if [[ " ${*} " == *" --large "* ]]; then
  reports=large/reports
fi
mkdir -p "$reports"
python3 -u scripts/test.py "$@" 2>&1 | tee "$reports/tests.log"
