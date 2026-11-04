"""Count testcase records in JUnit XML, without double-counting nested suites."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from .paths import contained_output
except ImportError:
    from paths import contained_output

FREEZE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]
FREEZE_RELATIVE = Path('experiments/mvp/pipeline/freeze')


def parse_junit(path: str | Path) -> dict:
    root = ET.parse(path).getroot()
    cases = [element for element in root.iter() if element.tag.rsplit('}', 1)[-1] == 'testcase']
    counts = {'tests': len(cases), 'passed': 0, 'failures': 0, 'errors': 0, 'skipped': 0, 'xfailed': 0}
    elapsed = 0.0
    for case in cases:
        tags = {child.tag.rsplit('}', 1)[-1] for child in case}
        for key, tag in (('failures', 'failure'), ('errors', 'error')):
            counts[key] += sum(child.tag.rsplit('}', 1)[-1] == tag for child in case)
        skipped = [child for child in case if child.tag.rsplit('}', 1)[-1] == 'skipped']
        xfailed = any(child.get('type') == 'pytest.xfail' for child in skipped)
        counts['xfailed'] += int(xfailed)
        counts['skipped'] += int(bool(skipped) and not xfailed)
        counts['passed'] += int(not tags.intersection({'failure', 'error', 'skipped'}))
        elapsed += float(case.get('time', '0'))
    counts['time_s'] = round(elapsed, 6)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xml', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--suite')
    parser.add_argument('--exit-code', type=int)
    parser.add_argument('--root', type=Path, default=ROOT,
                        help='repository root; outputs must remain inside its freeze directory')
    args = parser.parse_args()
    try:
        args.out = contained_output(args.out, args.root.resolve() / FREEZE_RELATIVE)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    try:
        result = parse_junit(args.xml)
    except (OSError, ValueError, ET.ParseError) as error:
        result = {key: None for key in ('tests', 'passed', 'failures', 'errors', 'skipped', 'xfailed', 'time_s')}
        result['parse_error'] = str(error)
    result['junit_xml'] = str(args.xml)
    result['xpassed'] = None
    result['count_note'] = 'Generic JUnit does not identify non-strict xpasses separately from passes.'
    if args.suite is not None:
        result['suite'] = args.suite
    if args.exit_code is not None:
        result['exit_code'] = args.exit_code
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
    if 'parse_error' in result:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
