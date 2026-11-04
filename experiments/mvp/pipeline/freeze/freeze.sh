#!/bin/bash
# Freeze-time entry point; unit tests use only readonly command previews.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT" || exit 1
FREEZE="$ROOT/experiments/mvp/pipeline/freeze"
export UV_CACHE_DIR="$ROOT/.uv-cache" PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="src:experiments/mvp/frontier:experiments/mvp/frontier/lab"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
scope=pipeline
pipeline_manifest="$FREEZE/manifest-pipeline.json"
print_commands=0
after_hero=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --scope|--pipeline-manifest)
            [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
            if [[ "$1" == '--scope' ]]; then scope="$2"; else pipeline_manifest="$2"; fi
            shift 2 ;;
        --print-commands) print_commands=1; shift ;;
        --after-hero) after_hero=1; shift ;;
        --help) echo "usage: $0 [--scope pipeline|film] [--pipeline-manifest PATH] [--after-hero] [--print-commands]"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ "$scope" == pipeline || "$scope" == film ]] || { echo "unknown scope: $scope" >&2; exit 2; }
[[ "$after_hero" == 0 || "$scope" == pipeline ]] || { echo '--after-hero requires pipeline scope' >&2; exit 2; }
manifest="$FREEZE/manifest-$scope.json"
build=(uv run --offline --no-sync python "$FREEZE/build.py" --scope "$scope")
if [[ "$scope" == film ]]; then build+=(--pipeline-manifest "$pipeline_manifest"); fi
verify=(uv run --offline --no-sync python "$FREEZE/verify.py" --require-frozen "$manifest")
suites=(bash "$FREEZE/suites.sh" --out-dir "$FREEZE/suites/$scope")
# The guard supplies the trusted digest; the hero check cannot accept a caller's guess.
hero_check=(uv run --offline --no-sync python -m experiments.mvp.pipeline.freeze.evidence
    --pipeline-manifest "$manifest" --only experiments/mvp/pipeline/package/fixtures/hero-run)
if [[ "$print_commands" == 1 ]]; then
    if [[ "$scope" == film ]]; then
        printf '%q ' "${suites[@]}"; printf '\n'
        printf '%q ' "${build[@]}"; printf '\n'
        printf '%q ' "${verify[@]}"; printf '\n'
    elif [[ "$after_hero" == 1 ]]; then
        printf '%q ' "${verify[@]}"; printf '\n'
        printf '%q ' "${hero_check[@]}"; printf '\n'
        printf '%q ' "${suites[@]}"; printf '\n'
        printf '%q ' "${verify[@]}"; printf '\n'
    else
        printf '%q ' "${build[@]}"; printf '\n'
        printf '%q ' "${verify[@]}"; printf '\n'
    fi
    exit 0
fi
if [[ "$after_hero" == 0 && -e "$manifest" ]]; then
    echo "manifest-$scope.json already exists; refusing to replace the freeze" >&2
    exit 1
fi
lock="$FREEZE/.freeze-$scope-lock"
if ! mkdir "$lock"; then
    echo "freeze is already running; inspect $lock before retrying" >&2
    exit 1
fi
trap 'rmdir "$lock"' EXIT
suite_code=0
if [[ "$scope" == pipeline && "$after_hero" == 0 ]]; then
    "${build[@]}" || exit 1
    "${verify[@]}" || exit 1
    echo 'Pipeline manifest ready. p111 must rebuild the hero package against it, then run freeze.sh --scope pipeline --after-hero for both suites.'
    exit 0
fi
if [[ "$after_hero" == 1 ]]; then
    "${verify[@]}" || exit 1
    "${hero_check[@]}" || exit 1
fi
"${suites[@]}" || suite_code=$?
if [[ "$scope" == film ]]; then "${build[@]}" || exit 1; fi
"${verify[@]}" || exit 1
echo "Freeze written and verified; suite exit status: $suite_code (see suites/$scope/*-counts.json)."
exit "$suite_code"
