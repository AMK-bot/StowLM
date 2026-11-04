"""Untrained Glimmer on the mask study's same 200 sentences, through the same recorder and mask.

Fills "untrained N of 200, fine-tuned 2 of 200". The mask-study store resumes, and the best
block is saved under the rule's label. The comparison counts sentences with a real block.
Usage: python -m experiments.mvp.pipeline.reader_final.base_arm
"""
from __future__ import annotations

import json

from experiments.mvp.reader.v3 import mask_study


def main():
    mask_study.run_child('base')
    sample, _ = mask_study.study_rows()
    base = mask_study.load_store(mask_study.STUDY / 'base.jsonl')
    adopted = json.loads(mask_study.REPORT.read_text())
    report = {'sample_sha256': mask_study.sha256(mask_study.SAMPLE), 'untrained': mask_study.summarize(sample, base),
              'fine_tuned': adopted['adapter_arm'], 'fine_tuned_adapter': adopted['adapter']}
    if report['untrained']['recorded'] < mask_study.MIN_RECORDED * len(sample):
        raise SystemExit(f"only {report['untrained']['recorded']} of {len(sample)} rows recorded")
    picked = mask_study.best_block(sample, base)
    if picked:
        report['block_shown'], trace = picked
        report['trace'] = mask_study.save_trace(mask_study.labelled(
            trace, f'Glimmer before fine-tuning, selected from {len(sample)} sentences',
            'Untrained base Glimmer, no adapter; adapter_sha256 holds the base config hash. '
            'Observed local inference; no API spend. Picked by the fixed rule.', len(sample)),
            'before-fine-tuning-trace.json')
    report['line'] = (f"untrained {report['untrained']['sentences_real_block']} of {len(sample)}, "
                      f"fine-tuned {report['fine_tuned']['sentences_real_block']} of {len(sample)}")
    (mask_study.STUDY / 'report-base.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(report['line'])


if __name__ == '__main__':
    main()
