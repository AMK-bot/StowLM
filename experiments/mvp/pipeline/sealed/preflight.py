"""Public-only release checks, lattice drain wait and named idle attestation."""
from __future__ import annotations

from contextlib import contextmanager
import importlib
from pathlib import Path
from threading import Event, Thread
import time

from .common import HERE, ROOT, HarnessError, admission, atomic_json, read_json, sha256

LATTICE = ROOT/'experiments/mvp/pipeline/lattice/hero-coverage'
CORE_STATE = ROOT/'experiments/fleet/core-state.json'


def drain_status(directory=LATTICE):
    directory = Path(directory)
    if not (directory/'STOP').is_file():
        return 'lattice hero-coverage/STOP is absent'
    try:
        active = read_json(directory/'progress.json')['active']
        if isinstance(active, list):
            active = len(active)
        if type(active) is not int or active < 0:
            return 'lattice progress.active is invalid'
        return None if active == 0 else f'lattice hero-coverage has {active} active rows'
    except (OSError, ValueError, KeyError, TypeError):
        return 'lattice progress.json is unavailable or invalid'


def wait_for_drain(directory=LATTICE, *, sleep=time.sleep, emit=print):
    while (reason := drain_status(directory)) is not None:
        emit('Waiting: '+reason, flush=True)
        sleep(5)


def idle_snapshot(*, attested, directory=LATTICE, core_state=CORE_STATE):
    """The operator attests fleet/GPU idle; STOP proves only lattice drain."""
    if not attested or drain_status(directory) is not None:
        return None
    try:
        row = read_json(core_state)
        # Do not replace an explicit contradiction from the telemetry producer.
        if row.get('other_fleet_work', []) != []:
            return None
        return dict(row, other_fleet_work=[], idle_confirmation_source='leader_run_sealed',
                    gpu_idle_operator_attested=True, lattice_stop_and_drained=True,
                    telemetry_source=str(core_state))
    except (OSError, ValueError, TypeError):
        return None


@contextmanager
def admission_bridge(path, *, attested, directory=LATTICE, core_state=CORE_STATE):
    """Refresh owned admission evidence without editing shared fleet telemetry."""
    path = Path(path)
    if not path.resolve().is_relative_to(HERE/'measurements'):
        raise HarnessError('Release admission evidence must stay in sealed/measurements')
    if not attested:
        raise HarnessError('Leader must attest no other fleet work and a free GPU')
    stop = Event()
    def refresh():
        row = idle_snapshot(attested=attested, directory=directory, core_state=core_state)
        # An invalid snapshot explicitly invalidates previously qualifying bytes.
        atomic_json(path, row or dict(t=0, mask=0, load1=0, other_fleet_work=['unconfirmed']))
    refresh()
    def maintain():
        while not stop.wait(2):
            refresh()
    thread = Thread(target=maintain, name='sealed-idle-evidence', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
        atomic_json(path, dict(t=0, mask=0, load1=0, other_fleet_work=['launcher exited']))


def check(config, *, operational=True, directory=LATTICE, core_state=CORE_STATE):
    """Check all public prerequisites except the not-yet-built freeze manifest."""
    from . import driver
    from .budget import measured_budget
    from .calibration import verify_calibration
    from .prepare_launch_config import configuration
    blockers = []
    def attempt(label, function):
        try:
            function()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as error:
            blockers.append(label+': '+str(error))
    def registered():
        expected = configuration()
        if config != expected:
            raise HarnessError('launch-config.json differs from registered prepared settings')
        prereg = read_json(config['preregistration'])
        driver.validate_config(dict(config), prereg)
        if prereg['amendment']['sha256'] != sha256(HERE/'AMENDMENT.md'):
            raise HarnessError('Amendment hash does not match registration')
    def support():
        from .runner_seam import support_check
        support_check()
        if driver.GLOBAL_CLAIM.exists():
            raise HarnessError('Single sealed claim already exists; no retry')
    attempt('registration/config/reader', registered)
    attempt('runner/one-run', support)
    attempt('runtime projection', lambda: measured_budget(config))
    attempt('calibration', lambda: verify_calibration(config.get('cap_calibration_receipt')))
    if operational:
        reason = drain_status(directory)
        if reason:
            blockers.append(reason)
        # This checks capacity only: the actual launcher records the named
        # operator attestation immediately before opening a sealed claim.
        try:
            core = read_json(core_state)
            if core.get('other_fleet_work', []) != []:
                blockers.append('telemetry reports other fleet work')
            mask, load, age = core['mask'], float(core['load1']), time.time()-float(core['t'])
            if type(mask) is not int or mask.bit_count() < 18 or not 0 <= load <= 6 or not 0 <= age < 120:
                blockers.append('quiet capacity requires fresh 18 enabled cores and load1 <= 6')
        except (OSError, ValueError, KeyError, TypeError):
            blockers.append('fresh core telemetry unavailable')
    return blockers
