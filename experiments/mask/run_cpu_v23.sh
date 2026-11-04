#!/bin/sh
# Task 5.1 re-measurement on engine v2.3 index arrays, fresh 3.5 generator instances (C2 off: linear epoch stale).
set -e
cd "$(dirname "$0")/../.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
M="nice uv run python experiments/mask/measure_cpu.py --workers 2 --sources fresh --lookahead 0"
$M --rung tiny --instances 100 --out experiments/mask/v23-tiny.jsonl
$M --rung small --instances 100 --out experiments/mask/v23-small.jsonl
$M --rung medium --instances 60 --max-wall 120 --out experiments/mask/v23-medium.jsonl
$M --rung full --instances 12 --max-wall 300 --out experiments/mask/v23-full.jsonl
# the old path (legal_actions()) on the same seeds, for the overhead comparison
$M --rung medium --instances 20 --max-wall 120 --action-path --out experiments/mask/v23-medium-actionpath.jsonl
$M --rung full --instances 6 --max-wall 300 --action-path --out experiments/mask/v23-full-actionpath.jsonl
# full checker on completed tiny plans
$M --rung tiny --instances 20 --check --check-lookahead 0 --out experiments/mask/v23-tiny-check.jsonl
