"""Conservative fleet admission; edits only this executive's ledger line."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OWNER = 'mvp-exit/local'


def require_current_parent(parent):
    """D117: superseded pilot watchers cannot start a fresh GPU subprocess."""
    policy=ROOT/'experiments/mvp/exit/deployment/parent-policy.json'
    if policy.exists():
        retired=json.loads(policy.read_text()).get('retired_parents',[])
        if Path(parent).resolve() in {(ROOT/p).resolve() for p in retired}:
            raise RuntimeError('D117 parent retired; wait for the completed 8.1 adapter')


def background_cores():
    """Ledger load group; ignore our reserved host/verification cores."""
    ledger=ROOT/'experiments/fleet/ledger.md'
    text='\n'.join(line for line in ledger.read_text().splitlines()
                   if not line.startswith(f'| {OWNER} |'))
    cores,_,_=ledger_totals(text)
    return int(cores) if cores.is_integer() else None


def ledger_totals(text):
    cores, ram, gpu = 0., 0., False
    for line in text.splitlines():
        fields = [p.strip() for p in line.strip('|').split('|')]
        if not line.startswith('|') or len(fields) < 7 or fields[0] == 'executive/job':
            continue
        if set(fields[0]) <= {'-', ' '}:
            continue
        try:
            # Some fleet owners write an explicit upper bound, e.g. '2 max'.
            # Reserve that full bound; arbitrary prose still fails closed.
            def amount(value):
                match=re.fullmatch(r'(\d+(?:\.\d+)?)(?:\s+max)?',value)
                if not match:raise ValueError('unknown numeric reservation')
                number=float(match[1])
                if not math.isfinite(number):raise ValueError('nonfinite reservation')
                return number
            cores += amount(fields[2])
            ram += amount(fields[3])
        except ValueError as exc:
            raise RuntimeError(f'unknown fleet reservation: {line}') from exc
        gpu |= fields[4].lower() not in ('no', 'false', '0', '-')
    return cores, ram, gpu


def pilot_holds_reservation(text):
    return any(line.startswith('|') and len(parts:=line.strip('|').split('|'))>=7
               and parts[0].strip().startswith('fix-t8-sft/') for line in text.splitlines())


def probe():
    memory = subprocess.run(['memory_pressure'], capture_output=True, text=True, check=False)
    match = re.search(r'System-wide memory free percentage:\s*([\d.]+)%', memory.stdout)
    try:
        processes = subprocess.run(['ps', '-Ao', 'pid,pcpu,rss,command'], capture_output=True, text=True, check=False)
        process_snapshot = processes.stdout if processes.returncode == 0 else processes.stderr.strip()
        processes_available = processes.returncode == 0
    except OSError as exc:
        processes_available, process_snapshot = False, str(exc)
    return dict(load_1m=os.getloadavg()[0], memory_free_percent=float(match[1]) if match else None,
                processes_available=processes_available, process_snapshot=process_snapshot)


def _replace_own_line(path, new_line=None):
    # Other executives do not all share a lock. Preserve their latest bytes and
    # retry if changed immediately before this executive's update.
    for _ in range(5):
        before = path.read_text()
        lines = [line for line in before.splitlines() if not line.startswith(f'| {OWNER} |')]
        if new_line:
            lines.append(new_line)
        if path.read_text() == before:
            path.write_text('\n'.join(lines) + '\n')
            return
    raise RuntimeError('fleet ledger changed repeatedly; retry after active updates')


@contextmanager
def execution_lock(path=None):
    path = Path(path or ROOT / 'experiments/mvp/exit/.gpu.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('another ExIt run/smoke holds the shared GPU/job lock') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def reservation(out, *, cores, ram_gb, gpu, estimate_s, load_policy='strict'):
    with execution_lock():
        with _reservation(out, cores=cores, ram_gb=ram_gb, gpu=gpu, estimate_s=estimate_s,
                          load_policy=load_policy) as handle:
            yield handle


def _load_allowed(policy, gpu, cores, snapshot):
    if policy not in ('strict','gpu_smoke','gpu_generation','cpu_reverification_d131'):
        raise ValueError(f'unknown load policy: {policy}')
    if policy=='cpu_reverification_d131':
        if gpu or type(cores) is not int or not 1<=cores<=4:
            raise ValueError('D131 policy requires CPU-only reverification with at most4workers')
        # D131 explicit admission: actual measured load, ledger/RAM limits below
        # still mandatory. Do not fake/clip live load to reuse another policy.
        return snapshot['load_1m']<=18
    if policy != 'strict' and not gpu:
        raise ValueError('GPU load policy requires gpu=True')
    if policy == 'gpu_smoke':
        if cores != 1:
            raise ValueError('gpu_smoke load policy requires exactly one core')
        return snapshot['load_1m'] <= 18
    return policy == 'gpu_generation' or snapshot['load_1m']+cores <= 16


def _capacity_allowed(cores, ram_gb, gpu, others, snapshot):
    other_cores, other_ram, other_gpu = others
    return (other_cores+cores <= 16 and other_ram+ram_gb <= 112 and not (gpu and other_gpu)
            and snapshot['memory_free_percent'] is not None
            and snapshot['memory_free_percent'] >= max(15, ram_gb / 128 * 100))


def _handoff_paths():
    policy=ROOT/'experiments/mvp/exit/deployment/parent-policy.json'
    if policy.exists():
        marker=ROOT/json.loads(policy.read_text())['completion_marker']
        return marker,marker.with_name('TRAIN.DONE')
    marker=ROOT/'experiments/mvp/runs/sft-pilot2-small/polish-summary.json'
    return marker,marker.with_name('polish_summary.json')


class ReservationHandle:
    """Resize this job's verifier budget without changing the shared GPU lease."""

    def __init__(self,out,ledger,estimate,started_utc,load_policy):
        self.out,self.ledger,self.estimate=out,ledger,estimate
        self.started_utc,self.load_policy=started_utc,load_policy
        self.cores,self.ram_gb,self.gpu=estimate['cores'],estimate['peak_ram_gb'],estimate['gpu']
        self.active=True

    def _line(self):
        return (f'| {OWNER} | outcome iteration | {self.cores} | {self.ram_gb} | '
                f'{"yes" if self.gpu else "no"} | {self.estimate["wall_s"]:.0f}s; '
                f'PID {os.getpid()} | {self.started_utc} |')

    def resize_verifiers(self,workers):
        if not self.active:
            raise RuntimeError('reservation has already been released')
        if self.load_policy != 'gpu_generation' or not self.gpu:
            raise ValueError('verifier resizing requires a gpu_generation reservation')
        if not isinstance(workers,int) or isinstance(workers,bool) or workers < 1:
            raise ValueError('verification workers must be a positive integer')
        cores,ram_gb=workers+2,64+2*workers
        snapshot=probe()
        text='\n'.join(line for line in self.ledger.read_text().splitlines()
                       if not line.startswith(f'| {OWNER} |'))
        others=ledger_totals(text)
        reducing=cores<=self.cores and ram_gb<=self.ram_gb
        # Releasing part of an existing lease cannot worsen another owner's
        # newly increased CPU/RAM reservations. GPU/handoff/live-memory gates
        # still apply; increases always require the complete fleet calculation.
        capacity_others=(0,0,others[2]) if reducing else others
        admitted=(not pilot_holds_reservation(text)
                  and any(path.exists() for path in _handoff_paths())
                  and _capacity_allowed(cores,ram_gb,True,capacity_others,snapshot))
        change=dict(utc=datetime.now(timezone.utc).isoformat(),workers=workers,
                    previous_cores=self.cores,previous_ram_gb=self.ram_gb,
                    cores=cores,ram_gb=ram_gb,other_cores=others[0],other_ram_gb=others[1],
                    other_gpu=others[2],live=snapshot,admitted=admitted,
                    capacity_policy='monotone_release' if reducing else 'full_fleet_check')
        self.estimate.setdefault('reservation_changes',[]).append(change)
        if admitted:
            previous=(self.cores,self.ram_gb)
            self.cores,self.ram_gb=cores,ram_gb
            try:
                _replace_own_line(self.ledger,self._line())
            except BaseException:
                self.cores,self.ram_gb=previous
                change['admitted']=False
                raise
            self.estimate.update(cores=max(self.estimate['cores'],cores),
                peak_ram_gb=max(self.estimate['peak_ram_gb'],ram_gb),
                current_cores=cores,current_ram_gb=ram_gb)
        (self.out/'admission.json').write_text(json.dumps({**self.estimate,'admitted':True},indent=2))
        return admitted


