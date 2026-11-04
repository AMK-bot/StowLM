#!/bin/zsh
# Waits for the stand-in replan (its PID is $1), then computes the physics of the plan under the rule. Nice, 2 workers. Restart `--v6` after.
cd /Users/anda/Projects/meta-hackathon-stowage
while ps -p $1 >/dev/null; do /bin/sleep 20; done
[ -f experiments/mvp/ui/v6-hero/after.plan.json ] || { echo "no plan under the rule; the chapter holds the stored plan"; exit 1; }
export PYTHONPATH=.:src:experiments/mvp/frontier:experiments/mvp/frontier/lab OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
exec nice -n 10 .venv/bin/python experiments/mvp/ui/precompute_timeline.py --instance experiments/mvp/pipeline/package/fixtures/complete/instance.json --plan experiments/mvp/ui/v6-hero/after.plan.json --workers 2
