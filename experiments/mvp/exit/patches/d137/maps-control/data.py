"""Fresh corpus scheduling and checker-gated Expert Iteration SFT datasets."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[3]


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


class CorpusSchedule:
    """Single-runner schedule with atomic audit-sidecar streaming admission.

    The manifest supplies release settings; accepted train/val audits extend its
    stale inventory. Input bytes are fully validated lazily, before reservation.
    Original corpus source epochs remain pinned, rather than relabelled current.
    """
    def __init__(self, corpus_root: str | Path, state_path: str | Path):
        self.root, self.path = Path(corpus_root).resolve(), Path(state_path)
        manifest = json.loads((self.root / 'manifest.json').read_text())
        entries = {e['path']: dict(e) for e in manifest['files']}
        settings = manifest.get('release', {}).get('ship_settings', {})
        self.audit_rejections = []
        for split in ('train', 'val'):
            for rung in ('tiny', 'small'):
                for audit_path in sorted((self.root / split / rung).rglob('*.audit.json')):
                    path = Path(str(audit_path).replace('.audit.json', '.json')).resolve()
                    rel = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
                    original = entries.pop(rel, None)
                    try:
                        if not path.is_relative_to(self.root / split / rung):
                            raise ValueError('audit instance path escapes split/rung')
                        audit = json.loads(audit_path.read_text())
                        config = audit['config']
                        if not (audit.get('accepted') is True and audit.get('rule_freeze_v1_enabled') is False
                                and type(audit.get('global_screen')) is int and audit['global_screen'] == 0):
                            raise ValueError('audit must be accepted with freeze and C2 off')
                        if original and original.get('active_for_current_release') is False:
                            raise ValueError('manifest marks input inactive')
                        if config.get('split') != split or config.get('rung') != rung:
                            raise ValueError('audit config split/rung mismatch')
                        ship, family, seed = config['vessel'], config['generator'], config['seed']
                        if family not in ('random_yard', 'simulated_yard') or type(seed) is not int:
                            raise ValueError('invalid generator/seed config')
                        expected = Path(split) / rung / ship / family / f'seed-{seed:09d}.json'
                        if Path(rel) != expected:
                            raise ValueError('audit config does not match instance path')
                        if rung == 'small':
                            adopted = settings.get(ship)
                            if adopted is None or any(config.get(k) != v for k, v in adopted.items()):
                                raise ValueError('audit does not match current per-ship booking settings')
                        digest = audit['instance_sha256']
                        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                            raise ValueError('invalid instance SHA256')
                        entries[rel] = {**(original or {}), 'path': rel, 'split': split, 'rung': rung,
                                        'ship': ship, 'family': family, 'seed': seed, 'sha256': digest,
                                        'active_for_current_release': True,
                                        'audit_path': str(audit_path.relative_to(self.root)),
                                        'audit_sha256': hashlib.sha256(audit_path.read_bytes()).hexdigest(),
                                        'generation_settings': {k: config.get(k) for k in
                                                                ('booking_slack', 'booking_mass_fraction')}}
                    except (ValueError, KeyError, OSError, TypeError) as error:
                        self.audit_rejections.append({'path': rel, 'reason': str(error)})
        self.entries = list(entries.values())
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'version': 2, 'used': [], 'used_seeds': [], 'used_ids': [], 'generations': {}, 'validation': None}

    def _eligible(self, split: str, rung: str) -> list[dict]:
        result = []
        for entry in self.entries:
            if (entry.get('split') != split or entry.get('rung') != rung
                    or not entry.get('active_for_current_release', True)):
                continue
            path = (self.root / entry['path']).resolve()
            if not path.is_relative_to(self.root / split):
                raise ValueError(f'corpus manifest path escapes {split}: {path}')
            if path.is_file():
                result.append(entry)
        return result

    def _validate_entry(self, entry):
        path = (self.root / entry['path']).resolve()
        if not path.is_relative_to(self.root / entry['split']):
            raise ValueError('instance path escapes corpus split')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError(f'corpus hash mismatch: {entry["path"]}')
        from llm.exit.identity import identity_guard
        identity_guard(self.root).require_allowed(json.loads(path.read_text()),
            role='train' if entry['split']=='train' else 'audit', raw_sha=entry['sha256'], input_path=path)
        if not entry.get('audit_path'):
            return  # Legacy manifest authority; current producer always publishes sidecars.
        audit_path = self.root / entry['audit_path']
        if hashlib.sha256(audit_path.read_bytes()).hexdigest() != entry['audit_sha256']:
            raise ValueError('corpus audit changed after inventory pin')
        audit = json.loads(audit_path.read_text())
        from stowage.frozen import require_clean
        from stowage.generate.background_acceptance import requires_background_gate
        from stowage.io import load_instance
        instance = load_instance(path)
        require_clean(instance, what='Expert Iteration corpus admission')
        config = audit['config']
        if (instance.seed != config['seed'] or instance.size_class != config['rung']
                or instance.family.get('vessel') != config['vessel']
                or instance.vessel.id not in (config['vessel'], f"{config['vessel']}.{config['rung']}")):
            raise ValueError('instance seed/rung/vessel does not match audit config')
        creator = {'random_yard': 'generate.random_yard', 'simulated_yard': 'generate.instances'}
        if instance.provenance.created_by != creator[config['generator']]:
            raise ValueError('instance generator does not match audit config')
        if len(instance.load_list) != audit['requested_targets'] or instance.voyage is None:
            raise ValueError('instance target count/voyage does not match producer contract')
        if requires_background_gate(instance.vessel, size=instance.size_class):
            background = audit.get('background_last', {})
            if not (audit.get('background_checks', 0) > 0 and background.get('accepted') is True
                    and background.get('physics_sha256')):
                raise ValueError('missing accepted necessary background-gate evidence')
        for key in ('booking_slack', 'booking_mass_fraction'):
            if config.get(key) is not None and float(instance.family.get(key, 'nan')) != config[key]:
                raise ValueError('instance booking settings differ from audit')
        entry['instance_id'] = instance.id

    def _round_robin(self, entries: list[dict], offset: int) -> list[dict]:
        ships: dict[str, deque] = defaultdict(deque)
        for entry in sorted(entries, key=lambda x: x['path']):
            ships[entry.get('ship', '?')].append(entry)
        names = sorted(ships)
        if names:
            offset %= len(names)
            names = names[offset:] + names[:offset]
        result = []
        while any(ships.values()):
            for name in names:
                if ships[name]:
                    result.append(ships[name].popleft())
        return result

    def fixed_validation(self, tiny: int = 10, small: int = 20) -> list[Path]:
        chosen = self.state['validation']
        if chosen is None:
            chosen, identities = [], set()
            for rung, count in (('tiny', tiny), ('small', small)):
                if count < 0:
                    raise ValueError('map counts must be nonnegative')
                options = self._round_robin(self._eligible('val', rung), 0)
                selected = []
                for entry in options:
                    if len(selected) == count:
                        break
                    tokens = self._identities(entry)
                    if tokens & identities:
                        continue
                    self._validate_entry(entry)
                    tokens = self._identities(entry)
                    if tokens & identities:
                        continue
                    selected.append(entry)
                    identities |= tokens
                if len(selected) < count:
                    raise ValueError(f'not enough {rung} val maps: {len(selected)} < {count}')
                chosen.extend(selected)
        for entry in chosen:
            if not (self.root / entry['path']).resolve().is_relative_to(self.root / 'val'):
                raise ValueError('fixed validation path must remain in corpus/val')
            try:
                self._validate_entry(entry)
            except ValueError as error:
                raise ValueError(f'fixed validation hash mismatch or failed admission: {error}') from error
        self.state['validation'] = chosen
        _atomic_json(self.path, self.state)
        return [self.root / e['path'] for e in chosen]

    @staticmethod
    def _identities(entry):
        return {f'{key}:{entry[key]}' for key in ('sha256', 'seed', 'instance_id') if entry.get(key) is not None}

    def _checked_gold_evidence(self, entry, gold_root):
        """Select teacher maps with completed checker evidence, never plan-only files.

        These are historically checked artifacts. Selection MUST run their plans
        through the current epoch's full checker and mask layers again; a stale
        positive report is not current validity and cannot be an SFT target.
        """
        if gold_root is None:
            return None
        folder = Path(gold_root) / Path(entry['path']).with_suffix('')
        paths = {name: folder / name for name in ('plan.json', 'result.json', 'checker.json', 'case.lock')}
        if not all(path.is_file() for path in paths.values()):
            return None
        try:
            result = json.loads(paths['result.json'].read_text())
            report = json.loads(paths['checker.json'].read_text())
            source = Path(result.get('input_path', '')).resolve()
            expected = (self.root / entry['path']).resolve()
            from llm.exit.outcomes import checker_unavailable
            required = {'moves', 'rules', 'voyage', 'stability', 'strength',
                        'visibility', 'lashing', 'constraints'}
            parts = report.get('parts', {})
            completed = (isinstance(parts, dict) and required.issubset(parts) and not checker_unavailable(report)
                and all(part.get('ran') is True and not part.get('violations')
                        for part in parts.values()) and not report.get('violations'))
            if (result.get('passed') is not True or result.get('completed') is not True
                    or source != expected or result.get('input_sha256') != entry['sha256']
                    or result.get('plan_sha256') != hashlib.sha256(paths['plan.json'].read_bytes()).hexdigest()
                    or report.get('passed') is not True or not completed):
                return None
            return dict(result_path=str(paths['result.json']),
                result_sha256=hashlib.sha256(paths['result.json'].read_bytes()).hexdigest(),
                plan_sha256=hashlib.sha256(paths['plan.json'].read_bytes()).hexdigest(),
                checker_sha256=hashlib.sha256(paths['checker.json'].read_bytes()).hexdigest(),
                historically_full_checked=True, current_epoch_verification_required=True)
        except (ValueError, OSError, TypeError, AttributeError):
            return None

    def next_train(self, tiny: int, small: int, generation: int, *, gold_root=None,
                   seen_adapter_hashes=None, gold_fraction=.5) -> list[Path]:
        """Reserve fresh disjoint maps, balancing checked-gold teachers and no-gold maps.

        Half is rounded across the entire generation, then apportioned across
        rungs. If either stratum lacks fresh maps, refuse atomically rather than
        silently reverting to self-imitation. No-gold maps are called unseen only
        when the complete inherited adapter-training inventory proves it.
        """
        if type(tiny) is not int or type(small) is not int or min(tiny, small) < 0:
            raise ValueError('map counts must be nonnegative integers')
        if tiny + small == 0:
            raise ValueError('at least one fresh train map is required')
        if not isinstance(gold_fraction, (int, float)) or isinstance(gold_fraction, bool) or not 0 <= gold_fraction <= 1:
            raise ValueError('gold_fraction must be between zero and one')
        key = str(generation)
        if key in self.state['generations']:
            raise ValueError(f'generation {generation} already reserved; use its saved instances to resume')
        used = {f'sha256:{s}' for s in self.state['used']}
        used |= {f'seed:{s}' for s in self.state.get('used_seeds', [])}
        used |= {f'instance_id:{s}' for s in self.state.get('used_ids', [])}
        for entries in self.state['generations'].values():
            for entry in entries:
                used |= self._identities(entry)
        forbidden = set()
        for entry in self.entries:
            if entry.get('split') in ('val', 'test', 'showcase'):
                forbidden |= self._identities(entry)
        # Preserve old no-gold-root utility callers. Production always supplies it.
        teacher_total = int((tiny + small) * gold_fraction + .5) if gold_root is not None else 0
        quotas = {'tiny': int(tiny * gold_fraction) if gold_root is not None else 0,
                  'small': int(small * gold_fraction) if gold_root is not None else 0}
        for rung, count in sorted((('tiny', tiny), ('small', small)),
                key=lambda item: (-(item[1] * gold_fraction % 1), item[0])):
            if sum(quotas.values()) < teacher_total and quotas[rung] < count:
                quotas[rung] += 1
        selected = []
        known = None if seen_adapter_hashes is None else set(seen_adapter_hashes)
        for rung, count in (('tiny', tiny), ('small', small)):
            options = self._round_robin(self._eligible('train', rung), generation)
            evidence = {entry['path']: self._checked_gold_evidence(entry, gold_root) for entry in options}
            teacher = [entry for entry in options if evidence[entry['path']] is not None]
            # A plan-only/incomplete gold artifact is neither a checked teacher
            # nor an unseen no-gold map. Its recovery belongs to the gold pipeline.
            no_gold = [entry for entry in options if gold_root is None or not
                (Path(gold_root) / Path(entry['path']).with_suffix('') / 'plan.json').is_file()]
            if known is not None:
                no_gold = [entry for entry in no_gold if entry['sha256'] not in known]
            for group, target, label in ((teacher, quotas[rung], 'checked_gold_teacher'),
                                        (no_gold, count - quotas[rung], 'no_stored_gold')):
                unique = []
                for entry in group:
                    if len(unique) == target:
                        break
                    if self._identities(entry) & (used | forbidden):
                        continue
                    self._validate_entry(entry)
                    if self._identities(entry) & (used | forbidden):
                        continue
                    entry = dict(entry)
                    has_gold = label == 'checked_gold_teacher'
                    entry['novelty'] = ('unseen_by_any_adapter' if not has_gold and known is not None
                        and entry['sha256'] not in known else 'new_to_loop')
                    entry['adapter_training_inventory_known'] = known is not None
                    entry['gold_available_at_selection'] = has_gold
                    entry['map_stratum'] = label
                    entry['teacher_gold_evidence'] = evidence[entry['path']] if has_gold else None
                    unique.append(entry)
                    used |= self._identities(entry)
                if len(unique) < target:
                    raise ValueError(f'fresh {rung} {label} train maps exhausted: {len(unique)} < {target}')
                selected.extend(unique)
        self.state['used'] = sorted(x.split(':', 1)[1] for x in used if x.startswith('sha256:'))
        self.state['used_seeds'] = sorted(int(x.split(':', 1)[1]) for x in used if x.startswith('seed:'))
        self.state['used_ids'] = sorted(x.split(':', 1)[1] for x in used if x.startswith('instance_id:'))
        self.state['generations'][key] = selected
        _atomic_json(self.path, self.state)
        return [self.root / e['path'] for e in selected]


def layered_replay(instance, example, tokenizer) -> dict:
    """Supplement the builder's D79+D95 replay with the same lookahead decoder."""
    from llm.exit.selection import decoder_factory
    from llm.stowlm.mask import MaskConfig
    from llm.stowlm.sft_data import codec_for, d79_engine_config
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    factory = decoder_factory()
    engine = make_feasibility_engine(instance, get_action_space(), config=d79_engine_config())
    decoder = factory(instance, engine, codec_for(instance, tokenizer), MaskConfig(), 1)[0]
    for position, token in enumerate(example.input_ids[example.prompt_len:]):
        if token not in decoder.allowed_token_ids():
            raise ValueError(f'lookahead replay masked token {position}')
        decoder.advance(token)
    if decoder.status != 'complete' or len(decoder.actions) != example.n_actions:
        raise ValueError('lookahead replay did not complete the entire plan')
    return {'ok': True, 'layer': factory.name, 'tokens': example.completion_len}


