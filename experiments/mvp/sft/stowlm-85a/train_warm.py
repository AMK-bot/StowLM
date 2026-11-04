"""D209 8.5a: llm.stowlm.train unchanged, starting from 8.2's adapter weights instead of a fresh LoRA. Only the initial weights differ; a resume still loads
the checkpoint over them. Usage: train_warm.py <parent_adapter_dir> <train.py args>"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def install(parent: Path, out: Path):
    import mlx_lm.tuner.utils as utils
    from mlx.utils import tree_flatten
    import mlx.core as mx

    original, weights = utils.linear_to_lora_layers, parent/'adapters.safetensors'

    def warm(model, num_layers, config, *args, **kw):
        result = original(model, num_layers, config, *args, **kw)
        loaded = mx.load(str(weights))
        trainable = dict(tree_flatten(model.trainable_parameters()))
        if set(loaded) != set(trainable) or any(loaded[k].shape != trainable[k].shape for k in loaded):
            raise SystemExit('parent adapter does not fit this LoRA shape')
        model.load_weights(str(weights), strict=False)
        out.mkdir(parents=True, exist_ok=True)
        (out/'WARM_START.json').write_text(json.dumps(dict(parent=str(parent), keys=len(loaded)), indent=1)+'\n')
        return result
    utils.linear_to_lora_layers = warm


def main():
    parent, argv = Path(sys.argv[1]), sys.argv[2:]
    install(parent, Path(argv[argv.index('--adapter-dir')+1]))
    from llm.stowlm import train
    train.main(argv)


if __name__ == '__main__':
    main()
