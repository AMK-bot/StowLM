"""Build Studio's local measured evidence and explicitly assumed pilot projection.

Reads published artifacts only; never imports a planner, runs inference or opens maps.
Run: UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m app.studio_data
"""
from __future__ import annotations

import json
import argparse
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'app/static/studio-data'
HEADLINE = 'docs/headline.md'
REPORT = 'experiments/mvp/pipeline/sealed/runs/final-3/report.json'
EXT = 'experiments/mvp/pipeline/sealed/measurements/ext-1/FIGURES.json'
ADOPTION = 'experiments/mvp/reader/v3/results-final/final-adoption-d266.json'
SPARK = 'experiments/mvp/reader/v3/sealed/spark-summary.json'
PARSER = 'experiments/mvp/reader/v3/parser/sealed-v3-report.json'
MASK = 'experiments/mvp/reader/v3/results-final/mask-study/report-base.json'
OOD = 'experiments/mvp/reader/v3/ood-1/results/scores.json'
OOD_REPORT = 'experiments/mvp/reader/v3/ood-1/REPORT.md'
IMPACT = 'experiments/impact-estimate/fixtures/impact-v6.json'
IMPACT_DOC = 'docs/impact.md'
SCRIPT = 'experiments/mvp/video/script-v6.md'


def read_source(source: dict[str, Any], root: Path = ROOT) -> Any:
    """Resolve a recorded source: RFC6901 JSON pointer or an explicit prose regex.

    Prose fields use ``regex:<pattern>``; group defaults to 1. Optional ``clean``
    removes Markdown emphasis and rejoins prose line wraps, without changing digits.
    This same resolver is available to independent artifact checks.
    """
    path = root / source['path']
    field = source['field']
    if field.startswith('regex:'):
        match = re.search(field[6:], path.read_text(), re.MULTILINE | re.DOTALL)
        if match is None:
            raise ValueError(f'Prose source no longer matches: {source}')
        value = match.group(source.get('group', 1))
        if source.get('clean'):
            value = re.sub(r'\s+', ' ', value.replace('**', '').replace('`', '')).strip()
        if source.get('type') == 'float':
            value = float(value.replace(',', ''))
        return value
    value = json.loads(path.read_text())
    if field:
        for token in field.lstrip('/').split('/'):
            key = token.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def src(path: str, field: str) -> dict:
    return {'path': path, 'field': field}


def prose(path: str, pattern: str) -> tuple[str, dict]:
    source = {'path': path, 'field': 'regex:' + pattern, 'clean': True}
    return read_source(source), source


def figure(path: str, field: str, label: str, *, n_field=None,
           interval_field=None, unit='count', **extra) -> dict:
    source = src(path, field)
    row = {'value': read_source(source), 'n': None, 'interval': None,
           'label': label, 'source': source, 'unit': unit}
    if n_field:
        row['n_source'] = src(path, n_field)
        row['n'] = read_source(row['n_source'])
    if interval_field:
        row['interval_source'] = src(path, interval_field)
        row['interval'] = read_source(row['interval_source'])
    row.update(extra)
    return row


