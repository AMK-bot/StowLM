"""Pass an already-authorized sealed instance through ordinary runner assembly.

The runner's public loader keeps its private-data fence. Only the sealed worker,
after its single-use load, can supply the process-local provenance callback.
No map is opened here; the runner still revalidates the full assembled model.
"""
from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path
from unittest.mock import patch

from .common import HarnessError, object_hash, resolve

_active = False


def support_check():
    """Check the public runner seam without opening a map or loading a model."""
    from experiments.mvp.pipeline import runner
    from experiments.mvp.pipeline.runner import assembly, core
    if (runner.run_packet is not core.run_packet or runner.assemble is not assembly.assemble
            or core.assemble is not assembly.assemble
            or core.assembly_metadata is not assembly.assembly_metadata
            or not callable(assembly._base)):
        raise HarnessError('Runner assembly aliases differ from the authorized sealed seam')
    parameters = inspect.signature(core.run_packet).parameters
    if not {'packet','parser','out_dir','confirm','confirmation_source'} <= parameters.keys():
        raise HarnessError('Runner confirmation interface differs from the authorized sealed seam')
    return dict(source=str(Path(__file__).resolve()),
        assembly_source=str(Path(assembly.__file__).resolve()),
        core_source=str(Path(core.__file__).resolve()),
        kind='already-authorized-instance; ordinary runner revalidation retained')


def _packet_value(packet):
    from experiments.mvp.pipeline.contract.schema import VoyagePacket
    parsed = VoyagePacket.model_validate(packet)
    return parsed, parsed.model_dump(mode='json')


@contextmanager
def authorized_instance(instance, job, packet, *, require_loaded):
    """Use one exact loaded instance/packet; callback proves worker provenance.

    ``require_loaded(instance, job)`` must return literal True only for the
    current worker's hash-matched, single-use-authorized plan-phase load. A JSON
    field or a caller-supplied truthy marker is never sufficient provenance.
    """
    global _active
    support_check()
    from experiments.mvp.pipeline.runner import assembly
    parsed, expected = _packet_value(packet)
    if (job.get('mode') != 'sealed' or job.get('arm') not in {'pipeline','instruction'}
            or resolve(parsed.base_instance.path) != resolve(job['path'])
            or parsed.base_instance.sha256 != job['sha256'] or parsed.profile != 'standard'
            or parsed.standing != '' or parsed.load_list is not None or parsed.yard_state is not None):
        raise HarnessError('Sealed runner packet differs from its authorized loaded instance')
    if not callable(require_loaded) or require_loaded(instance, job) is not True:
        raise HarnessError('Sealed runner requires the worker authorized-load provenance')
    if _active:
        raise HarnessError('Nested sealed runner assembly is forbidden')
    packet_hash = object_hash(expected)
    instance_hash = object_hash(instance.model_dump(mode='json'))

    def base(candidate):
        _, actual = _packet_value(candidate)
        if (object_hash(actual) != packet_hash
                or require_loaded(instance, job) is not True
                or object_hash(instance.model_dump(mode='json')) != instance_hash):
            raise assembly.AssemblyError('Sealed runner packet or authorized-load provenance changed')
        return instance, job['sha256']

    _active = True
    try:
        with patch.object(assembly, '_base', base):
            yield
    finally:
        _active = False
