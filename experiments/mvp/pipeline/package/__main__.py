"""Offline package writer and verifier."""
import argparse
import json
from pathlib import Path

from . import verify_package, write_package


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    writer = commands.add_parser('write')
    writer.add_argument('outcome', type=Path)
    writer.add_argument('out_dir', type=Path)
    verifier = commands.add_parser('verify')
    verifier.add_argument('path', type=Path)
    verifier.add_argument('--result', type=Path, help='Save complete JSON, including the fresh checker report')
    verifier.add_argument('--expected-public-key', type=Path, help='Trusted PEM file; defaults to published application key')
    args = parser.parse_args(argv)
    try:
        if args.command == 'write':
            result = write_package(json.loads(args.outcome.read_text(encoding='utf-8')), args.out_dir)
            print(json.dumps(result, indent=2))
            return 0
        result = verify_package(args.path, expected_public_key=args.expected_public_key)
        if args.result:
            args.result.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        summary = {name: {k: v for k, v in result[name].items() if k != 'report'}
                   for name in ('files', 'chain', 'signature', 'checker', 'rules', 'coverage', 'handover')}
        print(json.dumps(dict(passed=result['passed'], wall_s=result['wall_s'], checks=summary), indent=2))
        return 0 if result['passed'] else 1
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
