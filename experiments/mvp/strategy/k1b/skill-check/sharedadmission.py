"""Cooperative admission hook for the existing supplemental Part 1 worker.

The matched judge imports admission.py directly and is unaffected. The running
Part 1 worker already checks for this optional module on every idle poll.
"""
from pathlib import Path
from admission import probe as leader_probe


def probe():
    row = leader_probe()
    marker = Path(__file__).with_name('extensions-active.json')
    if marker.exists():
        row = dict(row, admitted=False, extension_priority=True)
        row['errors'] = [*row['errors'], 'serial load-free extensions have priority over supplemental samples']
    return row
