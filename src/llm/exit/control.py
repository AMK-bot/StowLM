"""D128 gold-only control matched to the compared adapter's actual SFT ancestry.

This is a paired experiment: change the training targets to gold while keeping
the inherited SFT update schedule. It is weak attribution evidence, not proof
that population selection or a particular objective caused an improvement.
No MLX import or GPU operation occurs until the injected/default trainer runs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

SCHEDULE_FIELDS = ('lr_schedule', 'warmup_frac', 'min_lr_frac',
    'grad_accumulation_steps', 'batch_size', 'max_seq_length', 'attn_block',
    'grad_checkpoint')


def _read(path):
    return json.loads(Path(path).read_text())


def _write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _path(value):
    return str(Path(value).resolve())


def training_lineage(history, parent, baseline):
    """Follow inherited weights, never assume that two elapsed generations trained twice."""
    from llm.stowlm.train import TRAINER_VERSION
    nodes = {}
    for generation in history:
        for child in generation.get('children', ()):
            key = _path(child['adapter'])
            node = dict(child, generation=generation['generation'])
            if key in nodes and nodes[key] != node:
                raise ValueError('conflicting control lineage records')
            nodes[key] = node
    baseline, current = _path(baseline), _path(parent)
    phases, visited = [], set()
    while current != baseline:
        if current in visited or current not in nodes:
            raise ValueError('control requires complete acyclic inherited_from ancestry to the original baseline')
        visited.add(current); node = nodes[current]
        inherited = _path(node['inherited_from'])
        adapter = Path(current)
        # DPO publishes a preference subdirectory; its preceding SFT receipt is
        # authoritative for matching runtime.train, not the DPO step count.
        sft_adapter = adapter.parent if node.get('method') == 'sft_dpo' else adapter
        config_path = sft_adapter/'adapter_config.json'
        published = _read(config_path); config = published.get('stowlm_train_config', {})
        if published.get('stowlm_trainer') != TRAINER_VERSION:
            raise ValueError('control cannot claim matching schedules across trainer versions')
        if _path(config.get('extra', {}).get('exit_parent', '')) != inherited:
            raise ValueError('published SFT receipt disagrees with inherited_from')
        if any(key not in config for key in (*SCHEDULE_FIELDS, 'iters', 'learning_rate', 'seed')):
            raise ValueError('control SFT schedule receipt is incomplete')
        steps, accumulation = config['iters'], config['grad_accumulation_steps']
        if (type(steps) is not int or steps < 1 or type(accumulation) is not int
                or accumulation < 1 or steps % accumulation):
            raise ValueError('control needs completed integer optimizer-update schedule')
        train_file = Path(config['data_dir'])/'train.jsonl'
        training_rows = [json.loads(line) for line in train_file.read_text().splitlines() if line.strip()]
        train_hashes = {row.get('meta', {}).get('gold', {}).get('input_sha256') for row in training_rows}
        if not training_rows or any(not isinstance(digest, str) or len(digest) != 64 for digest in train_hashes):
            raise ValueError('control requires the actual SFT training-input inventory')
        phases.append(dict(loop_adapter=current, sft_adapter=str(sft_adapter),
            inherited_from=inherited, generation=node['generation'], method=node.get('method', 'sft'),
            steps=steps, lr=config['learning_rate'], seed=config['seed'],
            overrides={key:config[key] for key in SCHEDULE_FIELDS},
            optimizer_updates=steps//accumulation, trainer=TRAINER_VERSION,
            lr_schedule_units='optimizer_updates', optimizer='fresh Adam per inherited SFT phase',
            receipt_sha256=_sha(config_path),
            loop_weights_sha256=_sha(adapter/'adapters.safetensors'),
            loop_dataset_sha256=_sha(train_file), loop_train_rows=len(training_rows),
            loop_unique_train_examples=len(train_hashes),
            dpo_updates_unmatched=node.get('method') == 'sft_dpo'))
        current = inherited
    return list(reversed(phases))


def choose_control_schedule_adapter(history, parent, baseline, fitness=None):
    """Match real trained ancestry even when the unchanged elite wins selection.

    The compared adapter remains the selected elite. The training schedule is
    borrowed explicitly from its fittest actually trained competing child; this
    does not pretend the elite inherited any updates or select a child to ship.
    A zero-phase baseline self-audit is never a trained control.
    """
    if _path(parent) != _path(baseline):
        if not training_lineage(history, parent, baseline):
            raise ValueError('selected adapter has no actual SFT schedule')
        return _path(parent)
    candidates = {}
    for generation in history:
        for child in generation.get('children', ()):
            adapter = _path(child['adapter'])
            if adapter != _path(baseline):
                candidates[adapter] = child
    if fitness is not None:
        counts, means = fitness['pass_counts'], fitness['mean_moves']
        counts = {_path(adapter): value for adapter, value in counts.items()}
        means = {_path(adapter): value for adapter, value in means.items()}
        candidates = {adapter: child for adapter, child in candidates.items() if adapter in counts}
        rank = lambda adapter: (counts[adapter], -means[adapter] if means[adapter] is not None else 0)
    else:
        # Standalone callers must name the compared population through fitness;
        # old history alone cannot prove which children were actually compared.
        raise ValueError('elite control requires paired fitness to select a trained competing schedule')
    if not candidates:
        raise ValueError('gold control requires an actually trained competing child')
    selected = max(candidates, key=rank)
    if not training_lineage(history, selected, baseline):
        raise ValueError('gold control reference has no completed SFT phase')
    return selected


def _gold_records(gold_root, excluded, seed):
    """Inventory current train gold only, with shared seed/hash/identity exclusions."""
    from llm.exit.replay import choose_replay
    gold_root = Path(gold_root).resolve()
    records = []
    for rung in ('tiny', 'small'):
        for path in sorted((gold_root/'train'/rung).rglob('result.json')):
            if (path.parent/'case.lock').is_file() and _read(path).get('passed') is True:
                records.append(path)
    # choose_replay excludes current map identity, seed, raw/canonical hash and
    # held-out paths, and deduplicates maps. Limit=pool includes all eligible gold.
    return choose_replay(records, excluded, seed, len(records))


def _admit_gold_record(job):
    """Spawn-safe current-epoch gold admission; one immutable row per worker job."""
    import importlib.util
    from llm.exit.data import ROOT
    from llm.exit.identity import identity_guard
    from llm.exit.replay import ensure_current_replay, checker_epoch, outcome_dependency_id
    record, directory, index = Path(job['record']), Path(job['directory']), job['index']
    spec = importlib.util.spec_from_file_location('_control_mvp_builder',
        ROOT/'experiments/mvp/sft/dataset/build.py')
    loader = importlib.util.module_from_spec(spec); spec.loader.exec_module(loader)
    guard = identity_guard(ROOT/'experiments/mvp/corpus')
    try:
        instance, plan, report, digest, source = loader.load_triple(record)
        guard.require_allowed(instance, role='train', raw_sha=digest, input_path=source)
        plan, report, rechecked = ensure_current_replay(record, instance, plan, report,
            directory.parent.parent/'gold-current-checker-cache')
        target = directory/'admitted-gold'/f'{index:05d}.record.json'
        _write(target, dict(instance_path=str(source) if source else None,
            instance=instance.model_dump(mode='json'), plan=plan.model_dump(mode='json'),
            report=report, exit_checker_epoch=checker_epoch(),
            exit_outcome_dependency_id=outcome_dependency_id(), gold_source=str(record)))
        return dict(admitted=str(target))
    except (ValueError, RuntimeError, OSError) as error:
        return dict(rejected=dict(record=str(record), reason=f'{type(error).__name__}: {error}'))


def _build_gold_dataset(records, directory, *, validation_records=(), seed=0, workers=1):
    """Current-check gold and replay every action with a bounded ordered CPU pool."""
    from llm.exit.data import build_training_dataset
    from llm.exit.dataset_pool import ordered_pool
    directory = Path(directory)
    jobs = [dict(record=str(record), directory=str(directory), index=index)
            for index, record in enumerate(records)]
    with ordered_pool(workers) as ordered:
        outcomes = ordered(_admit_gold_record, jobs)
    admitted = [Path(row['admitted']) for row in outcomes if 'admitted' in row]
    rejected = [row['rejected'] for row in outcomes if 'rejected' in row]
    if not admitted:
        raise ValueError('no current-epoch checked train gold available for control')
    result = build_training_dataset(admitted, [], directory, validation_records=validation_records,
                                    seed=seed, replay_ratio=0., workers=workers)
    result.update(control_gold_only=True, gold_pool=len(records), gold_admitted=len(admitted),
                  gold_rejected=rejected, admission_workers=workers, native_threads=1)
    _write(directory/'control-gold-admission.json', result)
    return result


def _dataset_hashes(directory):
    """Content of the finished dataset folder (admitted records, splits, stats), keyed by relative path."""
    directory = Path(directory)
    return {str(path.relative_to(directory)): _sha(path) for path in sorted(directory.rglob('*')) if path.is_file()}


def ensure_gold_control(history, parent, args, directory, *, trainer=None, builder=None,
                        evaluator=None, excluded=(), validation_records=(), audit_paths=(),
                        schedule_adapter=None, fitness=None):
    """Return an adapter/audit receipt for the loop adapter ``parent``.

    ``args.parent`` is the original baseline; ``history`` contains committed generation
    summaries through the comparison point. ``evaluator(adapter, paths, out,
    args)`` must be the SAME fixed-set recipe used for the compared loop adapter.
    Runner passes its callback explicitly, keeping this module independent.
    Build/train callbacks retain their production signatures for CPU fixtures.
    """
    from llm.exit.outcome_epoch import training_dependency_epoch
    from llm.exit.replay import checker_epoch
    # Captured before any admission: every admitted record and dataset file below belongs to this epoch (rev-v4-j P1).
    training_epoch = training_dependency_epoch()
    baseline = Path(args.parent).resolve(); directory = Path(directory).resolve()
    reference = (_path(schedule_adapter) if schedule_adapter is not None else
                 choose_control_schedule_adapter(history, parent, baseline, fitness))
    if fitness is not None and _path(parent) == str(baseline):
        expected_reference = choose_control_schedule_adapter(history, parent, baseline, fitness)
        if reference != expected_reference:
            raise ValueError('control schedule must match the fittest trained competing child')
    phases = training_lineage(history, reference, baseline)
    if not phases:
        raise ValueError('gold control must train at least one actual completed SFT phase')
    excluded = list(excluded) or list(getattr(args, 'control_excluded_paths', ()))
    if phases and not excluded:
        raise ValueError('gold control requires explicit current-generation map exclusions')
    cap = getattr(args, 'control_max_gold', 64)
    if type(cap) is not int or cap < 1 or cap > 64:
        raise ValueError('control_max_gold must be an integer from 1 through 64')
    records = _gold_records(args.gold, excluded, phases[-1]['seed'] if phases else 0)
    if phases and not records:
        raise ValueError('gold-only control has no eligible current train gold')
    from llm.exit.replay import _metadata, choose_replay
    inventory = [dict(path=str(path), sha256=_sha(path), input_sha256=_metadata(path)['digest'])
                 for path in records]
    identity = dict(schema='exit-d137-trained-gold-control-v2', baseline=str(baseline),
        baseline_weights_sha256=_sha(baseline/'adapters.safetensors'),
        compared_adapter=_path(parent), schedule_reference_adapter=reference,
        compared_adapter_schedule_matched=reference == _path(parent),
        phases=phases, gold_inventory=inventory,
        excluded_paths=[dict(path=_path(p), sha256=_sha(p)) for p in sorted(map(Path, excluded))],
        checker_epoch=checker_epoch(), max_gold=cap, training_epoch_id=_digest(training_epoch),
        validation_records=[dict(path=str(p), sha256=_sha(p)) for p in validation_records])
    manifest = directory/'identity.json'
    if manifest.exists() and _read(manifest) != identity:
        raise RuntimeError('control ancestry, schedule, gold inventory or checker epoch changed')
    _write(manifest, identity)
    if trainer is None:
        from llm.exit.runtime import train as trainer
    builder = builder or _build_gold_dataset
    current, receipts = str(baseline), []
    for index, phase in enumerate(phases):
        folder = directory/f'phase-{index:02d}'
        adapter, dataset, receipt_path = folder/'adapter', folder/'data', folder/'receipt.json'
        # APPROXIMATION: cap gold-control targets at 64 unique maps to bound CPU
        # replay/check overhead. Match the loop's unique example count below that
        # cap; update count, peak LR, schedule and optimizer resets still match.
        desired = min(phase['loop_unique_train_examples'], cap)
        selected = choose_replay(records, excluded, phase['seed'], desired)
        binding = dict(parent=current, phase=phase, identity_sha256=_digest(identity),
            selected_gold=[str(p) for p in selected], gold_target_examples=desired,
            training_epoch_id=_digest(training_epoch))
        if receipt_path.exists():
            receipt = _read(receipt_path)
            if (receipt.get('binding') != binding or not (adapter/'adapters.safetensors').is_file()
                    or receipt.get('adapter_sha256') != _sha(adapter/'adapters.safetensors')
                    or receipt.get('adapter_config_sha256') != _sha(adapter/'adapter_config.json')):
                raise RuntimeError('committed control phase changed')
        else:
            started = time.monotonic()
            from llm.exit.dataset_pool import choose_workers
            dataset_capacity = choose_workers(args)
            _write(folder/'dataset-capacity.json', dataset_capacity)
            build = builder(selected, dataset, validation_records=validation_records, seed=phase['seed'],
                            workers=dataset_capacity['workers'])
            built = time.monotonic()
            dataset_files = _dataset_hashes(dataset)
            _write(folder/'dataset-binding.json', dict(training_epoch_id=_digest(training_epoch),
                checker_epoch=identity['checker_epoch'], files=dataset_files, build=build))
            # Same gate as the main child: an update only under the epoch that admitted its rows.
            import llm.exit.replay as replay_module  # read through the module: the live value, not a stale binding
            if (training_dependency_epoch() != training_epoch or replay_module.checker_epoch() != identity['checker_epoch']
                    or _dataset_hashes(dataset) != dataset_files):
                raise RuntimeError('control training epoch or dataset changed after admission; '
                                   'no update applied, rebuild in a fresh process')
            trainer(current, dataset, adapter, steps=phase['steps'], lr=phase['lr'],
                    seed=phase['seed'], **phase['overrides'])
            if not (adapter/'adapters.safetensors').is_file():
                raise RuntimeError('control trainer did not publish adapter weights')
            actual = _read(adapter/'adapter_config.json').get('stowlm_train_config', {})
            expected = dict(phase['overrides'], iters=phase['steps'],
                            learning_rate=phase['lr'], seed=phase['seed'])
            if any(actual.get(key) != value for key, value in expected.items()):
                raise RuntimeError('control trainer published a different update schedule')
            receipt = dict(binding=binding, adapter=str(adapter),
                adapter_sha256=_sha(adapter/'adapters.safetensors'), dataset=build, dataset_files=dataset_files,
                adapter_config_sha256=_sha(adapter/'adapter_config.json'),
                dataset_s=built-started, train_s=time.monotonic()-built,
                dataset_capacity=dataset_capacity,
                selected_gold_examples=len(selected),
                example_count_matched=(build.get('gold_admitted', len(selected)) == phase['loop_unique_train_examples']),
                optimizer_reset=True, fresh_optimizer_disclosure=phase['optimizer'])
            _write(receipt_path, receipt)
        receipts.append(receipt); current = str(adapter)
    report = dict(schema=identity['schema'], adapter=current, compared_adapter=_path(parent),
        schedule_reference_adapter=reference,
        compared_adapter_schedule_matched=reference == _path(parent),
        trained_control=True,
        phase_count=len(phases), total_sft_microsteps=sum(p['steps'] for p in phases),
        optimizer='fresh Adam per phase, matching loop runtime.train',
        schedule_matched=True, schedule_match_scope='schedule_reference_adapter',
        gold_only=True, phases=receipts,
        gold_pool_records=len(records),
        example_count_matched=all(r['example_count_matched'] for r in receipts),
        approximation='at most 64 seeded rung/ship-stratified current-gold targets per inherited phase',
        dpo_updates_unmatched=any(p['dpo_updates_unmatched'] for p in phases),
        attribution=('weak paired evidence; a method claim requires beating this trained control; '
                     'when the elite survives, schedule belongs to an explicitly named competing child'),
        recipe='caller supplies identical fixed-set evaluation for both adapters')
    if evaluator is not None:
        paths = list(audit_paths) or getattr(args, 'control_validation_paths', None)
        if not paths:
            raise ValueError('control audit requires the same explicit fixed validation paths')
        report['audit_rows'] = evaluator(current, paths, directory/'audit', args)
    _write(directory/'summary.json', report)
    return report