@contextmanager
def _reservation(out, *, cores, ram_gb, gpu, estimate_s, load_policy='strict'):
    out = Path(out)
    ledger = ROOT / 'experiments/fleet/ledger.md'
    history = ledger.with_name('history.md')
    snapshot = probe()
    load_allowed = _load_allowed(load_policy,gpu,cores,snapshot)
    ledger_text=ledger.read_text()
    if gpu and pilot_holds_reservation(ledger_text):
        (out/'admission.json').write_text(json.dumps(dict(admitted=False,
            reason='pilot owner still holds its ledger reservation; no GPU work started')))
        raise RuntimeError('GPU reserved for fix-t8-sft until its ledger row is released')
    other_cores, other_ram, other_gpu = ledger_totals(ledger_text)
    estimate = dict(cores=cores, peak_ram_gb=ram_gb, gpu=gpu, wall_s=estimate_s,
                    other_cores=other_cores, other_ram_gb=other_ram, other_gpu=other_gpu,
                    live=snapshot,load_policy=load_policy,reservation_changes=[],
                    current_cores=cores,current_ram_gb=ram_gb)
    # D117 binds admission to completed 8.1, never partially trained weights.
    # Legacy standalone fixtures retain their original pilot handoff.
    if gpu:
        marker,alternate=_handoff_paths()
        if not marker.exists() and not alternate.exists():
            write_path = out / 'admission.json'
            write_path.write_text(json.dumps({**estimate, 'admitted': False,
                'reason': 'required parent completion handoff absent','marker':str(marker)}, indent=2))
            raise RuntimeError('GPU reserved until required parent training/evaluation finishes')
    admitted = (load_allowed and _capacity_allowed(cores,ram_gb,gpu,
                (other_cores,other_ram,other_gpu),snapshot))
    (out / 'admission.json').write_text(json.dumps({**estimate, 'admitted': admitted}, indent=2))
    if not admitted:
        raise RuntimeError('fleet capacity unavailable; see admission.json; no heavy job started')
    started = time.monotonic()
    now = datetime.now(timezone.utc).isoformat()
    handle=ReservationHandle(out,ledger,estimate,now,load_policy)
    _replace_own_line(ledger,handle._line())
    status = 'failed'
    try:
        yield handle
        status = 'completed'
    finally:
        handle.active=False
        actual = dict(estimate=estimate, actual_wall_s=time.monotonic()-started,
                      process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      status=status, started_utc=now)
        # macOS ru_maxrss is bytes; Linux reports KiB.
        if sys.platform != 'darwin':
            actual['process_peak_rss_bytes'] *= 1024
        (out / 'compute.json').write_text(json.dumps(actual, indent=2))
        _replace_own_line(ledger)
        with history.open('a') as stream:
            stream.write(f'\n- {OWNER} {now}: estimated {estimate["cores"]} cores/{estimate["peak_ram_gb"]} GB/'
                         f'GPU={gpu}/{estimate_s:.0f}s; actual {actual["actual_wall_s"]:.1f}s, '
                         f'parent peak RSS {actual["process_peak_rss_bytes"]} bytes; {status}.\n')
