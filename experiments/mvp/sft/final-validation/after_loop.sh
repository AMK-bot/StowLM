#!/bin/zsh
# D152 step 3: when the loop stops, two-step on reserve25 (4 workers), then the one-map finisher diagnostic.
cd .
F=experiments/mvp/sft/final-validation
export PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 RAYON_NUM_THREADS=1 STOWAGE_VESSEL_SINGLEFLIGHT=1 UV_OFFLINE=1
n=0
while kill -0 10469 2>/dev/null; do sleep 30; n=$((n+1)); [ $n -gt 330 ] && { echo "loop still running after 165 min"; exit 3; }; done
echo "loop stopped $(date)"
.venv/bin/python $F/run_twostep.py experiments/mvp/valsplit/reserve_small25.txt reserve25 4 > $F/twostep-reserve25.log 2>&1; echo twostep rc=$?
.venv/bin/python $F/replay_timeout.py $F/8.2/reserve25/rows.json random.small.40100047.0.8 $F/8.2/reserve25/diagnostic-40100047.json > $F/replay-timeout.log 2>&1; echo replay rc=$?
