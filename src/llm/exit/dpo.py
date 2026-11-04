"""Preference learning on checked winners, with a frozen SFT-child reference.

No MLX import occurs until a GPU function is called. The production replay scorer
returns NumPy, so it is used for the *frozen reference* only. The trainable path
reuses its mask/distribution helpers without converting tensors to NumPy.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Callable, Mapping, Sequence

SCHEMA = 'exit-masked-dpo-v2'


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def adapter_identity(parent, *, include_lineage=True) -> str:
    """Bind references to actual adapter bytes/config, never just a directory."""
    parent = Path(parent)
    digest = hashlib.sha256()
    names=['adapter_config.json','adapters.safetensors']
    if include_lineage and (parent/'adapters.safetensors.provenance.json').exists():
        names.append('adapters.safetensors.provenance.json')
    for name in names:
        digest.update(name.encode())
        with (parent / name).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


def validate_trace(trace: Mapping) -> None:
    prompt, tokens, sets = trace['prompt'], trace['tokens'], trace['allowed_sets']
    if not prompt or not tokens:
        raise ValueError('DPO requires a nonempty prompt and nonempty action-token trace')
    if len(sets) != len(tokens):
        raise ValueError('one allowed set is required per action token')
    for value in [*prompt, *tokens, *(t for allowed in sets for t in allowed)]:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('token IDs must be nonnegative integers')
    action_mask = trace.get('action_mask', [True] * len(tokens))
    if len(action_mask) != len(tokens) or not any(action_mask) or any(type(x) is not bool for x in action_mask):
        raise ValueError('nonempty boolean action-token mask required')
    for token, allowed in zip(tokens, sets):
        if not allowed or len(allowed) != len(set(allowed)) or token not in allowed:
            raise ValueError('token outside its unique nonempty allowed set')


def validate_pair(pair: Mapping) -> None:
    if pair.get('chosen_verified') is not True:
        raise ValueError('chosen plan must pass the full checker and layered mask replay')
    validate_trace(pair['chosen'])
    validate_trace(pair['rejected'])
    if pair['chosen']['prompt'] != pair['rejected']['prompt']:
        raise ValueError('preference traces must share the same map/prompt')
    if pair['chosen']['tokens'] == pair['rejected']['tokens']:
        raise ValueError('identical action-token traces carry no preference')


def pair_identity(pair: Mapping) -> str:
    validate_pair(pair)
    # Includes ordered masks: a changed production layer or replay set cannot
    # reuse likelihoods calculated under a different probability distribution.
    return _digest({'chosen': pair['chosen'], 'rejected': pair['rejected']})


def scalar_loss(chosen: float, rejected: float, ref_chosen: float,
                ref_rejected: float, *, chosen_tokens: int, beta: float = .1,
                sft_weight: float = .1) -> float:
    """CPU oracle: -log sigmoid(beta * relative preference) + chosen NLL."""
    if beta <= 0 or sft_weight < 0 or chosen_tokens < 1:
        raise ValueError('beta/token count must be positive; SFT weight nonnegative')
    if not all(math.isfinite(x) for x in (chosen, rejected, ref_chosen, ref_rejected)):
        raise ValueError('DPO requires finite masked sequence log-probabilities')
    margin = beta * ((chosen - rejected) - (ref_chosen - ref_rejected))
    softplus = max(-margin, 0.) + math.log1p(math.exp(-abs(margin)))
    return softplus - sft_weight * chosen / chosen_tokens


class ReferenceCache:
    """Parent-bound scalar scores, generated completely before the first update.

    The caller must install the named parent adapter before ``precompute``. These
    plain numbers cannot accidentally receive gradients or follow child updates.
    """
    def __init__(self, path, *, parent_identity: str, mask_identity: str):
        if not parent_identity or not mask_identity:
            raise ValueError('parent and production-mask identities are required')
        self.path = Path(path) if path is not None else None
        self.identity = dict(schema=SCHEMA, parent=parent_identity, mask=mask_identity,
                             temperature=1.0, action_tokens_only=True)
        self.scores: dict[str, dict[str, float]] = {}
        if self.path is not None and self.path.exists():
            raw = json.loads(self.path.read_text())
            if raw.get('identity') != self.identity:
                raise ValueError('reference cache parent/mask identity mismatch')
            self.scores = raw['scores']
            for record in self.scores.values():
                if set(record) != {'chosen', 'rejected'} or not all(
                        math.isfinite(float(x)) for x in record.values()):
                    raise ValueError('reference cache contains invalid likelihoods')

    def precompute(self, pairs: Sequence[Mapping], scorer: Callable[[Mapping], float]) -> dict:
        """Score each uncached pair once; commit cache only after a clean prepass."""
        started = perf_counter()
        pending = {}
        for pair in pairs:
            key = pair_identity(pair)
            if key in self.scores or key in pending:
                continue
            values = {side: float(scorer(pair[side])) for side in ('chosen', 'rejected')}
            if not all(math.isfinite(x) for x in values.values()):
                raise ValueError('unscorable preference: nonfinite reference likelihood')
            pending[key] = values
        self.scores.update(pending)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + '.tmp')
            tmp.write_text(json.dumps(dict(identity=self.identity, scores=self.scores),
                                      sort_keys=True, allow_nan=False))
            os.replace(tmp, self.path)
        return dict(reference_prepass_s=perf_counter()-started, pairs_scored=len(pending),
                    cache_pairs=len(self.scores))

    def get(self, pair: Mapping) -> tuple[float, float]:
        key = pair_identity(pair)
        if key not in self.scores:
            raise ValueError('reference prepass must finish before preference training')
        record = self.scores[key]
        return float(record['chosen']), float(record['rejected'])


def reference_scorer(model):
    """Production masked replay, T=1 raw policy, evaluated before child updates."""
    from llm.stowlm.mask_mlx import masked_logprobs

    def score(trace):
        validate_trace(trace)
        values = masked_logprobs(model, trace['prompt'], trace['tokens'],
                                    trace['allowed_sets'], temperature=1.)
        return float(sum(float(lp) for lp, keep in zip(values, trace.get('action_mask', [True]*len(values))) if keep))
    return score


def masked_action_logprob(model, trace: Mapping):
    """Differentiable sequence score, restricted to action tokens and their masks.

    A single teacher-forced forward includes prompt context in the gradient graph.
    Unlike production ``masked_logprobs``, no NumPy conversion severs gradients.
    APPROXIMATION: full-sequence forwards trade memory for a simple exact gradient;
    use short tiny/small traces and fail on a configured length cap upstream.
    """
    validate_trace(trace)
    import mlx.core as mx
    from llm.stowlm.mask_mlx import _index_matrix, _behavior_logprobs
    tokens, prompt, sets = trace['tokens'], trace['prompt'], trace['allowed_sets']
    # The last prompt position predicts the first action token. Prompt tokens
    # supply context but never contribute directly to the loss.
    ids = mx.array([list(prompt) + list(tokens[:-1])])
    logits = model(ids)[0, len(prompt)-1:, :].astype(mx.float32)
    idx, valid = _index_matrix(sets)
    selected = mx.where(valid, mx.take_along_axis(logits, idx, axis=-1), -math.inf)
    targets = mx.array(list(tokens))[:, None]
    columns = mx.argmax(valid & (idx == targets), axis=-1)
    logps = _behavior_logprobs(selected, 1.)
    values = mx.take_along_axis(logps, columns[:, None], axis=-1)[:, 0]
    keep = mx.array(trace.get('action_mask', [True]*len(tokens)))
    return mx.sum(mx.where(keep, values, 0.))


def mlx_pair_loss(model, pair: Mapping, reference: tuple[float, float], *,
                  beta: float = .1, sft_weight: float = .1):
    """Standard summed-sequence DPO plus token-mean chosen negative log likelihood."""
    validate_pair(pair)
    if beta <= 0 or sft_weight < 0:
        raise ValueError('beta must be positive; SFT weight nonnegative')
    if len(reference) != 2 or not all(math.isfinite(x) for x in reference):
        raise ValueError('frozen reference scores must be finite')
    import mlx.core as mx
    chosen = masked_action_logprob(model, pair['chosen'])
    rejected = masked_action_logprob(model, pair['rejected'])
    margin = beta * ((chosen-rejected) - (reference[0]-reference[1]))
    # logaddexp is stable even for badly misranked long sequences.
    return mx.logaddexp(mx.array(0.), -margin) - sft_weight * chosen / sum(pair['chosen'].get('action_mask', [True]*len(pair['chosen']['tokens'])))


def train_steps(model, pairs: Sequence[Mapping], cache: ReferenceCache, *, steps: int,
                learning_rate: float = 1e-4, beta: float = .1, sft_weight: float = .1,
                seed: int = 0, max_sequence_length: int = 2048, on_step=None) -> dict:
    """Update only the resident model's trainable leaves; caller publishes adapter.

    Caller owns the GPU lock, parent installation, data admission, checkpoint and
    lineage. This function never loads base weights or mutates a reference model.
    Reference prepass MUST run first while the parent is installed. A fresh Adam
    optimizer and deterministic seeded pair order are used for this short phase.
    """
    if not pairs or steps < 1 or learning_rate <= 0 or max_sequence_length < 1:
        raise ValueError('pairs, steps, learning rate and sequence cap must be positive')
    refs = []
    for pair in pairs:
        validate_pair(pair)
        for side in ('chosen', 'rejected'):
            trace = pair[side]
            if len(trace['prompt']) + len(trace['tokens']) > max_sequence_length:
                raise ValueError('DPO trace exceeds configured maximum sequence length')
        refs.append(cache.get(pair))
    import random
    order = list(range(len(pairs)))
    random.Random(seed).shuffle(order)
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    optimizer = optim.Adam(learning_rate=learning_rate)
    mx.random.seed(seed)
    model.train()
    started = perf_counter()
    losses = []
    try:
        # No compilation across Python pair/mask structures: each trace can have
        # different token lengths and allowed-set widths.
        for step in range(steps):
            index = order[step % len(order)]
            pair, ref = pairs[index], refs[index]
            loss_fn = nn.value_and_grad(model, lambda m: mlx_pair_loss(
                m, pair, ref, beta=beta, sft_weight=sft_weight))
            loss, gradients = loss_fn(model)
            mx.eval(loss, gradients)
            value = float(loss.item())
            if not math.isfinite(value):
                raise ValueError('nonfinite DPO loss; refusing optimizer update')
            optimizer.update(model, gradients)
            mx.eval(model.parameters(), optimizer.state)
            losses.append(value)
            if on_step is not None:
                on_step(step+1, value)
    finally:
        model.eval()
    return dict(method='sft_dpo', steps=steps, pairs=len(pairs), beta=beta,
                sft_weight=sft_weight, learning_rate=learning_rate,
                train_s=perf_counter()-started, loss_first=losses[0], loss_last=losses[-1])


def _file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, allow_nan=False))
    os.replace(temporary, path)


def read_complete_checkpoint(directory):
    """Newest hash-verified DPO commit; incomplete newer writes are ignored."""
    directory = Path(directory)
    for path in sorted(directory.glob('*_state.json'), reverse=True):
        try:
            state = json.loads(path.read_text())
            step = int(state['iteration'])
            if state['schema'] != SCHEMA or path.name != f'{step:07d}_state.json':
                continue
            if any(_file_sha(directory / f'{step:07d}_{kind}.safetensors') != state[f'{kind}_sha256']
                   for kind in ('adapters', 'optimizer')):
                continue
            return state
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _same_trace(actual, expected):
    return (actual.get('tokens') == expected.get('token_ids') and all(
        actual.get(field) == expected.get(field) for field in
        ('prompt', 'token_ids', 'allowed_sets', 'action_mask', 'status', 'dead_end_loss_start')))


def _admit_rejected(instance, pair, chosen_plan):
    """Rebuild cached rejected support and objective proof under current layers."""
    from llm.exit.preferences import prepare_masked_sequence, definitive_checker_failure
    from llm.exit.selection import Candidate, score_candidate
    from stowage.model import CheckReport, MovePlan
    from stowage.checker import CHECKER_VERSION, report_matches_inputs
    from stowage.frozen import require_clean
    actual = pair['rejected']
    witness = pair.get('rejected_witness')
    if not witness:
        raise ValueError('cached preference requires rejected objective witness')
    replay = prepare_masked_sequence(instance, tokens=actual['token_ids'])
    if not _same_trace(actual, replay):
        raise ValueError('cached rejected trace differs from current layered replay')
    if witness.get('token_ids', actual['tokens']) != actual['tokens']:
        raise ValueError('cached rejected witness token mismatch')
    evidence = pair.get('evidence', {})
    kind = evidence.get('kind')
    if kind == 'dead_end':
        if replay['status'] != 'dead_end':
            raise ValueError('cached dead-end rejected prefix no longer dead-ends')
        return
    if kind not in ('strictly_more_moves', 'checker_failure'):
        # A historical replay failure has no proof left if current replay succeeds.
        raise ValueError('cached rejection lacks a supported current objective proof')
    if 'plan' not in witness:
        raise ValueError('cached move/checker rejection requires complete plan witness')
    rejected_plan = MovePlan.model_validate(witness['plan'])
    require_clean(instance, rejected_plan, what='cached DPO rejection')
    plan_trace = prepare_masked_sequence(instance, plan=rejected_plan)
    if not _same_trace(actual, plan_trace):
        raise ValueError('cached rejected plan differs from scored action trace')
    if kind == 'strictly_more_moves':
        chosen_cost = score_candidate(instance, Candidate(chosen_plan, 'preference'))
        rejected_cost = score_candidate(instance, Candidate(rejected_plan, 'preference'))
        if rejected_cost <= chosen_cost:
            raise ValueError('cached rejected plan is not strictly more moves')
    else:
        report = CheckReport.model_validate(evidence.get('verification', {}).get('report'))
        require_clean(report, what='cached DPO failed checker report')
        if (report.passed is not False or not report_matches_inputs(report, instance, rejected_plan)
                or report.provenance.versions.get('checker') != CHECKER_VERSION):
            raise ValueError('cached failed-checker rejection lacks current content binding')
        if not definitive_checker_failure(report):
            raise ValueError('cached failed-checker rejection lacks a definitive hard plan violation')


def _admit_pair_instances(pairs, *, corpus_root=None):
    """Recheck disk AND optional inline identities before cached pairs reach GPU."""
    from llm.exit.identity import identity_guard
    from stowage.io import load_instance
    guard = identity_guard() if corpus_root is None else identity_guard(corpus_root)
    identities = []
    chosen_cache = {}
    from llm.exit.preferences import layer_identity, prepare_masked_sequence
    from stowage.model import CheckReport, MovePlan
    from stowage.checker import CHECKER_VERSION, report_matches_inputs
    from stowage.frozen import require_clean
    current_layers = layer_identity()
    for pair in pairs:
        validate_pair(pair)
        if not pair.get('instance_path'):
            raise ValueError('preference pair requires source instance_path')
        path = Path(pair['instance_path'])
        instance = load_instance(path)
        digest = guard.require_allowed(instance, role='train', raw_sha=_file_sha(path), input_path=path)
        if 'instance' in pair:
            inline = guard.require_allowed(pair['instance'], role='train')
            if inline != digest:
                raise ValueError('inline preference instance differs from source instance')
        if pair.get('instance_sha256') != digest:
            raise ValueError('cached preference instance identity mismatch')
        if pair.get('layers') != current_layers:
            raise ValueError('cached preference production layer identity changed')
        witness = pair.get('chosen_witness')
        if not witness:
            raise ValueError('cached preference requires checked chosen witness')
        chosen_plan = MovePlan.model_validate(witness['plan'])
        report = CheckReport.model_validate(witness['report'])
        require_clean(instance, chosen_plan, report, what='cached DPO chosen')
        if (report.passed is not True or not report_matches_inputs(report, instance, chosen_plan)
                or report.provenance.versions.get('checker') != CHECKER_VERSION):
            raise ValueError('cached preference chosen checker binding/version mismatch')
        key = _digest({'instance':digest, 'plan':witness['plan']})
        if key not in chosen_cache:
            chosen_cache[key] = prepare_masked_sequence(instance, plan=chosen_plan, require_complete=True)
        expected = chosen_cache[key]
        actual = pair['chosen']
        if not _same_trace(actual, expected):
            raise ValueError('cached preference chosen trace differs from checked plan')
        _admit_rejected(instance, pair, chosen_plan)
        identities.append(digest)
    return identities


def train_preference(parent, child, pairs, output_dir, *, steps=16, lr=1e-4,
                     beta=.1, sft_weight=.1, seed=0, max_sequence_length=2048,
                     corpus_root=None,reference_cache_path=None,on_phase=None):
    """Post-SFT DPO phase on a resident base; exact per-step crash resume.

    ``child`` is the already-SFT-trained child and its own frozen DPO reference.
    ``parent`` remains the generation parent for provenance and base validation.
    Checkpoints live below output_dir/dpo and never overwrite SFT numbered files.
    The runner owns global GPU admission; the runtime lock serializes adapter swaps.
    """
    if not pairs or steps < 1 or lr <= 0 or beta <= 0 or sft_weight < 0:
        raise ValueError('nonempty pairs and positive steps/lr/beta required')
    parent, child, out = Path(parent).resolve(), Path(child).resolve(), Path(output_dir).resolve()
    if parent == out or parent == child:
        raise ValueError('preference child/output must differ from frozen parent')
    instances = _admit_pair_instances(pairs, corpus_root=corpus_root)
    for pair in pairs:
        for side in ('chosen', 'rejected'):
            if len(pair[side]['prompt']) + len(pair[side]['tokens']) > max_sequence_length:
                raise ValueError('DPO trace exceeds configured maximum sequence length')
    from llm.exit import runtime
    from llm.stowlm import train as trainer
    from llm.stowlm.planner import StowLMPlanner
    from stowage.frozen import require_clean
    parent_cfg = json.loads((parent/'adapter_config.json').read_text())
    child_cfg = json.loads((child/'adapter_config.json').read_text())
    if runtime._architecture(parent) != runtime._architecture(child):
        raise ValueError('DPO parent/child LoRA architectures differ')
    model_path = child_cfg.get('model', child_cfg.get('stowlm_train_config', {}).get('model'))
    if not model_path or model_path != parent_cfg.get('model', parent_cfg.get('stowlm_train_config', {}).get('model')):
        raise ValueError('DPO parent and child must name the same base model')
    parent_planner = StowLMPlanner(model_path=model_path, adapter_path=str(parent))
    child_planner = StowLMPlanner(model_path=model_path, adapter_path=str(child))
    parent_provenance = runtime._clean_parent(parent_planner)
    child_provenance = runtime._clean_parent(child_planner)
    sidecar = json.loads((child/'adapters.safetensors.provenance.json').read_text())
    parent_id = adapter_identity(parent)
    # SFT resume can republish identical weights/config with additional resume
    # lineage. That must not invalidate a completed DPO phase's starting weights.
    child_id = adapter_identity(child,include_lineage=False)
    mask = _digest([pair.get('layers', 'production-lookahead-securing') for pair in pairs])
    identity = dict(schema=SCHEMA, parent=parent_id, reference_policy='frozen_sft_child',child_config=_digest(child_cfg),
                    pairs=[pair_identity(pair) for pair in pairs], instances=instances,
                    mask=mask, steps=steps, lr=lr, beta=beta, sft_weight=sft_weight,
                    seed=seed, max_sequence_length=max_sequence_length)
    checkpoint_dir = out/'dpo'
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    state = read_complete_checkpoint(checkpoint_dir)
    if state is not None:
        if state['identity'] != identity:
            raise ValueError('DPO resume configuration/pair/parent mismatch')
        if child_id != state['initial_child_identity'] and _file_sha(child/'adapters.safetensors') != state['adapters_sha256']:
            raise ValueError('DPO initial SFT child changed since checkpoint')
        initial_child_id = state['initial_child_identity']
        # Retain the original inherited lineage across repeated publication.
        from stowage.model import Provenance
        child_provenance = Provenance.model_validate(state['provenance'])
        training = state['training']
    else:
        initial_child_id = child_id
        training = sidecar['training']
    # Each SFT child supplies its own frozen policy. Population siblings must not
    # reuse one another's likelihoods, even when their pair pool is identical.
    cache_path = checkpoint_dir/'reference.json'
    if reference_cache_path is not None:
        base = Path(reference_cache_path)
        cache_path = base.with_name(base.stem+'-'+initial_child_id[:16]+base.suffix)
    cache = ReferenceCache(cache_path,parent_identity=initial_child_id,mask_identity=mask)
    started = perf_counter()
    with runtime._LOCK:
        if on_phase:on_phase('reference_prepass_started')
        reference_model = runtime._resident_for(child_planner)
        missing_references = any(pair_identity(pair) not in cache.scores for pair in pairs)
        if missing_references and child_id != initial_child_id:
            raise ValueError('original SFT child weights required to complete frozen-reference cache')
        reference_stats = cache.precompute(pairs, reference_scorer(reference_model))
        if on_phase:on_phase('reference_prepass_finished',**reference_stats)
        if adapter_identity(child,include_lineage=False) != child_id:
            raise ValueError('SFT child changed during frozen-reference prepass')
        model = runtime._resident_for(child_planner)
        import mlx.core as mx
        import mlx.nn as nn
        import mlx.optimizers as optim
        from mlx.utils import tree_flatten
        optimizer = optim.Adam(learning_rate=lr)
        start = 0 if state is None else state['iteration']
        if state is not None:
            model.load_weights(str(checkpoint_dir/f'{start:07d}_adapters.safetensors'), strict=False)
            trainer.load_optimizer_state(optimizer, model, checkpoint_dir/f'{start:07d}_optimizer.safetensors')
        import random
        order = list(range(len(pairs)))
        random.Random(seed).shuffle(order)
        refs = [cache.get(pair) for pair in pairs]
        provenance = child_provenance.merge(parent_provenance,
            parent_ids=[f'exit-dpo-reference-sha256:{initial_child_id}', f'exit-dpo-pairs-sha256:{_digest(identity["pairs"])}'])
        require_clean(provenance, what='DPO trained child')
        losses = []
        model.train()
        train_started = perf_counter()
        if on_phase:on_phase('update_started')
        try:
            for step in range(start, steps):
                mx.random.seed(trainer.step_seed(seed, step))
                index = order[step % len(order)]
                loss_fn = nn.value_and_grad(model, lambda m: mlx_pair_loss(
                    m, pairs[index], refs[index], beta=beta, sft_weight=sft_weight))
                loss, gradients = loss_fn(model)
                mx.eval(loss, gradients)
                value = float(loss.item())
                if not math.isfinite(value):
                    raise ValueError('nonfinite preference loss; refusing optimizer update')
                optimizer.update(model, gradients)
                mx.eval(model.parameters(), optimizer.state)
                losses.append(value)
                iteration = step+1
                files = {}
                for kind, save in (
                    ('adapters', lambda path: mx.save_safetensors(str(path), dict(tree_flatten(model.trainable_parameters())))),
                    ('optimizer', lambda path: trainer.save_optimizer_state(optimizer, path))):
                    path = checkpoint_dir/f'{iteration:07d}_{kind}.safetensors'
                    # MLX appends .safetensors unless it is the final suffix.
                    temporary = path.with_name('.tmp-'+path.name)
                    save(temporary)
                    os.replace(temporary, path)
                    files[kind+'_sha256'] = _file_sha(path)
                state = dict(schema=SCHEMA, identity=identity, iteration=iteration,
                    initial_child_identity=initial_child_id, provenance=provenance.model_dump(mode='json'),
                    training=training, loss=value, **files)
                _atomic_json(checkpoint_dir/f'{iteration:07d}_state.json', state)
            # Publish from committed DPO checkpoint even if SFT's resume re-published
            # its own last weights immediately before this already-complete call.
            final = checkpoint_dir/f'{steps:07d}_adapters.safetensors'
            out.mkdir(parents=True, exist_ok=True)
            if out != child:
                (out/'adapter_config.json').write_text(json.dumps(child_cfg))
            temporary = out/'.tmp-dpo-adapters.safetensors'
            temporary.write_bytes(final.read_bytes())
            os.replace(temporary, out/'adapters.safetensors')
            trainer.write_adapter_sidecar(out, provenance, {**training, 'exit_method':'sft_dpo',
                'exit_dpo':identity, 'exit_dpo_steps':steps, 'exit_initial_sft_child':initial_child_id,
                'exit_dpo_reference':'frozen_sft_child', 'exit_dpo_reference_identity':initial_child_id})
            result = dict(method='sft_dpo', steps=steps, resumed_from=start, pairs=len(pairs),
                beta=beta, sft_weight=sft_weight, train_s=perf_counter()-train_started,
                wall_s=perf_counter()-started, loss_last=state['loss'],
                reference='frozen_sft_child',reference_adapter=str(child),
                reference_identity=initial_child_id,reference_cache=str(cache_path),**reference_stats)
            _atomic_json(out/'dpo_run.json', result)
            if on_phase:on_phase('update_finished',train_s=result['train_s'])
            return result
        finally:
            runtime._RESIDENT['identity'] = None
            model.eval()