def build_evidence() -> dict:
    original = read_source(src(REPORT, '/label'))
    extension = read_source(src(EXT, '/primary/label/value'))
    baseline, baseline_source = prose(HEADLINE, r'\*\*Two-step:\*\* "([^"]+)"')
    partial = read_source(src(EXT, '/original_partial_run_disclosure/value'))
    partial_short, partial_short_source = prose(HEADLINE, r'Film-card short form: "([^"]+)"')
    envelope, envelope_source = prose('experiments/mvp/charts/pipeline_charts.py',
        r'ENVELOPE = \(\s*"(.*?)"\s*\)')
    # The chart stores this caption as adjacent Python string literals.
    envelope = re.sub(r'"\s*"', '', envelope)
    envelope = re.sub(r'\s+', ' ', envelope).strip()
    labels = {'original': original, 'extension': extension, 'baseline': baseline,
              'partial_run': partial, 'partial_run_short': partial_short}

    def planning(key, arm, title):
        base = f'/{key}/arms/{arm}'
        return figure(EXT, base + '/numerator/value', title,
                      n_field=base + '/denominator/value', interval_field=base + '/exact95/value')

    def binding(key, title):
        base = f'/{key}/binding'
        return figure(EXT, base + '/numerator/value', title,
                      n_field=base + '/denominator/value', interval_field=base + '/exact95/value')

    voyages_original = [planning('separate_original_3_1', 'pipeline', 'Pipeline'),
                         planning('separate_original_3_1', 'twostep', 'Two-step')]
    voyages_extension = [planning('primary', 'pipeline', 'Pipeline'),
                          planning('primary', 'twostep', 'Two-step')]
    reader_series = [figure(ADOPTION, f'/sealed_128/{arm}/exact_match_count', title,
                           n_field=f'/sealed_128/{arm}/count')
                     for arm, title in [('swa-last-5', 'Adopted v3 Reader'),
                                        ('untrained', 'Without fine-tune'),
                                        ('adapter-v2', 'Earlier adapter v2')]]
    reader_series.extend([figure(SPARK, '/exact_match_count', 'Muse Spark reference', n_field='/count'),
                          figure(PARSER, '/exact_match_count', 'No-model parser', n_field='/count')])
    reader_gate, gate_source = prose(HEADLINE, r'(It passed the 90 % gate and missed two stricter ones \(.*?\)): say so')
    reader_context, reader_context_source = prose(HEADLINE, r'Reader figures \(D266\): (v3, adopted, .*?); untrained')
    mask_show, mask_show_source = prose(HEADLINE, r'Always beside it: "(almost all were the output format[^"]+)"')
    mask_limit, mask_limit_source = prose(HEADLINE, r'and "(both of the 2 were sentences[^"]+)"')
    instruction_show, instruction_source = prose(HEADLINE, r'Instruction claim \(D258, D291\): "([^"]+)"')
    instruction_extension, instruction_extension_source = prose(HEADLINE, r'(With one instruction, 18 of 22 voyages[^"\n]+)"')
    ood_label, ood_label_source = prose(OOD_REPORT, r'\*\*Label: ([^*]+)\*\*')
    ood_limits, ood_limits_source = prose(OOD_REPORT, r'\*\*Its weak side is\s*refusing:\*\* (.*?quote\.)')
    ood_show, ood_show_source = prose(OOD_REPORT, r'(On new wording from one other model,.*?exactly, against 18 untrained and 44 for the parser),')
    cards = [
        {'id': 'voyages', 'title': 'Held-out test voyages', 'label': original,
         'show': 'The planning step, from a clean load list. Full checker passes inside the design envelope.',
         'does_not': 'No like-for-like lift comparison is available. Navis and human performance aren\'t public.',
         'series': voyages_original, 'variants': {'original': voyages_original, 'extension': voyages_extension}},
        {'id': 'reader', 'title': 'Reader on a locked test', 'label': 'Sealed v3 · locked before tuning; scored once',
         'show': 'Same model, same laptop, with and without our fine-tune; same mask. ' + reader_context + '.',
         'does_not': reader_gate + '. Fixed original Spark reference; not rerun under the updated decoder.',
         'series': reader_series,
         'supplemental': [figure(ADOPTION, f'/sealed_128/{arm}/false_acceptance_count', title,
                                 n_field=f'/sealed_128/{arm}/loosening_count')
                          for arm, title in [('swa-last-5', 'v3 loosening accepted'), ('adapter-v2', 'v2 loosening accepted')]]},
        {'id': 'mask', 'title': 'Decode-mask overrides', 'label': 'Development · validation sentences',
         'show': mask_show + '; ' + mask_limit, 'does_not': mask_limit,
         'series': [figure(MASK, f'/{arm}/sentences_top_changed', title, n_field=f'/{arm}/sentences')
                    for arm, title in [('untrained', 'Before fine-tuning'), ('fine_tuned', 'After fine-tuning')]]},
        {'id': 'binding', 'title': 'Instruction binding', 'label': original,
         'show': instruction_show,
         'variant_shows': {'original': instruction_show, 'extension': instruction_extension},
         'does_not': 'Confirmation in this run is exact expected-rule match, not a human planner. These are templated sentences.',
         'series': [binding('separate_original_3_1', 'Checked plan obeyed the rule')],
         'variants': {'original': [binding('separate_original_3_1', 'Checked plan obeyed the rule')],
                      'extension': [binding('primary', 'Checked plan obeyed the rule')]},
         'supplemental': [figure(EXT, '/separate_original_3_1/already_obeyed/value', 'First plan already obeyed it',
                                 n_field='/separate_original_3_1/arms/instruction/denominator/value'),
                          figure(EXT, '/primary/already_obeyed/value', 'Extension: first plan already obeyed it',
                                 n_field='/primary/arms/instruction/denominator/value')]},
        {'id': 'ood', 'title': 'Reader on new wording', 'label': ood_label,
         'show': ood_show + '.', 'does_not': ood_limits + ' One author model; not real planner text. The blind second labeller was another Claude model, not a domain expert.',
         'series': [figure(OOD, f'/arms/{arm}/exact/count', title,
                           n_field=f'/arms/{arm}/exact/of', interval_field=f'/arms/{arm}/exact/ci95')
                    for arm, title in [('glimmer-adopted', 'Adopted Reader'), ('glimmer-untrained', 'Without fine-tune'), ('fair', 'Fair hand-written parser')]],
         'supplemental': [figure(OOD, '/arms/glimmer-adopted/wrong_rule_from_must_refuse/count', 'Rules made from must-refuse wording',
                                 n_field='/arms/glimmer-adopted/wrong_rule_from_must_refuse/of',
                                 interval_field='/arms/glimmer-adopted/wrong_rule_from_must_refuse/ci95')]},
    ]
    return {'schema_version': 'stowlm.studio.evidence/1',
            'scope': 'Synthetic ships and cargo, simulated port', 'envelope': envelope,
            'labels': labels, 'cards': cards,
            'text_sources': {'original': src(REPORT, '/label'),
                            'extension': src(EXT, '/primary/label/value'),
                            'partial_run': src(EXT, '/original_partial_run_disclosure/value'),
                            'partial_run_short': partial_short_source,
                            'baseline': baseline_source, 'envelope': envelope_source,
                            'reader_gate': gate_source, 'reader_context': reader_context_source,
                            'mask_show': mask_show_source, 'mask_limit': mask_limit_source,
                            'instruction_show': instruction_source,
                            'instruction_extension': instruction_extension_source, 'ood_label': ood_label_source,
                            'ood_limits': ood_limits_source, 'ood_show': ood_show_source},
            'chart_sources': ['experiments/mvp/charts/' + name for name in
                              ['pipeline-passes.svg', 'reader-exact-match.svg', 'decode-mask-overrides.svg',
                               'instruction-binding.svg', 'ext1-passes.svg']]}


