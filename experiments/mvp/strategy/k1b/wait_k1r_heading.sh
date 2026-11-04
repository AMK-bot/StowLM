#!/bin/zsh
# k2r's process holds the older K1R.md wording and rewrites the file at its end, so reapply the D198 heading after that.
cd "$(dirname "$0")/../../../.."
while [[ ! -f experiments/mvp/strategy/k1b/k2r/K2R.DONE ]]; do /bin/sleep 60; done
PYTHONPATH=experiments/mvp/strategy/k1b:experiments/mvp/strategy .venv/bin/python -c "import k1r_report; k1r_report.write_all()"
