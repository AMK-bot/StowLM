# Unquantised (bf16) MLX copy of Glimmer, so the adapter check can separate conversion error from 4-bit error.
# Same sanitize patch as experiments/glimmer-bench/convert.py. Usage: uv run python experiments/grpo-venue/convert_bf16.py OUT
import sys

from mlx_lm import convert
from mlx_lm.models import muse_glimmer

_sanitize = muse_glimmer.Model.sanitize
muse_glimmer.Model.sanitize = lambda self, w: _sanitize(self, {k: v for k, v in w.items() if not k.startswith("model.vision_")})
convert("meta-models/Muse-Glimmer-30B", mlx_path=sys.argv[1], quantize=False, dtype="bfloat16")
