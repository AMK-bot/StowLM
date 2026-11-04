"""K1b vs block-rule (compacted, 'Compact selected' column of BLOCKRULE.md's small-val table), same maps, appended to NULL.md's folder as
BLOCKRULE-PAIR.md. Refuses unless every K1b map has a block-rule row. Usage: pair_blockrule.py"""
from __future__ import annotations

import json
from pathlib import Path
import re

import k1b_report as r

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent/'blockrule/BLOCKRULE.md'


def blockrule_rows(text):
    """{map name: compacted selected moves or None} from the small-val section's table."""
    section = text.split('## small-val', 1)[1].split('\n## ', 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip('|').split('|')]
        if line.startswith('| small-') and len(cells) == 10:
            value = cells[9].split()[0]
            if value != 'pending':  # the run hasn't reached this map, so it counts as missing
                rows[cells[0]] = None if value == '—' else float(value)
    return rows


def map_name(input_path):
    match = re.search(r'small/(feeder_\d+)/(\w+)/seed-(\d+)\.json', input_path)
    return f'small-{match[1]}-{match[2]}-{match[3]}'


def main():
    model = [json.loads(p.read_text()) for p in (HERE/'judged/model').glob('*.json')]
    rule = blockrule_rows(SOURCE.read_text())
    missing = [m['input'] for m in model if map_name(m['input']) not in rule]
    if missing:
        raise SystemExit(f'{len(missing)} K1b maps have no block-rule row yet')
    mine = {m['instance_id']: m['moves'] for m in model}
    rival = {m['instance_id']: rule[map_name(m['input'])] for m in model}
    p = r.pair(mine, rival)
    lines = ['# K1b vs block-rule (D185), same 55 validation maps', '',
             "Block-rule is the 'Compact selected' column of `blockrule/BLOCKRULE.md`, small-val: the better passing of default two-step and the port rule, each compacted. "
             'Exact two-sided sign tests, ties dropped, no adjustment.', '', *r.HEAD, r.row_of('8.2 K1b vs block-rule', p), '',
             f"**Maps only K1b passes:** {', '.join(p['only_left']) or 'none'}. **Maps only block-rule passes:** {', '.join(p['only_right']) or 'none'}.", '']
    (HERE/'BLOCKRULE-PAIR.md').write_text('\n'.join(lines))
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
