"""Release gate for a handover explicitly presented as the hero run."""
from __future__ import annotations

import json
import base64
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import StageRecord, chain_intact, sha256_file, sha256_json

HERO_ANCHOR = Path(__file__).with_name('hero-run-anchor.json')


def handover_errors(handover, packet, rules, pipeline_sha256, source=None):
    """Bind the selected reader, words and rule inside the signed handover."""
    selection_path = Path(__file__).resolve().parent.parent / 'radio/hero-selection.json'
    selected = json.loads(selection_path.read_text())
    errors = []
    expected = dict(hero_sentence=selected['hero_sentence'], rule=selected['canonical_rule'])
    for key, value in expected.items():
        if handover.get(key) != value:
            errors.append('hero ' + key + ' differs from adopted bay-9 selection')
    if not pipeline_sha256 or handover.get('pipeline_sha256') != pipeline_sha256:
        errors.append('hero pipeline_sha256 differs from frozen receipt')
    reader = handover.get('reader') or {}
    for key in ('name', 'adapter_sha256', 'decoder_sha256'):
        if reader.get(key) != selected['reader'][key]:
            errors.append('hero reader ' + key + ' differs from adopted reader')
    if [row.get('text') for row in packet.get('voyage_sentences', [])] != [expected['hero_sentence']]:
        errors.append('hero packet does not contain exactly the selected sentence')
    if len(rules) != 1 or rules[0].get('sentence') != expected['hero_sentence'] or rules[0].get('reading') != expected['rule']:
        errors.append('hero receipt rule differs from selected close_bay 9 reading')
    else:
        evidence = rules[0].get('parser') or {}
        for key in ('name', 'adapter_sha256', 'decoder_sha256'):
            if evidence.get(key) != selected['reader'][key]:
                errors.append('hero reading evidence ' + key + ' differs from adopted reader')
        if reader.get('mode') == 'recorded' and (not evidence.get('recorded')
                or evidence.get('label') != 'Recorded reading'):
            errors.append('recorded hero reading is not labelled Recorded reading')
    if source is not None:
        for key in ('hero_sentence', 'rule', 'reader', 'pipeline_sha256'):
            if source.get(key) != handover.get(key):
                errors.append('hero provenance ' + key + ' differs from signed handover')
    return errors


def confirmed_instruction_rows(rules, confirmation, steps, *, reading=None,
                               runner_receipt=None, packet=None):
    """Recover only approval bound to the exact recorded words and readings.

    A runner signature preserves its application attestation, not human identity.
    Recovery also rechecks the recorded reading digest and ordered payload. Old
    hero artifacts without that payload require an independently anchored receipt.
    """
    explicit = [dict(row) for row in rules if row.get('confirmed') is True]
    if len(explicit) == len(rules) or any('confirmed' in row and row['confirmed'] is not True for row in rules):
        return explicit
    confirmation = confirmation or {}
    if (confirmation.get('schema_version') != 'stowlm.binding-confirmation/1'
            or not confirmation.get('source') or not confirmation.get('confirmation_sha256')):
        return explicit
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from experiments.mvp.pipeline.contract.schema import canonical_json
        if (not isinstance(runner_receipt, dict) or not isinstance(packet, dict)
                or runner_receipt.get('rules') != rules or runner_receipt.get('steps') != steps
                or runner_receipt.get('packet_sha256') != sha256_json(packet)):
            return explicit
        unsigned = {key: value for key, value in runner_receipt.items() if key != 'signature'}
        Ed25519PublicKey.from_public_bytes(base64.b64decode(runner_receipt['public_key'], validate=True)).verify(
            base64.b64decode(runner_receipt['signature'], validate=True), canonical_json(unsigned).encode())
        if not chain_intact([StageRecord.model_validate(row) for row in steps]):
            return explicit
        approval = next(row for row in steps if row['stage'] == 'confirmation')
        notes = json.loads(approval['notes'])
        runner = Path(__file__).resolve().parents[1] / 'runner/core.py'
        if (approval.get('source_sha256') != sha256_file(runner)
                or notes.get('confirmed_count') != len(rules)
                or notes.get('confirmation') != confirmation):
            return explicit
        if reading is not None:
            from hashlib import sha256
            # public_brief._json uses JSON's default ASCII escaping, whereas
            # runner stage digests use contract.canonical_json (UTF-8 text).
            payload = json.dumps(reading, sort_keys=True, separators=(',', ':'), allow_nan=False)
            sentences = reading['sentences']
            voyage = [row for row in sentences if row['role'] == 'voyage']
            recorded = [(row['sentence'], row['parsed']) for row in voyage]
            supplied = [(row['sentence'], row.get('reading', row.get('canonical_envelope'))) for row in rules]
            confirmed = dict(confirmed=voyage, confirmation_source=confirmation['source'],
                             standing_questions=[], confirmation=confirmation)
            if (reading.get('schema_version') != 'stowlm.brief-receipt/2'
                    or sha256(payload.encode()).hexdigest() != confirmation.get('receipt_sha256')
                    or recorded != supplied or any(row.get('status') != 'accepted' for row in voyage)
                    or approval.get('input_sha256') != sha256_json(sentences)
                    or approval.get('output_sha256') != sha256_json(confirmed)):
                return explicit
        else:
            anchor = json.loads(HERO_ANCHOR.read_text())
            trusted = anchor.get('trusted_runner_receipts', [anchor['runner_receipt_sha256']])
            if sha256_json(runner_receipt) not in trusted:
                return explicit
    except (OSError, StopIteration, ValueError, KeyError, TypeError, AttributeError, InvalidSignature):
        return explicit
    return [dict(row, confirmed=True, confirmation_source=confirmation['source']) for row in rules]


