"""Verified packet assembly. Missing identities become planner questions."""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from experiments.mvp.pipeline.contract.schema import Question, VoyagePacket, sha256_file, sha256_json
from stowage.io import load_instance
from stowage.model import Container, Instance, YardState


class AssemblyError(ValueError):
    def __init__(self, message, *, status='error', questions=None):
        super().__init__(message)
        self.status = status
        self.questions = list(questions or [])


def _packet(packet):
    try:
        return VoyagePacket.model_validate(packet)
    except ValidationError as error:
        raise AssemblyError(f'Voyage packet is invalid: {error.errors()[0]["msg"]}.') from None


def _base(packet):
    path = Path(packet.base_instance.path).expanduser()
    # Resolve before reading, so symlinks cannot bypass the sealed-set fence.
    resolved = path.resolve()
    parts = [part.lower() for part in resolved.parts]
    if any(part == 'sealed' or part.startswith('sealed-') or part.startswith('sealed_') for part in parts):
        raise AssemblyError('Sealed maps cannot be read by the runner.')
    if 'testv3' in parts and not any(part in ('train', 'validation', 'val') for part in parts):
        raise AssemblyError('Test v3 sealed maps cannot be read by the runner.')
    if 'c6-maps' in parts and any(part in ('holdout', 'parents') for part in parts):
        raise AssemblyError('C6 holdout maps cannot be read by the runner.')
    try:
        actual = sha256_file(resolved)
        if actual != packet.base_instance.sha256:
            raise AssemblyError(f'Base instance hash mismatch: measured {actual}; expected {packet.base_instance.sha256}. Select the matching base file or correct the packet hash and retry.')
        return load_instance(resolved), actual
    except AssemblyError:
        raise
    except (OSError, ValueError) as error:
        if isinstance(error, ValidationError):
            detail = error.errors()[0]
            field = '.'.join(map(str, detail['loc'])) or 'instance'
            raise AssemblyError(f'Base instance field {field} is invalid: {detail["msg"]}. Correct the base instance and retry.') from None
        raise AssemblyError(f'Cannot read the base instance: {error}. Supply a readable, valid instance JSON file and retry.') from None


def _question(name, prompt, reason, *, row=None, field='id'):
    return Question(id=name, row=row, field=field, prompt=prompt, reason=reason)


def _assemble(packet, base):
    payload = base.model_dump(mode='json')
    questions = []
    if packet.load_list is not None and not packet.load_list.rows:
        question = _question('load.empty', 'Provide at least one load-list row.',
                             'An empty replacement load list cannot produce a voyage plan.', field='load_list.rows')
        raise AssemblyError('The load list is empty. Add the cargo rows and retry.',
                            status='needs_answers', questions=[question])
    if packet.yard_state is not None:
        try:
            yard = YardState.model_validate(packet.yard_state.state)
        except ValidationError as error:
            questions.append(_question('yard.invalid', 'Correct the yard snapshot.',
                                       error.errors()[0]['msg'], field='yard_state'))
            raise AssemblyError('The yard snapshot needs an answer.', status='needs_answers', questions=questions) from None
        payload['yard'] = yard.model_dump(mode='json')
    else:
        yard = base.yard
    cargo = {box.id: box.model_dump(mode='json') for box in base.containers}
    yard_ids = {cid for stack in yard.stacks for cid in stack.boxes} | set(yard.quay_buffer)
    if packet.load_list is not None:
        ids = []
        for index, row in enumerate(packet.load_list.rows):
            try:
                box = Container.model_validate(row)
            except ValidationError as error:
                detail = error.errors()[0]
                questions.append(_question(f'load.{index}.invalid', f'Correct load-list row {index + 1}.',
                                           detail['msg'], row=index, field='.'.join(map(str, detail['loc']))))
                continue
            if box.id in ids:
                questions.append(_question(f'load.{index}.duplicate', f'Resolve duplicate box {box.id}.',
                                           'Each load-list identity must occur once.', row=index))
            ids.append(box.id)
            cargo[box.id] = box.model_dump(mode='json')
            if box.id not in yard_ids:
                questions.append(_question(f'load.{index}.missing_yard', f'Where is box {box.id} in the yard?',
                                           'A load-list row has no matching yard identity; location is never guessed.', row=index))
        payload['load_list'] = ids
    for cid in sorted(yard_ids - cargo.keys()):
        questions.append(_question(f'yard.{cid}.unknown', f'Provide the cargo row for yard box {cid}.',
                                   'The yard identity has no container attributes.'))
    if questions:
        raise AssemblyError('Resolve the load-list and yard questions before planning.',
                            status='needs_answers', questions=questions)
    payload['containers'] = list(cargo.values())
    try:
        return Instance.model_validate(payload)
    except ValidationError as error:
        detail = error.errors()[0]
        question = _question('assembly.invalid', 'Correct the load list or yard snapshot.', detail['msg'],
                             field='.'.join(map(str, detail['loc'])) or 'instance')
        raise AssemblyError('The assembled instance needs an answer.', status='needs_answers', questions=[question]) from None


def assemble(packet) -> Instance:
    """Replace cargo attributes/load identities and yard, then revalidate the full model."""
    packet = _packet(packet)
    base, _ = _base(packet)
    return _assemble(packet, base)


def assembly_metadata(packet, instance=None) -> dict:
    """Compare canonical model hashes, independently of the base file's byte hash."""
    packet = _packet(packet)
    base, file_hash = _base(packet)
    assembled = _assemble(packet, base) if instance is None else instance
    base_hash = sha256_json(base.model_dump(mode='json'))
    assembled_hash = sha256_json(assembled.model_dump(mode='json'))
    return dict(base_file_sha256=file_hash, base_instance_sha256=base_hash,
                assembled_instance_sha256=assembled_hash, hash_equal=base_hash == assembled_hash,
                replacements=dict(load_list=packet.load_list is not None, yard_state=packet.yard_state is not None))
