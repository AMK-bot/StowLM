#!/bin/zsh
# The running null driver holds the judge.py with the wrong ROOT, so it stops after decode.DONE. Rerun it on the fixed code; decode is skipped.
cd "$(dirname "$0")/../../../.."
while kill -0 49156 2>/dev/null; do /bin/sleep 30; done
[[ -f experiments/mvp/strategy/k1b/null/NULL.DONE ]] || .venv/bin/python experiments/mvp/strategy/k1b/null_driver.py 4 2 >> experiments/mvp/strategy/k1b/null_driver.log 2>&1
