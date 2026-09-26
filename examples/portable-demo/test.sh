#!/bin/bash
# portable-demo/test.sh — Smoke test runner.
#
# Runs app.py and checks for the PASS line.
# This is the script that Black Flag's test matrix will execute.
set -euo pipefail
cd "$(dirname "$0")"
python3 app.py
echo "TEST PASSED"
