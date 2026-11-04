#!/bin/zsh
# Four shard workers, native threads 1, no GPU. Writes each PID; waits for all four.
cd /Users/anda/Projects/meta-hackathon-stowage
export PYTHONPATH=src:. PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1 STOWAGE_VESSEL_SINGLEFLIGHT=1
Q=experiments/mvp/gold/quality-d149
for s in 0 1 2 3; do
  .venv/bin/python $Q/measure.py run $s 4 > $Q/shard-$s.log 2>&1 &
  echo $! >> $Q/pids.txt
done
wait
echo done
