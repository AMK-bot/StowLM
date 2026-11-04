"""Independent sentence-target agreement for labelled binding experiments.

Expected labels screen claims only; they must never become compiler inputs.
Unlabelled live execution cannot establish this agreement independently.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.mvp.reader.schema import canonicalize

HERE = Path(__file__).resolve().parent


def sentence_target_agreement(row, *, cases_path=None, prereg_path=None):
    """Recompute actual-vs-intended agreement against the frozen cohort target.

    Do not trust either ``parse_exact`` or the target copied into a plan record.
    A failure is explicit and cannot earn sentence-binding credit. No expected
    answer is returned, keeping it out of the compiler's execution interface.
    """
    out = dict(parse_agreement=False, sentence_target_verified=False,
               sentence_compliance_verified=False, claim_scope='labelled_sentence_binding')
    try:
        cases_path = Path(cases_path) if cases_path is not None else HERE/'binding-cases.json'
        prereg_path = Path(prereg_path) if prereg_path is not None else HERE/'runs/binding/preregister.json'
        raw = cases_path.read_bytes()
        frozen = json.loads(prereg_path.read_text())
        if hashlib.sha256(raw).hexdigest() != frozen['cases_sha256']:
            raise ValueError('Frozen intended-target cohort changed')
        matches = [case for case in json.loads(raw)['cases'] if case['case_id'] == row.get('case_id')]
        if len(matches) != 1:
            raise ValueError('No unique frozen intended sentence target')
        target = matches[0]
        for key in ('sentence', 'input', 'context'):
            if row.get(key) != target[key]:
                raise ValueError(f'Recorded {key} differs from frozen sentence target')
        expected = canonicalize(target['expected'], target['context'])
        if 'expected' in row and canonicalize(row['expected'], target['context']) != expected:
            raise ValueError('Copied expected answer differs from frozen sentence target')
        out['sentence_target_verified'] = True
        out['parse_agreement'] = canonicalize(row['parsed'], target['context']) == expected
        if not out['parse_agreement']:
            out['sentence_target_failure'] = 'Actual parser response differs from frozen intended sentence target'
    except Exception as error:
        out['sentence_target_failure'] = f'{type(error).__name__}: {error}'
    return out
