#!/bin/sh
# CPU part of task 5.1 (uniform random sampling under the mask). Lookahead 0 is the default mask; lookahead 8 is
# sampled less because the engine's legal_actions on some successor states (after forced hatch restows) takes
# seconds (see README). Shared Mac, 4 workers, nice.
set -e
cd "$(dirname "$0")/../.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
M="nice uv run python experiments/mask/measure_cpu.py --workers 4"
[ -s experiments/mask/cpu-tiny.jsonl ] || $M --rung tiny --sources v2-random v2-sim --instances 50 --out experiments/mask/cpu-tiny.jsonl
[ -s experiments/mask/cpu-small.jsonl ] || $M --rung small --sources sim --instances 50 --samples 2 --census v2-random v2-sim sim --out experiments/mask/cpu-small.jsonl
$M --rung medium --sources sim --instances 20 --samples 5 --lookahead 0 --census v2-random v2-sim sim --out experiments/mask/cpu-medium.jsonl
$M --rung medium --sources sim --instances 20 --samples 1 --lookahead 8 --max-wall 60 --out experiments/mask/cpu-medium-la8.jsonl
$M --rung full --sources sim --instances 5 --samples 4 --lookahead 0 --max-wall 300 --census v2-random v2-sim sim --out experiments/mask/cpu-full.jsonl
# full checker on completed tiny plans (finalize ballast has a 30 s limit per plan)
$M --rung tiny --sources v2-random v2-sim --instances 15 --lookahead 0 --check --check-lookahead 0 --out experiments/mask/cpu-tiny-check.jsonl
