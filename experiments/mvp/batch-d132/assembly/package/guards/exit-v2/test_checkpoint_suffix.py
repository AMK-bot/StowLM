"""CPU fake reproduces observed MLX suffix-appending behavior for both saves."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pytest

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').exists())
import llm.exit
STAGE = Path(llm.exit.__file__).resolve().parent


def load_dpo(source):
    path=STAGE/'dpo.py' if source=='patched' else ROOT/'src/llm/exit/dpo.py'
    spec=importlib.util.spec_from_file_location('d135_dpo_'+source,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trace(tokens=(1, 2)):
    return dict(prompt=[5, 6], tokens=list(tokens), allowed_sets=[[1, 2, 3] for _ in tokens])

def pair():
    return dict(chosen_verified=True, chosen=trace(), rejected=trace((2, 3)))

@pytest.mark.parametrize('source',['patched'])
def test_checkpoint_extension_and_optimizer_commit_resume(tmp_path, monkeypatch,source):
    """Original fails exactly as production did; patch commits/resumes both saves."""
    dpo=load_dpo(source)
    from llm.exit import runtime
    from llm.stowlm import train as trainer
    from llm.stowlm import planner
    from stowage.model import Provenance
    parent, child = tmp_path/'parent', tmp_path/'child'
    cfg = dict(model='base', fine_tune_type='lora', num_layers=1, lora_parameters={'rank':1})
    for folder in (parent, child):
        folder.mkdir()
        (folder/'adapter_config.json').write_text(json.dumps(cfg))
        (folder/'adapters.safetensors').write_bytes(b'SFT')
        (folder/'adapters.safetensors.provenance.json').write_text(json.dumps({
            'training':{'base_model_content_sha256':'base-sha'}, 'provenance':Provenance().model_dump(mode='json')}))
    (parent/'adapters.safetensors').write_bytes(b'generation-parent')
    sft_child_identity = dpo.adapter_identity(child, include_lineage=False)
    monkeypatch.setattr(dpo, '_admit_pair_instances', lambda *a, **kw: ['instance'])
    monkeypatch.setattr(dpo, 'reference_scorer', lambda model: lambda trace: -1.)
    monkeypatch.setattr(dpo, 'mlx_pair_loss', lambda *a, **kw: np.array(.5))
    monkeypatch.setattr(planner, 'StowLMPlanner', lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(runtime, '_clean_parent', lambda p: Provenance())
    restored = []
    model = SimpleNamespace(train=lambda: None, eval=lambda: None,
        load_weights=lambda path, strict: restored.append(path),
        trainable_parameters=lambda: {'w':np.array([2.])}, parameters=lambda: {})
    installed = []
    def resident(p):
        installed.append(p.adapter_path)
        return model
    monkeypatch.setattr(runtime, '_resident_for', resident)
    monkeypatch.setattr(runtime, '_RESIDENT', {'model':model, 'identity':None})
    saved_paths=[]
    def save_arrays(path, weights):
        # Matches the observed production leftover:
        # 0000001_adapters.safetensors.tmp.safetensors
        destination=Path(path if str(path).endswith('.safetensors') else str(path)+'.safetensors')
        saved_paths.append((str(path),str(destination)))
        payload={key:value.tolist() if isinstance(value,np.ndarray) else value for key,value in weights.items()}
        destination.write_text(json.dumps(payload))
    mx = SimpleNamespace(eval=lambda *a: None, random=SimpleNamespace(seed=lambda s:None), save_safetensors=save_arrays)
    nn = SimpleNamespace(value_and_grad=lambda model, fn: lambda m: (fn(m), {}))
    class Adam:
        def __init__(self, learning_rate): self.state = {}
        def update(self, model, gradients): self.state['step'] = self.state.get('step',0)+1
    optim = SimpleNamespace(Adam=Adam)
    package = SimpleNamespace(core=mx, nn=nn, optimizers=optim)
    for name, module in [('mlx',package), ('mlx.core',mx), ('mlx.nn',nn), ('mlx.optimizers',optim),
                         ('mlx.utils',SimpleNamespace(tree_flatten=lambda x:list(x.items())))]:
        monkeypatch.setitem(sys.modules, name, module)
    # Use the real CPU-accessible save_optimizer_state wrapper so the optimizer
    # also passes through the same MLX suffix behavior, not a permissive fake.
    optimizer_restores = []
    def restore(opt, model, path):
        optimizer_restores.append(str(path))
        opt.state = json.loads(path.read_text())
    monkeypatch.setattr(trainer, 'load_optimizer_state', restore)
    def publish(out, provenance, training):
        (out/'adapters.safetensors.provenance.json').write_text(json.dumps({
            'training':training, 'provenance':provenance.model_dump(mode='json')}))
    monkeypatch.setattr(trainer, 'write_adapter_sidecar', publish)
    if source=='original':
        with pytest.raises(FileNotFoundError,match='0000001_adapters.safetensors.tmp'):
            dpo.train_preference(parent, child, [pair()], child, steps=2)
        assert (child/'dpo/0000001_adapters.safetensors.tmp.safetensors').exists()
        assert not list((child/'dpo').glob('*_state.json'))
        return
    result = dpo.train_preference(parent, child, [pair()], child, steps=2)
    assert result['resumed_from'] == 0
    assert installed and set(installed) == {str(child)}
    assert result['reference'] == 'frozen_sft_child'
    assert result['reference_identity'] == sft_child_identity
    assert json.loads((child/'dpo/reference.json').read_text())['identity']['parent'] == sft_child_identity
    assert (child/'adapters.safetensors').read_bytes() == json.dumps({'w':[2.]}).encode()
    assert dpo.read_complete_checkpoint(child/'dpo')['iteration'] == 2
    # The legacy SFT trainer may republish its finished SFT checkpoint on restart.
    (child/'adapters.safetensors').write_bytes(b'SFT')
    result = dpo.train_preference(parent, child, [pair()], child, steps=2)
    assert result['resumed_from'] == 2
    assert result['pairs_scored'] == 0
    assert optimizer_restores[-1].endswith('0000002_optimizer.safetensors')
    assert restored[-1].endswith('0000002_adapters.safetensors')
    assert (child/'adapters.safetensors').read_bytes() == json.dumps({'w':[2.]}).encode()
    assert not list(child.glob('*_state.json'))
    assert all(requested==actual and requested.endswith('.safetensors') for requested,actual in saved_paths)
    assert {Path(path).name for path,_ in saved_paths}=={
        '.tmp-0000001_adapters.safetensors','.tmp-0000001_optimizer.safetensors',
        '.tmp-0000002_adapters.safetensors','.tmp-0000002_optimizer.safetensors'}
    assert json.loads((child/'dpo/0000002_optimizer.safetensors').read_text())=={'step':2}
    assert not list((child/'dpo').glob('*.tmp.safetensors'))
    assert not list((child/'dpo').glob('.tmp-*'))

