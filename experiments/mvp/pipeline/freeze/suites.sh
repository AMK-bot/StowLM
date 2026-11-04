#!/bin/bash
# Run only at freeze time. Capacity admission is retried before each suite.
set -u
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT" || exit 1
FREEZE="$ROOT/experiments/mvp/pipeline/freeze"
export UV_CACHE_DIR="$ROOT/.uv-cache"
export PYTHONPATH="src:experiments/mvp/frontier:experiments/mvp/frontier/lab"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1

# Test-only entry points do not run pytest or wait for admission.
if [[ "${1:-}" == '--parse-junit' ]]; then
    shift
    exec uv run --offline --no-sync python "$FREEZE/suite_counts.py" "$@"
fi
if [[ "${1:-}" == '--check-admission' ]]; then
    shift
    exec uv run --offline --no-sync python "$FREEZE/admission.py" "$@"
fi

state="$ROOT/experiments/fleet/core-state.json"
out_dir="$FREEZE/suites/pipeline"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --state) state="$2"; shift 2 ;;
        --out-dir) out_dir="$2"; shift 2 ;;
        *) echo "usage: $0 [--state PATH] [--out-dir PATH]" >&2; exit 2 ;;
    esac
done
out_dir="$(uv run --offline --no-sync python "$FREEZE/paths.py" "$out_dir")" || exit 2
# Validate child outputs too: an existing XML, counts file or pytest temp path
# may itself be a symlink escaping an otherwise contained output directory.
for name in repo pipeline; do
    uv run --offline --no-sync python "$FREEZE/paths.py" \
        "$out_dir/$name.xml" "$out_dir/$name-counts.json" \
        "$out_dir/unit-tmp-$name" >/dev/null || exit 2
done
mkdir -p "$out_dir" || exit 1
failed=0
run_suite() {
    local name="$1" code=0
    shift
    uv run --offline --no-sync python "$FREEZE/admission.py" --state "$state" --wait || return 1
    rm -f "$out_dir/$name.xml" "$out_dir/$name-counts.json" || return 1
    uv run --offline --no-sync pytest "$@" -p no:cacheprovider \
        --basetemp="$out_dir/unit-tmp-$name" --junitxml="$out_dir/$name.xml" || code=$?
    uv run --offline --no-sync python "$FREEZE/suite_counts.py" "$out_dir/$name.xml" \
        --out "$out_dir/$name-counts.json" --suite "$name" --exit-code "$code" || return 1
    return "$code"
}
run_suite repo || failed=1
run_suite pipeline experiments/mvp/pipeline app/tests || failed=1
exit "$failed"