def _training_row(job):
    """Full admission + replay, identical in serial and spawn workers."""
    from llm.exit.dataset_pool import worker_tokenizer
    from llm.exit.identity import identity_guard
    from llm.stowlm.sft_data import build_mvp_example
    root = Path(job['root'])
    spec = importlib.util.spec_from_file_location('_exit_mvp_builder', root / 'experiments/mvp/sft/dataset/build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    path, split = Path(job['path']), job['split']
    is_replay, max_len = job['is_replay'], job['max_len']
    forbidden_hashes, forbidden = job['forbidden_hashes'], set(job['forbidden'])
    guard = identity_guard(root / 'experiments/mvp/corpus')
    tokenizer = worker_tokenizer(job.get('tokenizer'))
    recheck = None
    instance, plan, report, digest, ipath = builder.load_triple(Path(path))
    guard.require_allowed(instance, role='train' if split == 'train' else 'loss_val',
                                   raw_sha=digest, input_path=ipath)
    excluded = forbidden if split == 'train' else set(forbidden_hashes) | builder.test_input_hashes()
    banned_parts = ('test', 'showcase', 'val') if split == 'train' else ('test', 'showcase')
    if digest in excluded or (ipath and any(x in ipath.parts for x in banned_parts)):
        raise ValueError(f'held-out corpus map cannot enter SFT: {path}')
    record=json.loads(Path(path).read_text())
    if is_replay:
        from llm.exit.replay import ensure_current_replay
        plan, report, rechecked = ensure_current_replay(path, instance, plan, report,
            Path(job['out_dir']) / 'replay-current-epoch')
        recheck = dict(path=str(path), rechecked=rechecked)
    cached_recovery=False
    if record.get('exit_recovery'):
        if split!='train':
            raise ValueError('recovery targets belong only to training')
        from llm.exit.recovery import admit_cached_example
        example=admit_cached_example(instance,plan,report,
            record['exit_recovery']['admitted_example'],tokenizer=tokenizer)
        cached_recovery=True
    else:
        example = build_mvp_example(instance, plan, report, split=split, source=str(path), tokenizer=tokenizer)
    if not cached_recovery:
        example.meta['exit_layered_replay'] = layered_replay(instance, example, tokenizer)
    example.meta['gold']['input_sha256'] = digest
    if len(example.input_ids) > max_len:
        raise ValueError(f'row exceeds sequence cap: {len(example.input_ids)} > {max_len}')
    return example, recheck

def build_training_dataset(winners: Sequence[str | Path], replay: Sequence[str | Path],
                           out_dir: str | Path, *, validation_records: Sequence[str | Path] = (),
                           tokenizer=None, seed: int = 0, forbidden_hashes: Sequence[str] = (),
                           max_len: int = 10240, replay_ratio: float = .67,
                           recovery_records: Sequence[str | Path] = (),
                           workers: int = 1) -> dict:
    """Use the existing MVP builder admission and add 100% securing+lookahead replay.

    APPROXIMATION: when replay has fewer unique rows than this generation, reuse
    admitted replay examples to preserve the requested training mixture (D110
    defaults to 0.67 replay while saved gold dwarfs generation winners).
    The fixed survival-validation maps are never used as SFT targets.
    """
    from llm.stowlm.sft_data import write_dataset
    if workers == 1:
        from llm.stowlm.format import load_tokenizer
        tokenizer = tokenizer or load_tokenizer()
    spec = importlib.util.spec_from_file_location('_exit_mvp_builder', ROOT / 'experiments/mvp/sft/dataset/build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    from llm.exit.identity import identity_guard
    guard = identity_guard(ROOT / 'experiments/mvp/corpus')
    forbidden = set(forbidden_hashes) | builder.test_input_hashes()
    manifest = ROOT / 'experiments/mvp/corpus/manifest.json'
    if manifest.exists():
        forbidden |= {e['sha256'] for e in json.loads(manifest.read_text())['files']
                      if e.get('split') in ('val', 'test', 'showcase')}
    if not 0 <= replay_ratio < 1:
        raise ValueError('replay_ratio must lie in [0, 1)')
    if not winners:
        raise ValueError('no generation winners to train on')
    from llm.exit.dataset_pool import ordered_pool
    replay_rechecks = []
    def admit(paths, split, *, is_replay=False):
        # Parent performs held-out admission and first-occurrence dedup before
        # dispatch. Workers repeat admission; no cached/inline path can bypass it.
        jobs, seen = [], set()
        for path in paths:
            instance, plan, report, digest, ipath = builder.load_triple(Path(path))
            guard.require_allowed(instance, role='train' if split == 'train' else 'loss_val',
                                  raw_sha=digest, input_path=ipath)
            excluded = forbidden if split == 'train' else set(forbidden_hashes) | builder.test_input_hashes()
            banned_parts = ('test', 'showcase', 'val') if split == 'train' else ('test', 'showcase')
            if digest in excluded or (ipath and any(x in ipath.parts for x in banned_parts)):
                raise ValueError(f'held-out corpus map cannot enter SFT: {path}')
            if digest in seen:
                continue
            seen.add(digest)
            jobs.append(dict(path=str(path), split=split, is_replay=is_replay,
                root=str(ROOT), out_dir=str(out_dir), max_len=max_len,
                forbidden_hashes=list(forbidden_hashes), forbidden=sorted(forbidden),
                tokenizer=tokenizer))
        outcomes = ordered_rows(_training_row, jobs)
        admitted = []
        for example, recheck in outcomes:
            admitted.append(example)
            if recheck is not None:
                replay_rechecks.append(recheck)
        return admitted
    with ordered_pool(workers) as ordered_rows:
        current = admit(winners, 'train')
        recovered=admit(recovery_records,'train')
        current+=recovered
        previous = admit(replay, 'train', is_replay=True)
        current_hashes = {e.meta['gold']['input_sha256'] for e in current}
        previous = [e for e in previous if e.meta['gold']['input_sha256'] not in current_hashes]
        random.Random(seed).shuffle(previous)
        # A first generation without existing gold is admitted, but explicitly reported.
        replay_count = round(len(current)*replay_ratio/(1-replay_ratio))
        mixed = current + ([previous[i % len(previous)] for i in range(replay_count)] if previous else [])
        val = admit(validation_records, 'val')
        train_hashes = {e.meta['gold']['input_sha256'] for e in mixed}
        if train_hashes & {e.meta['gold']['input_sha256'] for e in val}:
            raise ValueError('SFT train/val leakage')
        stats = write_dataset(mixed + val, out_dir, mvp=True)
        report = {'dataset_kind': 'mvp_gold', 'current_rows': len(current), 'replay_rows': len(mixed)-len(current),
                  'recovery_rows':len(recovered),
                  'replay_epoch_admission': replay_rechecks,
                  'requested_replay_ratio': replay_ratio,
                  'actual_replay_ratio': (len(mixed)-len(current))/len(mixed),
                  'validation_rows': len(val), 'layered_replay_fraction': 1.0, 'stats': stats}
        _atomic_json(Path(out_dir) / 'exit_build_report.json', report)
        return report
