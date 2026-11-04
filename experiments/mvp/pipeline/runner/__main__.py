"""Offline command line for the packet runner. No model or GPU is loaded here."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def recorded_parser(path: str | Path | None = None, *, expected_sha256=None):
    """Replay authenticated recording bytes through the context-validating parser."""
    from app.brief import RecordedParser, RecordedReadings
    from .stored import authenticate_file, BUNDLED_READINGS_SHA256
    if path is None:
        path = HERE / 'fixtures' / 'readings.json'
        expected_sha256 = expected_sha256 or BUNDLED_READINGS_SHA256
    elif expected_sha256 is None:
        raise ValueError('Custom recorded reader file requires an independently trusted SHA256; supply --recorded-sha256')
    path = Path(path)
    raw = path.read_bytes()
    authenticate_file(path, expected_sha256=expected_sha256, data=raw)
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get('readings'), list):
        raise ValueError('Recorded reader file needs a readings list')
    from app.instruct import normalise
    readings = RecordedReadings.__new__(RecordedReadings)
    readings.normalise, readings.rows = normalise, {}
    for row in data['readings']:
        if (not isinstance(row, dict) or not isinstance(row.get('sentence'), str)
                or not isinstance(row.get('envelope'), dict) or not isinstance(row.get('evidence'), dict)):
            raise ValueError('Every recording needs sentence, envelope and evidence')
        readings.rows.setdefault(normalise(row['sentence']), []).append(dict(
            sentence=row['sentence'], envelope=row['envelope'], evidence=row['evidence'],
            seconds=row.get('seconds'), ship=row.get('ship'), context=row.get('context'),
            source=str(path.resolve())))
    return RecordedParser(readings)


def _json(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description='Turn a synthetic voyage packet into a checked plan package.')
    cli.add_argument('packet', type=Path)
    cli.add_argument('--out', required=True, type=Path, help='Plan package output directory')
    cli.add_argument('--recorded', type=Path, help='Replay a recorded reader JSON file; never calls a model')
    cli.add_argument('--earlier', type=Path, help='Earlier checked no-rule outcome; recheck new limits without another search')
    cli.add_argument('--offline', action='store_true', help='Recorded answers and stored plan only; no network, GPU or live search')
    cli.add_argument('--stored', type=Path, help='Stored plan JSON for --offline; defaults to the bundled synthetic fixture')
    cli.add_argument('--stored-sha256', help='Independently trusted SHA256 of a custom stored-plan file')
    cli.add_argument('--recorded-sha256', help='Independently trusted SHA256 of a custom recorded-reader file')
    cli.add_argument('--confirm-all', action='store_true', help='Batch acceptance of all readings; recorded as batch_confirm_all, not a human confirmation')
    cli.add_argument('--events', action='store_true', help='Print JSON events to stderr as they arrive')
    args = cli.parse_args(argv)
    try:
        from . import run_packet
        packet = json.loads(args.packet.read_text())
        if (args.stored is not None or args.stored_sha256 is not None) and not args.offline:
            raise ValueError('--stored requires --offline')
        if args.recorded is not None and not args.recorded_sha256:
            raise ValueError('--recorded requires --recorded-sha256 from an independently trusted source')

        def on_event(event):
            print(json.dumps(_json(event), ensure_ascii=False), file=sys.stderr, flush=True)

        options = dict(out_dir=args.out, confirm=(lambda reading: True) if args.confirm_all else None,
                       on_event=on_event if args.events else None,
                       confirmation_source='batch_confirm_all' if args.confirm_all else 'none')
        if args.earlier is not None:
            options['earlier'] = json.loads(args.earlier.read_text())
        if args.offline:
            from .offline import run_recorded_packet
            if args.recorded is not None:
                options['readings_path'] = args.recorded
            if args.stored is not None:
                options['stored_path'] = args.stored
            options.update(stored_file_sha256=args.stored_sha256, readings_file_sha256=args.recorded_sha256)
            outcome = _json(run_recorded_packet(packet, **options))
        else:
            parser = (recorded_parser(args.recorded, expected_sha256=args.recorded_sha256)
                      if args.recorded_sha256 else recorded_parser(args.recorded))
            outcome = _json(run_packet(packet, parser=parser, **options))
        print(json.dumps(outcome, ensure_ascii=False, allow_nan=False))
        return 0 if outcome.get('status') == 'checked_plan' else 2
    except KeyboardInterrupt:
        print(json.dumps(dict(status='error', message='Planning interrupted.')), flush=True)
        return 130
    except json.JSONDecodeError as error:
        print(json.dumps(dict(status='error', message=f'A JSON file is malformed at line {error.lineno}, column {error.colno}. Correct the JSON and rerun.')))
        return 1
    except OSError:
        print(json.dumps(dict(status='error', message='An input file could not be read. Check that the input files exist and are readable, then rerun.')))
        return 1
    except ValueError as error:
        print(json.dumps(dict(status='error', message='Packet could not be run: '+str(error)+'. Check the packet and recording files, then rerun.')))
        return 1
    except Exception:
        print(json.dumps(dict(status='error', message='Packet could not be run. Check the packet and recording files, then retry with a fresh output directory.')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
