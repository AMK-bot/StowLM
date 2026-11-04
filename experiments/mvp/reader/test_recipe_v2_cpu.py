"""Standalone synthetic MLX CPU smoke: run explicitly, never Glimmer or GPU.

UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m \
    experiments.mvp.reader.test_recipe_v2_cpu
"""
from __future__ import annotations


def main():
    import json
    from pathlib import Path
    import tempfile
    from unittest.mock import patch

    import mlx.core as mx
    # Set CPU before creating any arrays, modules, or random state.
    mx.set_default_device(mx.cpu)
    import mlx.nn as nn
    from mlx.utils import tree_flatten
    import mlx_lm
    from mlx_lm.tuner.lora import LoRALinear
    from experiments.mvp.reader import train_v2 as recipe

    class TinyModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = [LoRALinear.from_base(nn.Linear(8, 8), r=2, scale=1.0)]

        def __call__(self, tokens):
            return self.layers[0](mx.eye(8)[tokens])

    class Tokenizer:
        def apply_chat_template(self, messages, *, tokenize, **_kwargs):
            return [1, 2, 3, 4] if tokenize else 'synthetic prompt'

        def encode(self, _text, **_kwargs):
            return [1, 2]

    model = TinyModel()
    mx.eval(model.parameters())
    base_before = mx.array(model.layers[0].linear.weight)
    bias_before = mx.array(model.layers[0].linear.bias)
    lora_before = mx.array(model.layers[0].lora_b)
    with tempfile.TemporaryDirectory(prefix='reader-v2-mlx-cpu-') as temp:
        folder = Path(temp)
        source, destination = folder / 'adapter-v1', folder / 'adapter-v2'
        source.mkdir()
        source_weights = {key: value for key, value in tree_flatten(model.parameters())
                          if key.rsplit('.', 1)[-1] in {'lora_a', 'lora_b'}}
        mx.save_safetensors(str(source / 'adapters.safetensors'), source_weights)
        source_hash = recipe.sha256(source / 'adapters.safetensors')
        cfg = json.loads((recipe.HERE / 'config-v2.json').read_text())
        cfg.update(rank=2, scale=1.0, num_layers=1, max_seq_length=16,
                   grad_checkpoint=False, steps_per_eval=4, steps_per_save=4,
                   val_batches=1, hard_stop_seconds=60)
        rows = [{'id': f'synthetic-{i}', 'messages': [
            {'role': 'system', 'content': 'synthetic'},
            {'role': 'user', 'content': 'synthetic'},
            {'role': 'assistant', 'content': 'synthetic'}]} for i in range(5)]

        def load(path, adapter_path):
            assert path == str(folder / 'synthetic-base')
            assert adapter_path == str(source)
            model.load_weights(list(mx.load(str(source / 'adapters.safetensors')).items()), strict=False)
            return model, Tokenizer()

        with patch.object(mlx_lm, 'load', load):
            result = recipe.run_train(rows, rows[:1], folder / 'synthetic-base', source,
                                      destination, cfg, {'synthetic_cpu_only': True})
        mx.eval(model.parameters())
        assert mx.default_device() == mx.cpu
        assert result['full_epoch_completed'] and result['rows_completed'] == 5
        assert result['optimizer_updates'] == 2
        assert mx.array_equal(model.layers[0].linear.weight, base_before).item()
        assert mx.array_equal(model.layers[0].linear.bias, bias_before).item()
        assert not mx.array_equal(model.layers[0].lora_b, lora_before).item()
        assert recipe.sha256(source / 'adapters.safetensors') == source_hash
        final = mx.load(str(destination / 'adapters.safetensors'))
        assert set(final) == set(source_weights)
        print(json.dumps({'device': 'cpu', 'synthetic_only': True, 'rows_completed': 5,
                          'optimizer_updates': 2, 'base_unchanged': True,
                          'source_unchanged': True, 'lora_changed': True}))


if __name__ == '__main__':
    main()
