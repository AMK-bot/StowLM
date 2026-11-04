#!/bin/sh
# One map -> fresh cold construction -> exact finishing -> shared full checker.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$here/../../../../../.." && pwd)
cd "$root"
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "usage: sh run-map.sh MAP.json [fresh-output-name]" >&2
    exit 2
fi
map=$1
name=${2:-map-$$}
case "$name" in
    ''|*[!A-Za-z0-9._-]*) echo "output name must be a simple directory name" >&2; exit 2 ;;
esac
if [ ! -f "$map" ]; then
    echo "map file does not exist: $map" >&2
    exit 2
fi
out="$here/results/$name"
if [ -e "$out" ]; then
    echo "use a fresh output name: $out" >&2
    exit 2
fi
mkdir -p "$out"
printf '%s\n' "$map" > "$out/input-list.txt"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1 STOWAGE_IN_PORT_WORKERS=1
export STOWAGE_VESSEL_CACHE_DIR="$here/results/vessel-cache"
export C4_DETAIL_DIR="$out/details"
export PYTHONPATH="$root/src:$root/experiments/mvp/frontier:$root/experiments/mvp/frontier/lab"
exec nice -n 5 "$root/.venv/bin/python" -u \
    "$root/experiments/mvp/frontier/lab/score.py" \
    --set "$out/input-list.txt" --method "$here/method.py:propose" \
    --finish "$here/method.py:finish" --budget-s 300 --finalize-s 1140 \
    --tries 1 --workers 1 --seed 0 --out "$out"
