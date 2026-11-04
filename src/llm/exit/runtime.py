"""Local Expert Iteration GPU boundary; importing this module never imports MLX.

Candidates are deliberately unchecked: the selection module must gate them before
training. Training uses the existing trainer's admission, checkpoints and lineage.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields
from dataclasses import replace
import gc
import hashlib
import json
from pathlib import Path
import threading
import time

_LOCK = threading.RLock()
_PLANNER = None
_RESIDENT = None
MLX_CACHE_LIMIT_BYTES = 2 << 30


def configure_mlx_cache(mx=None, *, limit_bytes=MLX_CACHE_LIMIT_BYTES):
    """Bound MLX's reusable buffers before loading the one resident base.

    The cache limit bounds *unused buffers*, not live tensors or process RSS.
    Importing this module remains CPU-only; this runs at the GPU boundary.
    Refuse to load a model when this MLX version cannot enforce the limit.
    ``mx`` is injectable so CPU tests never initialize Metal.
    """
    if type(limit_bytes) is not int or not 0 <= limit_bytes <= MLX_CACHE_LIMIT_BYTES:
        raise ValueError('MLX cache limit must be an integer between 0 and 2 GiB')
    if mx is None:
        import mlx.core as mx
    setter = getattr(mx, 'set_cache_limit', None)
    backend = 'mlx.core.set_cache_limit'
    if not callable(setter):
        setter = getattr(getattr(mx, 'metal', None), 'set_cache_limit', None)
        backend = 'mlx.core.metal.set_cache_limit'
    if not callable(setter):
        raise RuntimeError('MLX cache limit unavailable; refusing resident model loading')
    try:
        previous = setter(limit_bytes)
        # Returning buffers from an earlier phase promptly avoids waiting for
        # the next allocation to enforce the new limit.
        clear = getattr(mx, 'clear_cache', None)
        if not callable(clear):
            clear = getattr(getattr(mx, 'metal', None), 'clear_cache', None)
        if not callable(clear):
            raise RuntimeError('MLX clear_cache unavailable')
        clear()
    except Exception as error:
        raise RuntimeError('MLX cache limit configuration failed; refusing resident model loading') from error
    report = dict(limit_bytes=limit_bytes, setter=backend,
                  previous_limit_bytes=previous if type(previous) is int else None,
                  scope='reusable_buffers_only', single_resident_model=True)
    print(json.dumps(dict(event='mlx_cache_configured', **report)), flush=True)
    return report


def preflight_mlx_device(mx=None):
    """Check this process can use Metal before claiming a smoke attempt.

    A host without a usable Metal device is an execution-context failure, not
    evidence about an adapter. No model weights or test inputs are loaded here.
    Injection keeps CPU unit tests independent of the installed MLX runtime.
    """
    try:
        if mx is None:
            import mlx.core as mx
        available=getattr(getattr(mx,'metal',None),'is_available',None)
        if not callable(available):
            raise RuntimeError('MLX Metal availability API unavailable')
        if available() is not True:
            raise RuntimeError('No Metal device available to this process')
        memory_cache=configure_mlx_cache(mx)
    except Exception as error:
        raise RuntimeError(f'MLX device preflight failed: {error}') from error
    return dict(metal_available=True,memory_cache=memory_cache)


def decoder_factory():
    """Share the exact layer order with verification and dataset admission."""
    from llm.exit.selection import decoder_factory as shared_factory
    return shared_factory()


def release_model():
    """Release the resident base when handing the GPU to another owner."""
    global _PLANNER, _RESIDENT
    with _LOCK:
        _PLANNER = None
        _RESIDENT = None
        gc.collect()
        # Do not initialize Metal just to clear a cache on a CPU-only invocation.
        import sys
        mx = sys.modules.get('mlx.core')
        if mx is not None:
            mx.clear_cache()


def _clean_parent(planner):
    from stowage.frozen import require_clean
    provenance = planner._adapter_provenance()
    require_clean(provenance, what='Expert Iteration parent')
    if planner._adapter_base_model_sha256() is None:
        raise ValueError('parent adapter must bind a base-model content SHA-256')
    return provenance


def _architecture(parent):
    raw = json.loads((Path(parent) / 'adapter_config.json').read_text())
    if raw.get('fine_tune_type', 'lora') != 'lora':
        raise ValueError('Expert Iteration requires LoRA adapters')
    return {key: raw[key] for key in ('num_layers', 'lora_parameters')}


def _unfreeze_lora(model):
    """Trainer calls freeze recursively; re-enable only existing LoRA leaves."""
    model.freeze()
    found = False
    for _, module in model.named_modules():
        keys = [key for key in ('lora_a', 'lora_b') if hasattr(module, key)]
        if keys:
            module.unfreeze(recurse=False, keys=keys, strict=True)
            found = True
    if not found:
        raise ValueError('resident model has no LoRA trainable leaves')


def _load_parent_weights(model, parent, *, load_arrays=None, flatten=None):
    """Require EXACT trainable tensor names and shapes before assigning any.

    strict=False is needed because frozen base weights are absent from an adapter;
    this explicit check prevents it from hiding missing or extra LoRA tensors.
    """
    if load_arrays is None:
        import mlx.core as mx
        load_arrays = mx.load
    if flatten is None:
        from mlx.utils import tree_flatten
        flatten = tree_flatten
    weights = load_arrays(str(Path(parent) / 'adapters.safetensors'))
    expected = dict(flatten(model.trainable_parameters()))
    missing, extra = sorted(expected.keys()-weights.keys()), sorted(weights.keys()-expected.keys())
    shapes = [name for name in weights.keys() & expected.keys()
              if tuple(weights[name].shape) != tuple(expected[name].shape)]
    if not expected or missing or extra or shapes:
        raise ValueError(f'parent adapter tensor mismatch: missing={missing}, extra={extra}, shapes={sorted(shapes)}')
    model.load_weights(list(weights.items()), strict=False)


def _resident_for(planner):
    """One base model stays resident; compatible adapters swap only LoRA arrays."""
    global _RESIDENT, _PLANNER
    provenance = _clean_parent(planner)  # always before MLX/model loading
    architecture = _architecture(planner.adapter_path)
    # The weight-bound provenance includes the adapter digest, so different bytes
    # at the same path cannot silently reuse a previously loaded adapter.
    identity = provenance.model_dump(mode='json')
    if _RESIDENT is None:
        device_config = preflight_mlx_device()
        cache_config = device_config['memory_cache']
        model = planner._load()
        _unfreeze_lora(model)
        _load_parent_weights(model, planner.adapter_path)
        _RESIDENT = dict(model=model, model_path=str(Path(planner.model_path).resolve()),
            base_sha=planner._loaded_content_identity['model_content_sha256'],
            architecture=architecture, identity=identity,
            mlx_cache=cache_config,
            mlx_device=device_config,
            config=json.loads((Path(planner.model_path) / 'config.json').read_text()))
    else:
        if str(Path(planner.model_path).resolve()) != _RESIDENT['model_path']:
            raise ValueError('resident base model differs; clear cache explicitly before changing bases')
        planner._check_adapter_base(_RESIDENT['base_sha'])
        if architecture != _RESIDENT['architecture']:
            raise ValueError('adapter architecture differs from resident LoRA; refusing swap')
        model = _RESIDENT['model']
        if identity != _RESIDENT['identity']:
            _unfreeze_lora(model)
            _load_parent_weights(model, planner.adapter_path)
            _RESIDENT['identity'] = identity
    model.eval()
    # Retain only this planner's validated lineage, not an older adapter's.
    planner._model = model
    planner._loaded_adapter_provenance = provenance.model_copy(deep=True)
    _PLANNER = planner
    return model


def _sample_batches(sampler, model, prompt, decoders, *, temperature, max_tokens, seed,
                    batch_size=None):
    """Greedy row 0 and k-1 sampled rows share each bounded lock-step batch.

    B >= k gives one prompt prefill. Smaller B uses ceil(k/B) microbatches;
    only absolute sample 0 is greedy, including when B=1.
    """
    if batch_size is not None and batch_size < 1:
        raise ValueError('decode batch size must be positive')
    if not decoders:
        return [], []
    results, timings = [], []
    width = batch_size or len(decoders)
    temperatures = [0.0] + [temperature] * (len(decoders)-1)
    for start in range(0,len(decoders),width):
        group = decoders[start:start+width]
        temps = temperatures[start:start+width]
        sampled, stats = sampler(model, prompt, group, temperature=temps,
                                 max_tokens=max_tokens, seed=seed+start)
        results.extend(sampled)
        timings.append(dict(temperature=temps, batch=len(group), **stats.summary()))
    return results, timings


def generate(instance_path, parent, k=4, *, temperature=0.8, seed=0,
             model_path=None, max_tokens=20_000, engine_config=None, identity_role='train', corpus_root=None,
             batch_size=None, incumbent_provider=None, early_pruning=True):
    """Return complete raw plans plus all terminal statuses; never finalize/check.

    ``parent`` is an adapter directory. This process retains one loaded adapter
    between maps. The base stays resident through training; release it when changing GPU owner.
    """
    if k < 1 or max_tokens < 1 or temperature < 0 or (batch_size is not None and batch_size<1):
        raise ValueError('k/max_tokens must be positive; temperature nonnegative')
    from llm.stowlm.planner import StowLMPlanner, DEFAULT_MODEL
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from llm.stowlm.sft_data import codec_for
    from stowage.io import load_instance
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.actions import get_action_space
    from stowage.moves import count_moves
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if rule_freeze_v1_enabled():
        raise RuntimeError('Expert Iteration requires the v4 rule-freeze switch OFF')
    instance = load_instance(instance_path)
    from llm.exit.identity import identity_guard, DEFAULT_CORPUS
    identity_guard(corpus_root or DEFAULT_CORPUS).require_allowed(instance,role=identity_role,
                                                                input_path=instance_path)
    started = time.perf_counter()
    with _LOCK:
        model_path = str(model_path or DEFAULT_MODEL)
        parent = str(Path(parent).resolve())
        planner = StowLMPlanner(model_path=model_path, adapter_path=parent)
        model = _resident_for(planner)
        from llm.stowlm.mask_mlx import generate_masked
        loaded = time.perf_counter()
        tokenizer = load_tokenizer()
        # APPROXIMATION: match the MVP gold path's C2-off production engine;
        # full checker + layered replay remain mandatory admission gates.
        config = engine_config or FeasibilityConfig(extra={'global_screen': 0})
        engine = make_feasibility_engine(instance, get_action_space(), config=config)
        decoders = decoder_factory()(instance, engine, codec_for(instance, tokenizer), None, k)
        pruning = None
        if early_pruning and k > 1:
            from llm.exit.pruning import PruningController,sampler_is_append_only
            pruning = PruningController(instance, incumbent_provider=incumbent_provider)
            append_only=sampler_is_append_only(generate_masked)
            decoders = [pruning.wrap_decoder(d, sample_id=i, exempt=(i == 0),
                        append_only=append_only)
                        for i,d in enumerate(decoders)]
        prompt = encode_prompt(instance, tokenizer)
        prepared = time.perf_counter()
        results, batches = _sample_batches(generate_masked, model, prompt, decoders,
            temperature=temperature, max_tokens=max_tokens, seed=seed, batch_size=batch_size)
        candidates, samples = [], []
        for i, (result, decoder) in enumerate(zip(results, decoders)):
            row = dict(sample=i, status=result.status, tokens=len(result.tokens),
                       logprob=result.logprob, temperature=0.0 if i == 0 else temperature,
                       token_ids=list(result.tokens),
                       prefix_actions=[get_action_space().encode(a) for a in decoder.actions])
            samples.append(row)
            if result.status == 'complete':
                plan = decoder.engine.to_plan()
                plan = plan.model_copy(update={'provenance': plan.provenance.merge(_PLANNER.provenance)})
                candidates.append(dict(source='model', plan=plan,
                    weighted_moves=count_moves(instance, plan).weighted_nonproductive, **row))
        pruning_report=pruning.summary() if pruning else {'enabled':False,'pruned_samples':0}
        if pruning is not None:
            pruning_report.update(enabled=append_only,sampler_append_only=append_only,
                greedy_fitness_exempt=True,disabled_reasons=dict(
                    (str(i),d.disabled_reason) for i,d in enumerate(decoders) if not d.enabled))
        from llm.exit.securing_guard import securing_metrics
        return dict(candidates=candidates, samples=samples, batches=batches,pruning=pruning_report,
                    memory_config=dict(_RESIDENT['mlx_cache']),
                    securing=securing_metrics(decoders),
                    layers=['production', 'lookahead', 'securing'], timings={
                        'load_s': loaded-started, 'prepare_s': prepared-loaded,
                        'decode_s': time.perf_counter()-prepared,
                        'wall_s': time.perf_counter()-started})


@contextmanager
def _parent_initialization(trainer, lora_utils, parent, parent_provenance, resident=None, model_utils=None):
    """Scoped hook for the legacy trainer, restored even when training fails.

    Do not invoke another trainer in another thread while this context is active.
    The wrapper's lock serializes all Expert Iteration generation/training calls.
    Existing complete child checkpoints still override initialization on resume.
    """
    original_layers = lora_utils.linear_to_lora_layers
    original_provenance = trainer.training_provenance
    original_load = None if model_utils is None else model_utils.load_model

    def layers(model, *args, **kwargs):
        if resident is not None and model is resident['model']:
            _unfreeze_lora(model)  # trainer.freeze() froze the existing adapters
            result = None
        else:
            result = original_layers(model, *args, **kwargs)
        _load_parent_weights(model, parent)
        return result

    def provenance(cfg, data, base, inherited):
        return original_provenance(cfg, data, base, [parent_provenance, *inherited])

    def load_model(path, *args, **kwargs):
        if str(Path(path).resolve()) != resident['model_path']:
            raise ValueError('trainer requested a different base from resident model')
        return resident['model'], resident['config']

    lora_utils.linear_to_lora_layers = layers
    trainer.training_provenance = provenance
    if model_utils is not None:
        model_utils.load_model = load_model
    try:
        yield
    finally:
        lora_utils.linear_to_lora_layers = original_layers
        trainer.training_provenance = original_provenance
        if model_utils is not None:
            model_utils.load_model = original_load


def recovery_loss_offset(example):
    recovery=example.meta.get('exit_recovery')
    if recovery is None:
        return example.prompt_len
    offset=recovery.get('loss_start')
    prefix=recovery.get('prefix_tokens')
    if (type(offset) is not int or not isinstance(prefix,list) or not prefix
            or not example.prompt_len < offset < len(example.input_ids)
            or prefix != example.input_ids[example.prompt_len:offset]):
        raise ValueError('recovery loss offset is not bound to its exact prefix tokens')
    return offset


def _bind_recovery_identity(data):
    bindings=[]
    from llm.stowlm.sft_data import example_sha256
    for example in data.train+data.val:
        offset=recovery_loss_offset(example)
        if offset != example.prompt_len:
            bindings.append((example.split,example.instance_id,example_sha256(example),offset,
                             example.meta['exit_recovery']['prefix_tokens']))
    if not bindings:
        return data
    identity={**data.identity,'exit_recovery_offsets':bindings}
    identity['entries_sha256']=hashlib.sha256(json.dumps(dict(
        entries=data.identity['entries_sha256'],recovery=bindings),sort_keys=True).encode()).hexdigest()
    return replace(data,identity=identity)


@contextmanager
def _recovery_dataset(trainer):
    """Keep full-plan replay intact; only the training loss boundary changes."""
    original_dataset,original_admit=trainer.TokenDataset,trainer.admit_dataset
    class RecoveryDataset(original_dataset):
        def __init__(self,examples,max_seq_length):
            super().__init__(examples,max_seq_length)
            self._items=[(tokens,recovery_loss_offset(example))
                         for (tokens,_),example in zip(self._items,examples)]
    trainer.TokenDataset=RecoveryDataset
    trainer.admit_dataset=lambda cfg:_bind_recovery_identity(original_admit(cfg))
    try:
        yield
    finally:
        trainer.TokenDataset,trainer.admit_dataset=original_dataset,original_admit


def train(parent, dataset_dir, output_dir, steps=120, lr=1e-4, *, seed=0, **overrides):
    """Continue the parent's LoRA weights on checked winners using fresh Adam.

    APPROXIMATION: each generation resets Adam moments because its dataset changes;
    same-generation interrupted runs use the original trainer's exact resume.
    LoRA architecture is inherited, and architecture-changing overrides are refused.
    """
    if steps < 1 or lr <= 0:
        raise ValueError('steps and lr must be positive')
    if Path(parent).resolve() == Path(output_dir).resolve():
        raise ValueError('child output must differ from parent')
    from llm.stowlm import train as trainer
    from llm.stowlm.planner import StowLMPlanner
    parent = Path(parent).resolve()
    raw = json.loads((parent / 'adapter_config.json').read_text())
    parent_cfg = raw.get('stowlm_train_config', {})
    if not parent_cfg:
        raise ValueError('parent must declare stowlm_train_config (cannot infer LoRA architecture)')
    inherited = {f.name: parent_cfg[f.name] for f in fields(trainer.TrainConfig)
                 if f.name in parent_cfg}
    architecture = {'rank', 'scale', 'dropout', 'num_layers', 'keys', 'model'}
    for key in architecture & overrides.keys():
        if overrides[key] != inherited.get(key):
            raise ValueError(f'cannot mutate inherited LoRA architecture: {key}')
    inherited.update(overrides)
    inherited.update(data_dir=str(dataset_dir), adapter_dir=str(output_dir),
                     iters=steps, learning_rate=lr, seed=seed, diagnostic=False, resume=True)
    # Short runs cannot afford whole-corpus loss evaluation every 50 steps.
    inherited.update(steps_per_eval=steps, val_batches=1, steps_per_save=steps)
    inherited['extra'] = {**inherited.get('extra', {}),
        'exit_parent': str(parent), 'exit_optimizer': 'fresh Adam per generation'}
    cfg = trainer.TrainConfig(**inherited)
    planner = StowLMPlanner(model_path=cfg.model, adapter_path=parent)
    if (cfg.grad_accumulation_steps < 1 or cfg.iters % cfg.grad_accumulation_steps
            or cfg.steps_per_save % cfg.grad_accumulation_steps):
        raise ValueError('steps/save interval must be multiples of positive grad_accumulation_steps')
    expected_architecture = dict(num_layers=cfg.num_layers, lora_parameters=trainer.lora_parameters(cfg))
    if _architecture(parent) != expected_architecture:
        raise ValueError('parent adapter_config and inherited trainer LoRA architecture disagree')
    provenance = _clean_parent(planner)
    _bind_recovery_identity(trainer.admit_dataset(cfg))  # CPU admission before any MLX import
    with _LOCK:
        _resident_for(planner)
        from mlx_lm.tuner import utils as lora_utils
        from mlx_lm import utils as model_utils
        try:
            with _parent_initialization(trainer, lora_utils, parent, provenance,
                                        resident=_RESIDENT, model_utils=model_utils):
                with _recovery_dataset(trainer):
                    return trainer.train(cfg)
        finally:
            # Trained tensors no longer match the previously loaded adapter bytes.
            # The next generate() explicitly installs its selected parent/child.
            _RESIDENT['identity'] = None
            _RESIDENT['model'].eval()
            import mlx.core as mx
            gc.collect()
            mx.clear_cache()


# Public spelling used by the generation orchestrator.
clear_model_cache = release_model
