"""Prepare reviewable watchdog patches/copies; never modify their live targets."""
import ast
import difflib
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def deadline_source(name="deadline", *, cpu=False, exclusive=False):
    kind = "ITIMER_PROF" if cpu else "ITIMER_REAL"
    alarm = "SIGPROF" if cpu else "SIGALRM"
    clock = "process_time" if cpu else "monotonic"
    ownership = ('    if previous_timer[0] > 0:\n'
                 '        raise RuntimeError("smoke requires ownership of its own process alarm")\n') if exclusive else ''
    return f'''@contextmanager
def {name}(seconds):
    """Operational watchdog: expiry is unavailable, never a physical verdict.

    A caught alarm still rejects normal return. Nested scopes preserve the
    earlier timer and restore its remaining time instead of renewing it.
    """
    if isinstance(seconds, bool) or not 0 < float(seconds) < float('inf'):
        raise ValueError('watchdog seconds must be finite and positive')
    seconds = float(seconds)
    clock = time.{clock}
    started = clock()
    previous = signal.getsignal(signal.{alarm})
    previous_timer = signal.getitimer(signal.{kind})
{ownership}    previous_due = started + previous_timer[0] if previous_timer[0] > 0 else None
    own_due = started + seconds
    due = min(own_due, previous_due) if previous_due is not None else own_due
    expired_flag = False
    forwarded_previous = False

    def expired(*args):
        nonlocal expired_flag, forwarded_previous
        expired_flag = True
        if (previous_due is not None and previous_due <= own_due
                and clock() >= previous_due and not forwarded_previous and callable(previous)):
            forwarded_previous = True
            previous(*args)
        raise TimeoutError(f'External watchdog exceeded {{seconds}}s (including swallowed alarm)')

    def reject_expiry():
        if expired_flag or clock() >= due:
            expired()

    signal.signal(signal.{alarm}, expired)
    signal.setitimer(signal.{kind}, max(1e-9, due-clock()))
    try:
        try:
            yield
        except Exception:
            # A late failure report/ordinary exception is still unavailable;
            # operator BaseException cancellation keeps its original meaning.
            reject_expiry()
            raise
        else:
            reject_expiry()
    finally:
        signal.setitimer(signal.{kind}, 0)
        signal.signal(signal.{alarm}, previous)
        if previous_due is not None:
            remaining = previous_due-clock()
            interval = previous_timer[1]
            if remaining <= 0 and interval > 0:
                remaining = interval-((-remaining) % interval)
            if remaining > 0:
                signal.setitimer(signal.{kind}, remaining, interval)
'''


def replace_function(source, name, replacement):
    tree = ast.parse(source)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    start = min([node.lineno] + [item.lineno for item in node.decorator_list])-1
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:start]) + replacement + ''.join(lines[node.end_lineno:])


def output(relative, label, transform):
    original = (ROOT / relative).read_text()
    candidate = transform(original)
    ast.parse(candidate)
    (HERE / f'{label}-candidate.py').write_text(candidate)
    patch = ''.join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                      fromfile='a/'+relative, tofile='b/'+relative))
    (HERE / f'{label}-watchdog.patch').write_text(patch)
    return dict(target=relative, source_sha256=sha256(original.encode()).hexdigest(),
                candidate_sha256=sha256(candidate.encode()).hexdigest(), patch=f'{label}-watchdog.patch', applied=False)


def gold(source):
    source = replace_function(source, 'deadline', deadline_source())
    old = '            with deadline(limit):\n                yield details\n        finally:\n'
    new = '''            with deadline(limit):
                yield details
        except TimeoutError as error:
            details.update(passed=False, unavailable=True, watchdog_timeout=True,
                           error=f'{type(error).__name__}: {error}')
            row.update(unavailable=True, watchdog_timeout=True, operational_status='unavailable')
            raise
        finally:
'''
    if source.count(old) != 1:
        raise ValueError('gold stage source drift; review before preparing')
    return source.replace(old, new)


def exit_smoke(source):
    old = '''    def expired(*_):
        raise SmokeDeadline()
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 600)
    timed_out = False
'''
    new = '''    timed_out = False
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise RuntimeError('GPU smoke requires ownership of its own process alarm')
    def expired(*_):
        nonlocal timed_out
        timed_out = True
        raise SmokeDeadline()
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 600)
'''
    if source.count(old) != 1:
        raise ValueError('loop smoke source drift; review before preparing')
    source = source.replace(old, new)
    old = '''    except SmokeDeadline:
        timed_out = True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
'''
    new = '''    except SmokeDeadline:
        timed_out = True
    finally:
        # Retain the existing ten-minute operational limit even if an inner
        # handler swallowed the BaseException or signal delivery was delayed.
        timed_out = timed_out or time.monotonic()-started >= 600
        signal.setitimer(signal.ITIMER_REAL, 0)
'''
    if source.count(old) != 1:
        raise ValueError('loop smoke completion source drift; review before preparing')
    return source.replace(old, new)


def smoke(source):
    source = replace_function(source, 'deadline', deadline_source(exclusive=True))
    old = '''        except Exception as exc:
            row["stages"][name].update(error=f"{type(exc).__name__}: {exc}",
                                       traceback=traceback.format_exc())
'''
    new = '''        except Exception as exc:
            if isinstance(exc, TimeoutError):
                row["stages"][name].update(unavailable=True, watchdog_timeout=True)
                row.update(unavailable=True, watchdog_timeout=True, operational_status='unavailable')
            row["stages"][name].update(passed=False, error=f"{type(exc).__name__}: {exc}",
                                       traceback=traceback.format_exc())
'''
    if source.count(old) != 1:
        raise ValueError('smoke stage source drift; review before preparing')
    return source.replace(old, new)


if __name__ == '__main__':
    rows = [output('experiments/mvp/gold_tools/driver.py', 'gold-driver', gold),
            output('experiments/mvp/smoke/run_smoke.py', 'smoke', smoke),
            output('experiments/mvp/lookahead/worker.py', 'lookahead', lambda source: replace_function(source, 'cpu_cap', deadline_source('cpu_cap', cpu=True))),
            output('src/llm/exit/runner.py', 'exit-smoke', exit_smoke)]
    (HERE/'PATCHES.json').write_text(json.dumps({'applied':False, 'files':rows}, indent=2)+'\n')
    print(json.dumps({'prepared': len(rows), 'applied':False}))
