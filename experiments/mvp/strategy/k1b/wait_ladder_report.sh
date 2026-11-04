#!/bin/zsh
# Rebuilds K2-LADDER.md with the wall-budget labels once both ladder arms are done; the running drivers hold the older report code.
cd "$(dirname "$0")/../../../.."
while [[ ! -f experiments/mvp/strategy/k1b/k2r/K2R.DONE ]]; do /bin/sleep 60; done
PYTHONPATH=experiments/mvp/strategy/k1b:experiments/mvp/strategy .venv/bin/python experiments/mvp/strategy/k1b/ladder_report.py
