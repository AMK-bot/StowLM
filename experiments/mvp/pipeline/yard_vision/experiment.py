"""Frozen synthetic experiment. Paid runs are explicit; replay is entirely offline."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_file
from .freeze import HERE, ROOT
from .render import render_yard
from .reader import context_sha256, read_yard_picture, reading_context
from .metrics import score_yard
from stowage.model.yard import YardState


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def audited_photo(directory):
    audit = json.loads((directory / 'pair_audit.json').read_text())
    image = directory / audit['photo_file']
    if image.parent.resolve() != directory.resolve():
        raise ValueError('Photo must be inside its recorded case')
    if any(audit.get(field) is not True for field in ('preserved', 'counts_preserved', 'layout_preserved', 'identities_preserved')):
        raise ValueError('Changed or unverifiable repaint is discarded, never scored as a trusted pair')
    if (audit['photo_sha256'] != sha256_file(image)
            or audit['clean_sha256'] != sha256_file(directory / 'clean.png')
            or audit['truth_sha256'] != sha256_file(directory / 'truth.json')):
        raise ValueError('Audited pair or ground truth changed')
    truth = json.loads((directory / 'truth.json').read_text())
    if audit['observed_box_count'] != sum(len(s['boxes']) for s in truth['stacks']):
        raise ValueError('Audited count does not match source scene')
    return image


def prepare():
    manifest = json.loads((HERE / 'fixtures/frozen.json').read_text())
    for case in manifest['cases']:
        directory = HERE / 'fixtures' / case['id']
        if directory.exists():
            raise FileExistsError('Prepared case exists; never overwrite experiment evidence')
        source = ROOT / case['source']
        if sha256_file(source) != case['source_sha256']:
            raise ValueError('Frozen source changed')
        data = json.loads(source.read_text())
        truth = YardState.model_validate(data['yard']).model_dump(mode='json')
        base = {'port': {k: data['port'][k] for k in ('yard', 'yard_max_tiers')},
                'containers': [{k: c[k] for k in ('id', 'length_ft', 'direct_delivery')} for c in data['containers']],
                'load_list': data['load_list'], 'onboard': data['onboard']}
        directory.mkdir(parents=True)
        write(directory / 'base.json', base)
        write(directory / 'truth.json', truth)
        render = render_yard(truth, base, directory / 'clean.png', title=f'Generated synthetic yard | {case["id"]}')
        write(directory / 'render.json', render)
        write(directory / 'case.json', {**case, 'synthetic': True, 'mode': 'generated',
                                      'clean_sha256': sha256_file(directory / 'clean.png')})
    print(json.dumps({'prepared': len(manifest['cases']), 'paid_calls': 0}))


def run(case_id, arm):
    """One paid attempt per frozen image; the durable marker forbids retries."""
    from .clients import YardVisionClient
    directory = HERE / 'fixtures' / case_id
    marker = directory / f'{arm}.attempt.json'
    if marker.exists():
        raise FileExistsError('Attempt already recorded; never retry a scored image')
    image = directory / 'clean.png' if arm == 'clean_render' else audited_photo(directory)
    base = json.loads((directory / 'base.json').read_text())
    write(marker, {'started_utc': datetime.now(timezone.utc).isoformat(),
                   'image_sha256': sha256_file(image), 'automatic_retries': 0,
                   'status': 'started', 'synthetic': True})
    paid = YardVisionClient()

    class Capture:
        mode = 'live'
        evidence = None

        @property
        def records(self):
            return getattr(paid, 'records', [])

        def read(self, path, **kwargs):
            self.evidence = paid.read(path, **kwargs)
            return self.evidence

    capture = Capture()
    started = time.perf_counter()
    result = read_yard_picture(image, base_instance=base, client=capture)
    elapsed = time.perf_counter() - started
    evidence = capture.evidence
    if evidence is None:
        attempt = getattr(paid, 'last_attempt', {})
        evidence = {'stacks': [], 'complete_view': False, 'quay_clear': False,
                    'attempt': attempt, 'read_failed': True,
                    'segments': attempt.get('sam', {}).get('segments', []),
                    'cost_usd': sum(r.cost_usd for r in result.records),
                    'billing_status': 'known_receipts_only; inspect shared ledger for unknown holds'}
    write(directory / f'{arm}.evidence.json', evidence)
    write(directory / f'{arm}.yard_read.json', result.model_dump(mode='json'))
    truth = json.loads((directory / 'truth.json').read_text())
    segments = evidence.get('segments', [])
    score = score_yard(truth, evidence, accepted=result.status == 'complete',
                       wall_s=elapsed, cost_usd=evidence.get('cost_usd', 0),
                       detected_box_count=len(segments) if isinstance(segments, list) else 0)
    if evidence.get('read_failed'):
        score['malformed_evidence'] = True
        score['malformed_reasons'] = ['client_response_unusable; see retained raw attempt']
    score['spark_box_count'] = score_yard(truth, evidence)['box_count']
    score.update(case_id=case_id, arm=arm, status=result.status,
                 questions=len(result.questions), image_sha256=sha256_file(image),
                 context_sha256=context_sha256(base))
    write(directory / f'{arm}.score.json', score)
    write(marker, {**json.loads(marker.read_text()), 'status': 'recorded',
                   'wall_s': elapsed, 'result_status': result.status})
    print(json.dumps({k: score[k] for k in ('case_id', 'arm', 'status', 'questions', 'box_count', 'stack_height', 'id', 'silent_wrong_stacks', 'wall_s', 'cost_usd')}))


def repaint(case_id, *, enhanced=False):
    from .clients import YardVisionClient
    directory = HERE / 'fixtures' / case_id
    stem = 'repaint-v2' if enhanced else 'repaint'
    marker = directory / f'{stem}.attempt.json'
    if marker.exists():
        raise FileExistsError('Repaint already attempted; count discarded scenes without retrying')
    write(marker, {'started_utc': datetime.now(timezone.utc).isoformat(), 'automatic_retries': 0})
    kwargs = {'source_state': json.loads((directory / 'truth.json').read_text())} if enhanced else {}
    photo = 'photo-v2.png' if enhanced else 'photo.png'
    result = YardVisionClient().repaint(directory / 'clean.png', directory / photo, **kwargs)
    write(directory / f'{stem}.json', result)
    print(json.dumps({'case_id': case_id, 'repaint': 'generated', 'cost_usd': result['cost_usd'],
                      'preservation': 'pending independent visual audit'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'read', 'repaint'])
    parser.add_argument('--case', choices=[f'case-{i:02}' for i in range(1, 7)])
    parser.add_argument('--arm', choices=['clean_render', 'repainted_photo'])
    parser.add_argument('--enhanced', action='store_true', help='Development-tuned synthesis manifest prompt')
    arguments = parser.parse_args()
    if arguments.command == 'prepare':
        prepare()
    elif arguments.command == 'read':
        if not arguments.case or not arguments.arm:
            parser.error('read requires --case and --arm')
        run(arguments.case, arguments.arm)
    else:
        if not arguments.case:
            parser.error('repaint requires --case')
        repaint(arguments.case, enhanced=arguments.enhanced)