def hero_provenance(outcome):
    """Audit runner evidence; this is an application attestation, not caller identity."""
    evidence = outcome.get('evidence') or {}
    receipt = outcome.get('receipt') or {}
    errors = []
    # Runner keys are ephemeral. Its embedded key authenticates no origin by itself.
    # Publish this anchor separately only after receiving the actual hero run.
    try:
        anchor = json.loads(HERO_ANCHOR.read_text())
        expected = anchor['runner_receipt_sha256']
        if (not isinstance(expected, str) or len(expected) != 64
                or any(ch not in '0123456789abcdef' for ch in expected)):
            raise ValueError('invalid published runner receipt SHA256')
        digest = sha256_json(receipt)
        trusted = anchor.get('trusted_runner_receipts', [expected])
        if digest != expected and digest not in trusted:
            errors.append('untrusted hero runner receipt: differs from independently published anchor')
        else:
            expected = digest
    except (OSError, ValueError, KeyError, TypeError):
        expected = None
        errors.append('hero runner receipt is not independently published; hero has not landed')
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        from experiments.mvp.pipeline.contract.schema import canonical_json
        unsigned = {key: value for key, value in receipt.items() if key != 'signature'}
        Ed25519PublicKey.from_public_bytes(base64.b64decode(receipt['public_key'], validate=True)).verify(
            base64.b64decode(receipt['signature'], validate=True), canonical_json(unsigned).encode())
    except (KeyError, ValueError, TypeError, InvalidSignature):
        errors.append('original run_packet receipt signature is missing or invalid')
    from experiments.mvp.pipeline.binding.adapter import MANIFEST, verify_manifest, VERSION
    identity = verify_manifest()
    if VERSION != '0.13.0' or not identity['passed']:
        errors.append('pinned Engine 0.13.0 sources do not match')
    if receipt.get('engine_version') != '0.13.0' or receipt.get('engine_manifest_sha256') != sha256_file(MANIFEST):
        errors.append('runner receipt does not identify pinned Engine 0.13.0')
    for key, field in [('packet_sha256', 'packet'), ('instance_sha256', 'instance'), ('plan_sha256', 'plan')]:
        if receipt.get(key) != sha256_json(outcome.get(field)):
            errors.append('runner receipt ' + key + ' differs from handover')
    rules = receipt.get('rules', [])
    if not rules or any(row.get('confirmed') is False for row in rules):
        errors.append('hero requires at least one explicitly confirmed instruction')
    if evidence.get('runner_rules') != rules:
        errors.append('runner instruction evidence differs from its receipt')
    confirmation = outcome.get('confirmation') or evidence.get('confirmation') or {}
    if (confirmation.get('schema_version') != 'stowlm.binding-confirmation/1'
            or not str(confirmation.get('source') or '').strip()
            or len(str(confirmation.get('confirmation_sha256', ''))) != 64
            or len(str(confirmation.get('receipt_sha256', ''))) != 64):
        errors.append('named authenticated host confirmation is missing')
    runner = Path(__file__).resolve().parents[1] / 'runner/core.py'
    source_hash = sha256_file(runner)
    try:
        steps = [StageRecord.model_validate(row) for row in receipt.get('steps', [])]
        if not chain_intact(steps):
            errors.append('runner stage hash chain is broken')
        stages = [row.stage for row in steps]
        required = ['assemble', 'reader', 'confirmation', 'engine', 'checker', 'ladder', 'receipt']
        cursor = -1
        for stage in required:
            cursor = stages.index(stage, cursor + 1)
        for stage in ('assemble', 'confirmation', 'engine', 'checker', 'ladder', 'receipt'):
            row = next(row for row in steps if row.stage == stage)
            if row.source_sha256 != source_hash:
                errors.append(stage + ' was not recorded by current run_packet source')
        assembled = next(row for row in steps if row.stage == 'assemble')
        if assembled.input_sha256 != receipt.get('packet_sha256') or assembled.output_sha256 != receipt.get('instance_sha256'):
            errors.append('runner assembly does not bind this packet and instance')
        approved = json.loads(next(row.notes for row in steps if row.stage == 'confirmation'))
        if approved.get('confirmation') != confirmation or approved.get('confirmed_count') != len(rules):
            errors.append('runner confirmation stage does not bind the confirmed instructions')
        engine = json.loads(next(row.notes for row in steps if row.stage == 'engine'))
        if (engine.get('engine_version') != '0.13.0'
                or engine.get('engine_manifest_sha256') != sha256_file(MANIFEST)
                or engine.get('engine_manifest', {}).get('passed') is not True):
            errors.append('runner engine stage does not bind the pinned manifest')
    except (ValueError, TypeError, StopIteration, AttributeError) as exc:
        errors.append('complete run_packet stage evidence is missing: ' + str(exc))
    return {'eligible': not errors, 'errors': errors, 'runner_source_sha256': source_hash,
            'trusted_runner_receipt_sha256': expected,
            'confirmation': confirmation, 'engine_version_required': '0.13.0',
            'scope': 'Application attestation of recorded run_packet stages; not independent human identity.'}


def require_hero_provenance(outcome):
    result = hero_provenance(outcome)
    if not result['eligible']:
        raise ValueError('hero provenance: ' + '; '.join(result['errors']))
    return result