def build_impact() -> dict:
    original = json.loads((ROOT / IMPACT).read_text())
    pilot = {}
    for case, row in original['inputs']['pilot'].items():
        inputs = {key: figure(IMPACT, f'/inputs/pilot/{case}/{key}/value', key.replace('_', ' '),
                              unit=field['unit'], status=field['status'], note=field['note'])
                  for key, field in row.items()}
        outputs = {period: {key: figure(IMPACT, f'/pilot/{case}/{period}/{key}', key.replace('_', ' '),
                                       unit='USD' if key.endswith('_usd') else 'hours' if 'hours' in key else 'lifts')
                            for key in original['pilot'][case][period]}
                   for period in ('per_call', 'annual')}
        pilot[case] = {'inputs': inputs, 'outputs': outputs}
    context = {key: figure(IMPACT, f'/inputs/context/{key}/value', key.replace('_', ' '),
                           unit=value['unit'], status=value['status'], note=value['note'])
               for key, value in original['inputs']['context'].items()
               if key.startswith('pilot_') or key.startswith('terminal_revenue_')}
    conditions, conditions_source = prose(IMPACT_DOC, r'## What must be true\s*\*\*([^*]+)\*\*')
    who, who_source = prose(IMPACT_DOC, r'(Potential leads: feeder operators and planning teams at Laem Chabang and Bangkok; no partnership or outreach implied\.)')
    revenue, revenue_source = prose(IMPACT_DOC, r'The verified terminal-revenue range is \*\*([^*]+)\*\*')
    inputs_summary, inputs_summary_source = prose(SCRIPT, r'fine print, at least 22 px, not monospace: "(inputs: .*?) · what would have to be true:')
    extra_conditions = [prose(IMPACT_DOC, pattern) for pattern in [
        r'(Earlier departure must free usable vessel time\.)',
        r'(Box mix, one container per lift, fleet-weighted time, rates, wages and adoption need validation\.)',
        r'(Costs of compute, integration and support must be deducted in a business case; the engine is not competitive at large scale yet\.)']]
    limits, limits_source = prose(IMPACT_DOC, r'(This is a gross opportunity scenario, not an audited global loss, net savings, software revenue or a result achieved\.)')
    unmeasured, unmeasured_source = prose(IMPACT_DOC, r"Today's eligible feeder-call count is unknown, (no operational saving is measured),")
    # Current impact.md explicitly links this calculator. impact.py is archival v5
    # and disallows annual dollar scaling of its small-map measured sensitivity.
    return {'schema_version': 'stowlm.studio.impact/1', 'label': 'Projection',
            'scope': 'Synthetic ships and cargo, simulated port',
            'model': {'path': 'experiments/impact-estimate/impact_v6.py', 'function': '_per_call',
                      'annual_function': 'calculate', 'source_resolution':
                      'docs/impact.md links impact_v6.py and fixtures/impact-v6.json. impact.py is archival v5 small-map sensitivity; its measured lift estimates are not inputs to this projection.'},
            'pilot': pilot, 'context': context,
            'global': {'exposure': figure(IMPACT, '/exposure/headline_billion_usd', 'Port-call cost exposure · projection', unit='USD billions/year'),
                       'prize_mid': figure(IMPACT, '/prize/mid/total_gross_prize_usd', 'Mid-case global pool · projection', unit='USD/year'),
                       'prize_low': figure(IMPACT, '/prize/low/total_gross_prize_usd', 'Low-case global pool · projection', unit='USD/year'),
                       'terminal_revenue_check': revenue, 'input_summary': inputs_summary},
            'what_must_be_true': [conditions] + [text for text, _ in extra_conditions],
            'who_for': who,
            'limits': limits + ' ' + unmeasured.capitalize() + '.',
            'text_sources': {'what_must_be_true': conditions_source, 'who_for': who_source,
                            'terminal_revenue_check': revenue_source, 'input_summary': inputs_summary_source,
                            'extra_conditions': [source for _, source in extra_conditions],
                            'limits': limits_source, 'unmeasured': unmeasured_source}}


def write_data(output_dir: Path = OUT) -> list[Path]:
    """Write deterministic artifacts to an explicit destination.

    Tests and reviews pass a temporary directory so verification never mutates
    the checked-in Studio fixtures.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, builder in [('evidence', build_evidence), ('impact', build_impact)]:
        payload = builder()
        target = output_dir / f'{name}.json'
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--output-dir', type=Path, default=OUT,
                        help='artifact destination (default: app/static/studio-data)')
    args = parser.parse_args(argv)
    for target in write_data(args.output_dir):
        display = target.relative_to(ROOT) if target.is_relative_to(ROOT) else target
        print(f'Wrote {display}')


if __name__ == '__main__':
    main()
