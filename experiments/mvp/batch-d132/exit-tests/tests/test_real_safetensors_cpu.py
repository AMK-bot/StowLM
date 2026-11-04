"""Real tiny safetensors bytes, with MLX's observed suffix routing emulated.

mlx.core itself cannot import in the Codex sandbox: it requires a Metal device
even before set_default_device(cpu). This test executes no MLX/GPU operations.
"""
import ast
import os
from pathlib import Path

import numpy as np
import pytest
from safetensors.numpy import load_file, save_file


STAGE=Path('/private/tmp/stowlm-d132-exit-qualified-runtime-20261030/src/llm/exit')
ROOT=next(p for p in STAGE.parents if (p/'pyproject.toml').is_file())


def temporary_expression(source):
    tree=ast.parse(source.read_text())
    loop=next(node for node in ast.walk(tree) if isinstance(node,ast.For)
              and any(isinstance(item,ast.Name) and item.id=='save' for item in ast.walk(node.target)))
    assignment=next(node for node in loop.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='temporary' for target in node.targets))
    return compile(ast.Expression(assignment.value),str(source),'eval')


def test_actual_cpu_safetensors_save_rename_reload(tmp_path):
    original=temporary_expression(Path('/private/tmp/stowlm-d132-assembly-v2-20261030/baseline/src/llm/exit/dpo.py'))
    patched=temporary_expression(STAGE/'dpo.py')
    weights={'layers.0.lora_a':np.array([[.25,-.5]],dtype=np.float32)}
    for kind in ('adapters','optimizer'):
        final=tmp_path/f'0000001_{kind}.safetensors'
        bad=eval(original,{},dict(path=final))
        # Emulate only MLX's production-observed filename suffix rule; the
        # serialization and reload below use the real CPU safetensors library.
        save_file(weights,str(bad)+'.safetensors')
        with pytest.raises(FileNotFoundError):os.replace(bad,final)
        good=eval(patched,{},dict(path=final))
        assert good.suffix=='.safetensors'
        save_file(weights,str(good))
        os.replace(good,final)
        np.testing.assert_array_equal(load_file(str(final))['layers.0.lora_a'],weights['layers.0.lora_a'])
