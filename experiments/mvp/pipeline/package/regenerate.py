"""Regenerate a published hero through the real packet runner and stored lattice."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
from unittest.mock import patch

from experiments.mvp.pipeline.contract.schema import VoyagePacket, sha256_file, sha256_json
from . import write_package, verify_package
from .build_fixture import wait_for_admission
from . import provenance
from .confirmation import BATCH_CONFIRMATION_SOURCE

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LATTICE = HERE.parent / 'lattice'
HERO_SELECTION = HERE.parent / 'radio/hero-selection.json'


def load_pipeline_manifest(path, expected_sha256):
    """Require an explicitly selected, unchanged frozen pipeline before launch."""
    if path is None or not expected_sha256:
        raise ValueError('pipeline_manifest and pipeline_sha256 are required; freeze the pipeline first')
    path = Path(path).resolve()
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(ch not in '0123456789abcdef' for ch in expected_sha256)):
        raise ValueError('pipeline_sha256 must be an explicit lowercase SHA256')
    if sha256_file(path) != expected_sha256:
        raise ValueError('pipeline manifest SHA256 differs from passed pipeline_sha256')
    if _read(path).get('scope') != 'pipeline':
        raise ValueError('pipeline manifest must identify the pipeline scope, not film')
    from experiments.mvp.pipeline.freeze.verify import require_frozen
    identity = require_frozen(path)
    if identity['manifest_sha256'] != expected_sha256 or sha256_file(path) != expected_sha256:
        raise ValueError('pipeline manifest changed during freeze verification')
    return dict(identity, path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path))


def _read(path):
    path = Path(path).resolve()
    if 'sealed' in path.parts:
        raise ValueError('sealed inputs are outside package regeneration scope')
    return json.loads(path.read_text())


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


@contextmanager
def _runtime():
    """Use the services unchanged, directing their numerical caches into our fence."""
    from experiments.mvp.pipeline.lattice import gates
    from experiments.mvp.pipeline.runner import core, runtime
    with ExitStack() as stack:
        stack.enter_context(patch.object(gates, 'HERE', HERE))
        stack.enter_context(gates.runtime_cache_scope())
        stack.enter_context(patch.object(runtime, 'HERE', HERE))
        stack.enter_context(patch.object(runtime, '_configured', False))
        stack.enter_context(patch.dict(os.environ, {'STOWAGE_VESSEL_CACHE_DIR': str(HERE / 'cache/vessels')}))
        yield core


def selected_inputs(hero_path, selection, instruction_path=None):
    """Reject mismatched or sealed source metadata before any model or computation."""
    hero_path = Path(hero_path).resolve()
    document = _read(hero_path)
    selected = document[selection]
    instruction_path = Path(instruction_path or HERO_SELECTION).resolve()
    instruction = _read(instruction_path)
    if instruction.get('schema_version') == 'stowlm.hero-selection/1':
        instruction = dict(instruction, instance_sha256=selected['instance_sha256'],
                           canonical_rules=[instruction['canonical_rule']], sentences=[instruction['hero_sentence']])
    source = (ROOT / selected['path']).resolve()
    if not source.is_relative_to(ROOT) or 'sealed' in source.parts or '/maps/train/' not in str(source):
        raise ValueError('hero source must be the explicitly approved unsealed development map')
    if sha256_file(source) != selected['file_sha256']:
        raise ValueError('hero source file SHA256 differs from selection')
    from stowage.model import Instance
    with patch.dict(os.environ, {'STOWAGE_VESSEL_CACHE_DIR': str(HERE / 'cache/vessels')}):
        instance = Instance.model_validate(_read(source))
    instance_hash = sha256_json(instance.model_dump(mode='json'))
    if instance_hash != selected['instance_sha256'] or instruction['instance_sha256'] != instance_hash:
        raise ValueError('instruction does not identify the selected hero instance')
    rules, sentences = instruction['canonical_rules'], instruction['sentences']
    if not rules or len(rules) != len(sentences):
        raise ValueError('hero needs one canonical reading per sentence')
    adopted = _read(HERO_SELECTION)
    if rules != [adopted['canonical_rule']] or sentences != [adopted['hero_sentence']]:
        raise ValueError('hero instruction differs from the adopted bay-9 sentence and rule')
    return selected, instruction, instance, source, instruction_path


def _recorded_reader(instance, sentences, *, record=False):
    from experiments.mvp.reader.schema import context_from_instance, answer_envelope
    from experiments.mvp.pipeline.runner.__main__ import recorded_parser
    context = context_from_instance(instance)
    key = sha256_json({'context': context, 'sentences': ['', *sentences]})
    path = HERE / 'reader-recordings' / (key + '.json')
    pin = path.with_suffix('.sha256')
    if record:
        from experiments.mvp.reader import pipeline
        rows = _read(path)['readings'] if path.exists() else []
        if path.exists() and (not pin.exists() or sha256_file(path) != pin.read_text().strip()):
            raise ValueError('existing reader recording does not match its published SHA256')
        parser = None
        for sentence in ['', *sentences]:
            if any(row['sentence'] == sentence for row in rows):
                continue
            parser = parser or pipeline.SparkParser(purpose='pipeline-package-hero-reader', allocation_usd=Decimal('.50'))
            began = perf_counter()
            answer, evidence = parser.parse(sentence, context)
            evidence['source_sha256'] = sha256_file(pipeline.__file__)
            rows.append(dict(sentence=sentence, envelope=answer_envelope(answer), evidence=evidence,
                             seconds=perf_counter()-began, ship=instance.id, context=context))
            _write(path, {'label': 'Recorded Muse Spark reader answers; synthetic development ship', 'readings': rows})
            pin.write_text(sha256_file(path) + '\n')
    if not path.exists() or not pin.exists():
        raise ValueError('No reader recording for this hero. Use --record-reader once, or --reader-factory MODULE:FUNCTION.')
    digest = pin.read_text().strip()
    parser = recorded_parser(path, expected_sha256=digest)
    parser.recorded_offline = True
    return parser, {'mode': 'recorded', 'path': str(path.relative_to(ROOT)), 'sha256': digest,
                    'original_cost_usd': sum(float(row['evidence'].get('cost_usd', 0)) for row in _read(path)['readings'])}


def seed_qualified_tables(instance):
    """Copy only this hull's existing acceleration data; native certificates still gate use."""
    from stowage.checker.stability import _context
    from stowage.checker import stability_tables, stability_liquid
    geometry, params = _context(instance.vessel)
    key = stability_tables._digest(stability_tables.cache_metadata(geometry, params, instance.port.water_density_kg_m3))
    source = LATTICE / 'runtime-cache/stability'
    paths = set()
    for path in source.glob(key + '-*.npz'):
        certificate = path.with_suffix('.certificate.json')
        if certificate.is_file():
            paths.update((path, certificate))
    manifest = source / stability_liquid.manifest_path(params).name
    if manifest.is_file():
        paths.add(manifest)
        for identity in _read(manifest).get('tank_table_keys', {}).values():
            paths.update((source / ('liquid-' + identity + '.npz'), source / ('liquid-' + identity + '.qualified.json')))
    copied = {}
    for path in sorted(paths):
        if not path.is_file() or path.is_symlink():
            continue
        digest = sha256_file(path)
        for target in (HERE / 'runtime-cache/stability', HERE / 'cache/stability'):
            target.mkdir(parents=True, exist_ok=True)
            destination = target / path.name
            if not destination.exists() or sha256_file(destination) != digest:
                temporary = destination.with_name('.' + destination.name + '.seed')
                shutil.copy2(path, temporary)
                temporary.replace(destination)
        copied[path.name] = digest
    return {'source': str(source.relative_to(ROOT)), 'files': copied,
            'scope': 'Existing qualified acceleration tables only; native metadata, SHA and qualification checks remain unchanged.'}


