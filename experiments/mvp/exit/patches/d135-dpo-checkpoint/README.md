# D135 DPO checkpoint suffix fix — staged only

Apply `checkpoint-suffix.diff` at the next authorized fresh-process batch.
It changes only the shared adapter/optimizer checkpoint temporary filename:

- Before: `0000001_adapters.safetensors.tmp`
- After: `.tmp-0000001_adapters.safetensors`

The same corrected naming applies to optimizer state. MLX appends `.safetensors`
when the supplied filename lacks that final extension. Production evidence is
`deployment-d131/gen-000/child-1/preference/dpo/0000001_adapters.safetensors.tmp.safetensors`
(64,515,278 bytes): saving succeeded, then renaming the requested `.tmp` path
failed because that path did not exist.

Checkpoint iteration, hashes, commit-last state marker, resume selection,
optimizer state, frozen reference, loss, and SFT fallback behavior are unchanged.
This matches the suffix-preserving temporary naming already used by
`llm.stowlm.train.save_checkpoint`. JSON temporary files use plain text writes
and need no suffix change. Final adapter publication also already retains the
`.safetensors` extension and uses a byte copy.

Verification (CPU only, no MLX/GPU execution):

```sh
git apply --check experiments/mvp/exit/patches/d135-dpo-checkpoint/checkpoint-suffix.diff
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest experiments/mvp/exit/patches/d135-dpo-checkpoint/tests -q
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/mvp/exit/patches/d135-dpo-checkpoint/tests/run_overlay.py tests/llm/exit/test_dpo.py -q
```

Results: apply check passed; **2 new regressions + 32 existing DPO tests passed**.
The new fake MLX backend realistically appends the extension. Original source
reproduces the production failure; staged source saves adapter and optimizer
files, commits both hashes, resumes iteration 2, and republishes correctly.
The optimizer test uses the actual `trainer.save_optimizer_state` wrapper,
rather than a stub that bypasses MLX naming behavior. Reference cache reuse and
SFT republish/resume behavior are checked too.

No production source/tests or failed-run artifacts were edited. Testing used
one CPU, tiny fake arrays, no GPU, and required no heavy-job reservation. Actual
Metal training remains unverified until the next admitted batch. Existing
uncommitted `.tmp.safetensors` leftovers are ignored by checkpoint discovery;
this patch does not delete or reinterpret them.
