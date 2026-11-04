"""Persist operational limitations alongside every self-updating result table."""
import json
from pathlib import Path
import sys
import time
import uuid

from ..measurement_lifecycle import durable_json

HERE = Path(__file__).resolve().parents[1]
STATUS = HERE / 'measurements/logic3-operations.json'


def wrap(base):
    if getattr(base, '_review3_operations', False):
        return base
    def refresh(output):
        state = base(output)
        if not STATUS.exists() or not state:
            return state
        operations = json.loads(STATUS.read_text())
        state['operational_limitations'] = operations
        durable_json(Path(output)/'status.json', state)
        note = '\n## Operational limitation\n\n' + operations['message'] + '\n'
        for path in (Path(output)/'RESULTS.md', HERE/'RESULTS.md', HERE/'RESULTS-LIVE.md'):
            content = path.read_text().split('\n## Operational limitation\n', 1)[0]
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(content + note)
            temporary.replace(path)
        return state
    refresh._review3_operations = True
    return refresh


def install():
    changed = []
    target = (HERE/'live_campaign.py').resolve()
    for name, module in list(sys.modules.items()):
        filename = getattr(module, '__file__', None)
        if filename and Path(filename).resolve() == target:
            handler = vars(module).get('_review3_refresh_handler')
            if handler is not None:
                module._review3_refresh_handler = wrap(handler)
                changed.append(name)
    return dict(installed_at=time.time(), modules=changed, reporting_only=True)
