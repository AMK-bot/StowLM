"""Current hero handover gate: recorded evidence only, no model or checker run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

from .provenance import hero_provenance
from .receipt import verify_receipt

HERE = Path(__file__).resolve().parent
SELECTION = HERE.parent / 'radio/hero-selection.json'
TIMING = HERE.parent / 'reader_final/readings/timing.json'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _identity(reader):
    identity = reader.get('identity') or reader
    name = identity.get('name', reader.get('name'))
    if name == 'StowLM Reader swa-last-5':
        name = 'swa-last-5'
    return {key: name if key == 'name' else identity.get(key, reader.get(key))
            for key in ('name', 'adapter_sha256', 'decoder_sha256')}


def verify_current_hero(path, *, pipeline_sha256):
    """Require the selected bay-9 hero and adopted reader in signed handover.

    This release check audits stored provenance. Regeneration separately runs
    ``verify_package`` with a fresh full checker before publishing the package.
    """
    errors = []
    result = {'passed': False, 'errors': errors, 'pipeline_sha256': pipeline_sha256}
    if not isinstance(pipeline_sha256, str) or not re.fullmatch('[0-9a-f]{64}', pipeline_sha256):
        errors.append('pipeline_sha256: expected a SHA256 of the frozen pipeline manifest')
        return result
    package = Path(path)
    if (package / 'package').is_dir():
        package = package / 'package'
    root = package.parent
    try:
        selected = _read(SELECTION)
        adopted = _identity(_read(TIMING)['identity'])
        sentence, rule = selected['hero_sentence'], selected['canonical_rule']
        if _identity(selected['reader']) != adopted:
            errors.append('reader identity: hero selection differs from adopted reader timing')
        if rule.get('constraint') != {'type': 'close_bay', 'bay': 9}:
            errors.append('hero rule: selection is not close_bay bay 9')
        index = _read(package / 'package.json')
        receipt = _read(package / 'receipt.json')
        runner = _read(package / 'runner-receipt.json')
        outcome = _read(root / 'outcome.json')
        provenance = _read(package / 'provenance.json')
        packet = _read(package / 'packet.json')
        instance = _read(package / 'instance.json')
        plan = _read(package / 'plan.json')
        checker = receipt.get('checker', {})

        checked = verify_receipt(receipt)
        for name in ('signature', 'chain'):
            if not checked[name]['passed']:
                errors.append(name + ': ' + json.dumps(checked[name]['errors'], sort_keys=True))

        files = index.get('files', {})
        required = {'receipt.json', 'runner-receipt.json', 'packet.json', 'instance.json',
                    'plan.json', 'provenance.json', 'hero-provenance.json'}
        if not isinstance(files, dict) or not required.issubset(files):
            errors.append('file hashes: required hero artifacts missing from package index')
            files = files if isinstance(files, dict) else {}
        for name, expected in files.items():
            if Path(name).name != name or not (package / name).is_file():
                errors.append('file hashes: invalid or missing ' + str(name))
            elif sha256_file(package / name) != expected:
                errors.append('file hashes: mismatch ' + name)
        if checker.get('package_files') != {key: value for key, value in files.items() if key != 'receipt.json'}:
            errors.append('file hashes: package index differs from signed manifest')
        if checker.get('package_index') != {key: value for key, value in index.items() if key != 'files'}:
            errors.append('package index: differs from signed metadata')
        if index.get('presentation') != 'hero' or index.get('status') != 'checked_plan':
            errors.append('hero status: package must be presented as a checked hero')
        if outcome.get('status') != 'checked_plan' or outcome.get('passed') is not True or checker.get('passed') is not True:
            errors.append('hero status: checked plan evidence is missing')
        if any(item.get('pipeline_sha256') != pipeline_sha256 for item in (receipt, index, provenance)):
            errors.append('pipeline_sha256: receipt, index or provenance differs from frozen manifest')
        if receipt.get('engine_version') != '0.13.0' or runner.get('engine_version') != '0.13.0':
            errors.append('engine: hero must come from Engine 0.13.0')
        if outcome.get('receipt') != runner:
            errors.append('run_packet: saved outcome and original runner receipt differ')
        for key, value in (('packet', packet), ('instance', instance), ('plan', plan)):
            if outcome.get(key) != value or receipt.get(key + '_sha256') != sha256_json(value):
                errors.append('run_packet: handover ' + key + ' differs from saved outcome')
        audit = hero_provenance(outcome)
        if not audit['eligible']:
            errors.extend('run_packet: ' + str(error) for error in audit['errors'])
        if checker.get('hero_provenance') != audit or _read(package / 'hero-provenance.json') != audit:
            errors.append('run_packet: signed hero provenance differs from audited evidence')

        expected_handover = dict(reader=adopted, hero_sentence=sentence,
                                 rule=rule, pipeline_sha256=pipeline_sha256)
        handover = checker.get('hero_handover') or {}
        for key, expected in expected_handover.items():
            actual = _identity(handover.get(key) or {}) if key == 'reader' else handover.get(key)
            if actual != expected:
                errors.append('hero handover: signed ' + key + ' differs from selection')
        if _identity(provenance.get('reader') or {}) != adopted:
            errors.append('reader identity: provenance does not name the adopted reader and hashes')
        sentences = [row.get('text') for row in packet.get('voyage_sentences', [])]
        if sentences != [sentence] or str(packet.get('standing') or '').strip():
            errors.append('hero sentence: packet must contain only the selected instruction')
        rules = receipt.get('rules', [])
        # The preserved runner audit predates per-row approval flags. Audit its
        # exact original evidence first, then compare the same normalized form
        # used by the handover writer. Retained v3 packages may also have raw
        # rows; their approval must come from that same authenticated evidence.
        from . import _normalise_approval
        approval_evidence = dict(reading=outcome.get('reading'), runner_receipt=runner, packet=packet)
        approved = _normalise_approval(runner.get('rules', []), outcome.get('confirmation'),
                                       runner.get('steps', []), **approval_evidence)
        normalized_rules = _normalise_approval(rules, outcome.get('confirmation'),
                                               runner.get('steps', []), **approval_evidence)
        if normalized_rules != approved:
            errors.append('hero rule: package and independently audited original runner rules differ')
        if (len(rules) != 1 or rules[0].get('sentence') != sentence
                or rules[0].get('reading') != rule or rules[0].get('recheck', {}).get('passed') is not True):
            errors.append('hero rule: exact selected close_bay reading and successful recheck required')
        else:
            parser = rules[0].get('parser') or {}
            if _identity(parser) != adopted or rules[0].get('adapter_sha256') != adopted['adapter_sha256']:
                errors.append('reader identity: original runner instruction has different adapter or decoder')
        if len(approved) != 1 or approved[0].get('confirmed') is not True:
            errors.append('confirmation: selected instruction was not confirmed before execution')
        readers = [row for row in runner.get('steps', [])
                   if row.get('stage') == 'reader' and row.get('adapter_sha256') is not None]
        if len(readers) != 1 or readers[0].get('adapter_sha256') != adopted['adapter_sha256']:
            errors.append('reader stage: adopted adapter hash is missing from original run_packet chain')

        frames = _read(root / 'native-frames.json')
        if (frames.get('complete') is not True or frames.get('passed') is not True
                or frames.get('instance_sha256') != sha256_json(instance)
                or frames.get('plan_sha256') != sha256_json(plan)
                or len(frames.get('frames', [])) != len(plan.get('moves', [])) + 1
                or provenance.get('native_frames_sha256') != sha256_file(root / 'native-frames.json')):
            errors.append('native frames: complete checked checkpoint does not bind the handover plan')
        result.update(hero_sentence=sentence, rule=rule, reader=adopted)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append('hero artifacts: ' + str(exc))
    result['passed'] = not errors
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path, default=HERE / 'fixtures/hero-run')
    parser.add_argument('--pipeline-sha256', required=True)
    args = parser.parse_args()
    result = verify_current_hero(args.path, pipeline_sha256=args.pipeline_sha256)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
