# Convert the pinned Meta Muse Glimmer checkpoint to a quantized MLX text-only model.
# Usage: uv run python experiments/glimmer-bench/convert.py OUT_DIR [BITS, default 4]
import importlib.metadata
import json
import sys
from pathlib import Path

SOURCE_REPO = 'meta-models/Muse-Glimmer-30B'
SOURCE_REVISION = 'cec94fcfd6512f2cfe708138c32533b346ea4f69'


def conversion_provenance(bits, versions=None):
    return dict(
        source_repo=SOURCE_REPO, source_revision=SOURCE_REVISION,
        quantize=True, q_bits=bits, q_group_size=64, q_mode='affine',
        text_only=True, sanitizer_patch='drop model.vision_* before upstream sanitize',
        software=versions if versions is not None else {
            name: importlib.metadata.version(name) for name in ('mlx', 'mlx-lm')
        },
    )


def main():
    from mlx_lm import convert
    from mlx_lm.models import muse_glimmer

    # mlx-lm 0.32.2 drops vision_tower.* but Meta names it model.vision_*.
    original_sanitize = muse_glimmer.Model.sanitize

    def sanitize(self, weights):
        weights = {k: v for k, v in weights.items() if not k.startswith('model.vision_')}
        return original_sanitize(self, weights)

    bits = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    provenance = conversion_provenance(bits)
    muse_glimmer.Model.sanitize = sanitize
    try:
        convert(SOURCE_REPO, mlx_path=sys.argv[1], revision=SOURCE_REVISION,
                quantize=True, q_bits=bits, q_group_size=64, q_mode='affine')
    finally:
        muse_glimmer.Model.sanitize = original_sanitize
    (Path(sys.argv[1]) / 'conversion_provenance.json').write_text(
        json.dumps(provenance, indent=2) + '\n'
    )


if __name__ == '__main__':
    main()
