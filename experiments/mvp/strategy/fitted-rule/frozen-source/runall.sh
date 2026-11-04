cd /Users/anda/Projects/meta-hackathon-stowage
for spec in "fitT1 0,1" "masscap 0,1" "fitgreedy 0"; do set -- ${=spec}
env -u STOWAGE_BALLAST_V4 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 TOKENIZERS_PARALLELISM=false UV_CACHE_DIR=/Users/anda/Projects/meta-hackathon-stowage/.uv-cache uv run --offline --no-sync python /private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight/rulearm.py /private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight/rule-$1.jsonl $1 $2
done
