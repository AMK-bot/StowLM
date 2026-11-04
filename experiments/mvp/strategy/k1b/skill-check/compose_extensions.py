"""Attach new evidence to the documents read by the existing report publisher."""
from pathlib import Path
import fcntl
import json
import os
import sys
import time

HERE = Path(__file__).resolve().parent
MARKER = '\n<!-- skill-check-extensions -->\n'
SECTIONS = ('status.md', 'part3.md', 'rule-masked.md', 'small-description.md', 'sample-curves.md')


def compose():
    sections = [(HERE/'extensions'/name).read_text() for name in SECTIONS if (HERE/'extensions'/name).exists()]
    if not sections:
        return
    for name in ('part1-sample0.md', 'part1.md'):
        target = HERE/name
        if not target.exists():
            continue
        current = target.read_text()
        base = current.split(MARKER, 1)[0].rstrip()+'\n'
        wanted = base+MARKER+'\n\n'.join(sections)+'\n'
        if wanted != current:
            temp = target.with_name(f'{target.name}.{os.getpid()}.tmp')
            temp.write_text(wanted)
            temp.replace(target)


if __name__ == '__main__':
    if '--follow' not in sys.argv:
        compose()
    else:
        guard = (HERE/'extension-composer.lock').open('a+')
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (HERE/'extension-composer.pid').write_text(str(os.getpid())+'\n')
        while True:
            compose()
            time.sleep(20)
