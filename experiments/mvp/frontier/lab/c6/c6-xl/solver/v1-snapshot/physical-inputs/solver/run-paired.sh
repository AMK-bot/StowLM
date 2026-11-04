#!/bin/zsh
# Input: predeclared TSV with approved one-map-list path, then comma-separated arms.
# Every arm is a fresh guarded interpreter. This shell performs no numerical work.
set -eu
XL=experiments/mvp/frontier/lab/c6/c6-xl
PAIRS=${1:?supply frozen pair-list path}
INDEX=${2:-0}
SNAPSHOT=${3:-$XL/solver/INPUT-SNAPSHOT.json}
check_inputs() {
  .venv/bin/python -B - "$SNAPSHOT" <<'PY'
import hashlib,json,sys
from pathlib import Path
manifest=json.loads(Path(sys.argv[1]).read_text())
drift=[]
for item in manifest['files']:
    path=Path(item['path'])
    actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    if actual!=item['sha256']:
        drift.append(item['path'])
if drift:
    print('INPUT DRIFT; stop measurements until reviewed:',drift,flush=True)
    raise SystemExit(42)
PY
}
check_inputs
while IFS=$'\t' read -r MAPLIST ARMS; do
  [[ -z "$MAPLIST" || "$MAPLIST" == \#* ]] && continue
  INDEX=$((INDEX+1))
  ARMS=${ARMS:-variant}
  for ARM in ${(s:,:)ARMS}; do
    while [[ -e experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING ]]; do
      sleep 10
    done
    if [[ "$ARM" == baseline ]]; then
      METHOD="$XL/bench/baseline/method.py"
    elif [[ "$ARM" == variant ]]; then
      METHOD="$XL/solver/method.py"
    else
      print -u2 -- "Unknown predeclared arm: $ARM"
      exit 2
    fi
    OUT="$XL/results/xl-$ARM/$(printf '%02d' "$INDEX")"
    .venv/bin/python -B "$XL/bench/run.py" \
      --maps "$MAPLIST" --method "${METHOD}:propose" --finish "${METHOD}:finish" \
      --out "$OUT" --budget-s 240 --finalize-s 60 --hard-s 1200 \
      --memory-gb 56 --tries 1 --seed 0 --resume
    check_inputs
  done
done < "$PAIRS"