def _publish_anchor(receipt, source):
    old = _read(provenance.HERO_ANCHOR) if provenance.HERO_ANCHOR.exists() else {}
    digest = sha256_json(receipt)
    trusted = set(old.get('trusted_runner_receipts', []))
    if old.get('runner_receipt_sha256'):
        trusted.add(old['runner_receipt_sha256'])
    trusted.add(digest)
    _write(provenance.HERO_ANCHOR, dict(schema_version='stowlm.published-hero-runs/1',
        runner_receipt_sha256=digest, trusted_runner_receipts=sorted(trusted),
        publication_source='Captured directly from the package callback inside run_packet',
        latest_source=source, published_utc=datetime.now(timezone.utc).isoformat()))


def regenerate(*, hero_path=LATTICE / 'hero.json', selection='hero', instruction_path=None,
               out_dir=HERE / 'fixtures/hero-run', parser=None, reader_factory=None,
               confirmation_source=BATCH_CONFIRMATION_SOURCE, record_reader=False,
               readings_path=None, readings_sha256=None, reader_mode='live',
               pipeline_manifest=None, pipeline_sha256=None):
    """Run the stored bay-9 plan with exact-reading batch confirmation; never search.

    ``reader_factory`` is MODULE:FUNCTION returning the adopted parse(sentence,
    context) provider. Its model/adapter evidence is retained by run_packet.
    """
    if confirmation_source != BATCH_CONFIRMATION_SOURCE:
        raise ValueError('automatic hero regeneration requires the disclosed batch confirmation source: '
                         + BATCH_CONFIRMATION_SOURCE)
    started = perf_counter()
    destination = Path(out_dir).resolve()
    if not destination.is_relative_to(HERE):
        raise ValueError('package regeneration output must stay inside package/')
    if readings_path is not None and not readings_sha256:
        raise ValueError('custom readings need an independently trusted readings_sha256')
    frozen = load_pipeline_manifest(pipeline_manifest, pipeline_sha256)
    selected, instruction, instance, source, instruction_path = selected_inputs(hero_path, selection, instruction_path)
    if sum(value is not None for value in (parser, reader_factory, readings_path)) > 1:
        raise ValueError('choose an injected parser, reader_factory or authenticated readings file')
    if readings_path is not None:
        from experiments.mvp.pipeline.runner.__main__ import recorded_parser
        if not readings_sha256:
            raise ValueError('custom readings need an independently trusted readings_sha256')
        if 'sealed' in Path(readings_path).resolve().parts:
            raise ValueError('sealed readings are outside package regeneration scope')
        parser = recorded_parser(readings_path, expected_sha256=readings_sha256)
        parser.recorded_offline = True
        reader = {'mode': 'recorded', 'path': str(readings_path), 'sha256': readings_sha256}
    elif reader_factory is not None:
        module, function = reader_factory.split(':', 1)
        parser = getattr(importlib.import_module(module), function)()
        reader = {'mode': 'live', 'factory': reader_factory}
    elif parser is not None:
        reader = {'mode': 'injected', 'provider': type(parser).__name__}
    else:
        if record_reader:
            raise ValueError('hero uses the adopted StowLM Reader; Spark recording is retired')
        from .hero_reader import adopted_parser
        parser, reader = adopted_parser(mode=reader_mode, selection_path=HERO_SELECTION)
    adopted = _read(HERO_SELECTION)['reader']
    if any(reader.get(key) != adopted[key] for key in ('name', 'adapter_sha256', 'decoder_sha256')):
        raise ValueError('hero reader does not identify adopted StowLM Reader swa-last-5')
    rules = instruction['canonical_rules']
    packet = VoyagePacket(packet_id=f'package-{selection}-{selected["instance_sha256"][:16]}',
        created_utc=datetime.now(timezone.utc).isoformat(),
        base_instance={'path': str(source.relative_to(ROOT)), 'sha256': sha256_file(source)},
        standing='', voyage_sentences=[{'text': text, 'origin': 'typed'} for text in instruction['sentences']],
        profile='standard').model_dump(mode='json')
    cache_seed = seed_qualified_tables(instance)
    destination.parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix='.hero-build-', dir=destination.parent))
    prior_anchor = provenance.HERO_ANCHOR.read_bytes() if provenance.HERO_ANCHOR.exists() else None
    captured = False
    try:
        with _runtime() as runner:
            runner_source = sha256_file(runner.__file__)
            from experiments.mvp.pipeline import lattice
            from experiments.mvp.pipeline.lattice import gates
            wait_for_admission()
            print(json.dumps({'stage': 'lattice.lookup', 'selection': selection, 'instance': instance.id}), flush=True)
            native_gate = gates.gate_plan
            def checkpointed_gate(*args, **kwargs):
                return native_gate(*args, frame_checkpoint_path=building / 'native-frames.json', **kwargs)
            with patch.object(gates, 'gate_plan', checkpointed_gate):
                candidate = lattice.lookup(selected['instance_sha256'], rules)
            if candidate is None:
                raise ValueError('lattice lookup withheld this stored plan; no live search fallback')
            if candidate['plan'] is None or (instruction.get('plan_sha256') is not None
                    and sha256_json(candidate['plan']) != instruction['plan_sha256']):
                raise ValueError('lattice candidate plan differs from selected instruction artifact')
            if sha256_file(runner.__file__) != runner_source:
                raise ValueError('runner changed during lookup; repeat regeneration in a fresh process')
            _write(building / 'stored-candidate.json', candidate)
            provenance_data = dict(label='Stored synthetic development hero through run_packet', selection=selection,
                hero_source_path=str(Path(hero_path).resolve().relative_to(ROOT)),
                hero_source_sha256=sha256_file(hero_path), instruction_path=str(instruction_path.relative_to(ROOT)),
                instruction_sha256=sha256_file(instruction_path), instance_sha256=selected['instance_sha256'],
                lattice_candidate_sha256=sha256_json(candidate), lattice_key=candidate['key'],
                lattice_gate=candidate['gate'], reader=reader, confirmation_source=confirmation_source,
                hero_sentence=instruction['sentences'][0], rule=rules[0],
                pipeline_sha256=pipeline_sha256, pipeline_manifest=frozen,
                cache_seed=cache_seed,
                native_frames_sha256=sha256_file(building / 'native-frames.json'),
                original_plan_producer=candidate.get('plan_producer'), producer_label=candidate.get('producer_label'))

            def publish(outcome, directory):
                nonlocal captured
                if outcome.get('status') != 'checked_plan':
                    raise ValueError('hero must pass the complete original-instance checker')
                load_pipeline_manifest(pipeline_manifest, pipeline_sha256)
                _publish_anchor(outcome['receipt'], provenance_data)
                captured = True
                outcome.update(presentation='hero', fixture_provenance=provenance_data,
                               pipeline_sha256=pipeline_sha256, hero_handover=dict(
                                   reader=reader, hero_sentence=instruction['sentences'][0],
                                   rule=rules[0], pipeline_sha256=pipeline_sha256))
                try:
                    return write_package(outcome, directory)
                except Exception as error:
                    _write(building / 'package-error.json', {'type': type(error).__name__, 'error': str(error)})
                    raise

            def confirm(row):
                return any(row['sentence'] == text and row.get('parsed') == rule
                           for text, rule in zip(instruction['sentences'], rules))

            def event(row):
                if row['kind'] in {'stage_start', 'stage_end', 'error'}:
                    print(json.dumps({'stage': row['stage'], 'kind': row['kind']}), flush=True)

            wait_for_admission()
            with patch.object(runner, 'write_package', publish):
                outcome = runner.run_packet(packet, parser=parser, out_dir=building, stored=candidate,
                    stored_sha256=sha256_json(candidate), confirm=confirm,
                    confirmation_source=confirmation_source, on_event=event)
            if outcome.get('status') != 'checked_plan' or not captured:
                raise ValueError('run_packet did not produce a checked hero: ' + outcome.get('message', 'unknown failure'))
            wait_for_admission()
            verified = verify_package(building / 'package')
            _write(building / 'verification.json', verified)
            if not verified['passed']:
                raise ValueError('fresh hero verification failed: ' + json.dumps({k: v for k, v in verified.items()
                    if isinstance(v, dict) and not v.get('passed', True)}))
            load_pipeline_manifest(pipeline_manifest, pipeline_sha256)
        summary = dict(passed=True, selection=selection, instance_id=instance.id, boxes=len(instance.load_list),
            engine_version=outcome['receipt']['engine_version'], package_dir=str(destination / 'package'),
            runner_receipt_sha256=sha256_json(outcome['receipt']), plan_sha256=sha256_json(outcome['plan']),
            verification_wall_s=verified['wall_s'], wall_s=perf_counter()-started, reader=reader,
            hero_sentence=instruction['sentences'][0], rule=rules[0], pipeline_sha256=pipeline_sha256,
            confirmation_source=confirmation_source, print_check='Leader will run outside sandbox')
        outcome['package_dir'] = str(destination / 'package')
        _write(building / 'outcome.json', outcome)
        _write(building / 'regeneration.json', summary)
        from .hero_acceptance import verify_current_hero
        acceptance = verify_current_hero(building, pipeline_sha256=pipeline_sha256)
        _write(building / 'hero-acceptance.json', acceptance)
        if not acceptance['passed']:
            raise ValueError('current hero handover failed: ' + json.dumps(acceptance['errors']))
        if destination.exists():
            history = destination.parent / 'history'
            history.mkdir(exist_ok=True)
            destination.rename(history / (destination.name + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')))
        building.rename(destination)
        return summary
    except BaseException:
        if captured:
            if prior_anchor is None:
                provenance.HERO_ANCHOR.unlink(missing_ok=True)
            else:
                provenance.HERO_ANCHOR.write_bytes(prior_anchor)
        # Keep the actual failed runner outcome for diagnosis; never present as hero.
        if building.exists():
            failed = destination.parent / ('failed-' + building.name.removeprefix('.'))
            building.rename(failed)
        raise


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--hero', type=Path, default=LATTICE / 'hero.json')
    cli.add_argument('--selection', choices=['hero', 'backup'], default='hero')
    cli.add_argument('--instruction', type=Path)
    cli.add_argument('--out', type=Path, default=HERE / 'fixtures/hero-run')
    cli.add_argument('--pipeline-manifest', type=Path, required=True)
    cli.add_argument('--pipeline-sha256', required=True, help='SHA256 of the exact frozen pipeline manifest bytes')
    cli.add_argument('--reader-mode', choices=['live', 'recorded'], default='live')
    cli.add_argument('--reader-factory', help='MODULE:FUNCTION returning the adopted reader parser')
    cli.add_argument('--readings', type=Path, help='Adopted reader recordings for offline replay')
    cli.add_argument('--readings-sha256', help='Independently trusted hash of --readings')
    cli.add_argument('--record-reader', action='store_true', help=argparse.SUPPRESS)
    args = cli.parse_args(argv)
    try:
        print(json.dumps({'stage': 'regenerate.start', 'pid': os.getpid()}), flush=True)
        print(json.dumps(regenerate(hero_path=args.hero, selection=args.selection, instruction_path=args.instruction,
            out_dir=args.out, reader_factory=args.reader_factory, record_reader=args.record_reader,
            readings_path=args.readings, readings_sha256=args.readings_sha256, reader_mode=args.reader_mode,
            pipeline_manifest=args.pipeline_manifest, pipeline_sha256=args.pipeline_sha256), indent=2))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'passed': False, 'error': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
