"""Prepare D126 BaseException watchdog variant; preserve D121/live bytes."""
import ast
import difflib
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
TARGET = 'experiments/mvp/gold_tools/driver.py'
spec = importlib.util.spec_from_file_location('d121_patch_preparation', HERE.parent/'prepare_watchdog_patches.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)

original = (ROOT/TARGET).read_text()
if 'class _WatchdogExpired(' in original:
    raise ValueError('live driver already defines a watchdog class; review before rebasing')
candidate = previous.gold(original)
needle = "        raise TimeoutError(f'External watchdog exceeded {seconds}s (including swallowed alarm)')"
if candidate.count(needle) != 1:
    raise ValueError('D121 deadline source drift')
candidate = candidate.replace(needle, needle.replace('raise TimeoutError(', 'raise _WatchdogExpired('))
needle = '            previous(*args)\n'
replacement = '''            try:
                previous(*args)
            except Exception as error:
                # A legacy outer handler may still raise ordinary TimeoutError.
                # Preserve this boundary's BaseException cancellation contract.
                raise _WatchdogExpired(f'External watchdog exceeded {seconds}s') from error
'''
if candidate.count(needle) != 1:
    raise ValueError('D121 nested-handler source drift')
candidate = candidate.replace(needle, replacement)
needle = '''        except TimeoutError as error:
            details.update(passed=False, unavailable=True, watchdog_timeout=True,
                           error=f'{type(error).__name__}: {error}')
            row.update(unavailable=True, watchdog_timeout=True, operational_status='unavailable')
            raise
'''
replacement = '''        except (_WatchdogExpired, TimeoutError) as error:
            details.update(passed=False, unavailable=True, watchdog_timeout=True,
                           error=f'{type(error).__name__}: {error}')
            row.update(unavailable=True, watchdog_timeout=True, operational_status='unavailable')
            if isinstance(error, _WatchdogExpired):
                # Convert only after the operational boundary has rejected it;
                # existing worker logging catches Exception, not BaseException.
                raise TimeoutError(str(error)) from error
            raise
'''
if candidate.count(needle) != 1:
    raise ValueError('D121 stage source drift')
candidate = candidate.replace(needle, replacement)
tree = ast.parse(candidate)
node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'deadline')
line = min([node.lineno] + [item.lineno for item in node.decorator_list])-1
lines = candidate.splitlines(keepends=True)
definition = '''class _WatchdogExpired(BaseException):
    """External operational cancellation; never a physical checker verdict."""


'''
candidate = ''.join(lines[:line]) + definition + ''.join(lines[line:])
ast.parse(candidate)
candidate_path = HERE/'gold-driver-d126-candidate.py'
candidate_path.write_text(candidate)
patch_path = HERE/'gold-driver-d126.patch'
patch_path.write_text(''.join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                                fromfile='a/'+TARGET, tofile='b/'+TARGET)))
metadata = dict(decision='D126', applied=False, target=TARGET,
                source_sha256=sha256(original.encode()).hexdigest(),
                candidate_sha256=sha256(candidate.encode()).hexdigest(),
                patch_sha256=sha256(patch_path.read_bytes()).hexdigest(),
                historical_d121_candidate_sha256=sha256((HERE.parent/'gold-driver-candidate.py').read_bytes()).hexdigest(),
                exception_class='_WatchdogExpired(BaseException)',
                stage_boundary='explicit catch records unavailable then raises operational TimeoutError')
(HERE/'PROPOSAL.json').write_text(json.dumps(metadata, indent=2)+'\n')
print(json.dumps({'prepared':True, 'applied':False, 'candidate_sha256':metadata['candidate_sha256']}))
