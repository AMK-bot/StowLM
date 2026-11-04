"""Language QA on the retired synthetic input; never promotes it to a demo hero."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from llm.spark.spend import Usage, calculate_cost
from .api import inputs, statement_menu
from .cache import _boxes, _write, estimate_cost, prefetch

HERE = Path(__file__).with_name('fixtures') / 'development'


def prior_checks():
    directory = HERE / 'retired-cache'
    index = json.loads((directory / 'index.json').read_text())
    return {box: [{'previous_payload_sha256': entry['th']['sha256'],
                   'check': entry['th']['audit']['checks']['thai_sense'],
                   'review_history': entry['th']['audit'].get('thai_review_history', []),
                   'prior_language_checks': entry['th']['audit'].get('prior_language_checks', [])}]
            for box, entry in index['entries'].items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spark', action='store_true')
    parser.add_argument('--sense-only', action='store_true', help='Review all current Thai facts without writing summaries')
    parser.add_argument('--screen-one', action='store_true', help='Record six real development fast screens')
    args = parser.parse_args(argv)
    values = inputs(*(json.loads((HERE / f'{name}.json').read_text())
                      for name in ('instance', 'plan', 'report')))
    boxes = _boxes(*values)
    if args.sense_only:
        from .review_recording import review_recording
        directory = HERE / 'current-cache'
        projection = review_recording(directory, *values)
        print(canonical_json(projection), flush=True)
        if not args.spark:
            return 0
        from .run_prefetch import remaining_budget_usd
        if Decimal(str(projection['estimate']['projected_cost_usd'])) > remaining_budget_usd():
            raise ValueError('Thai sense projection exceeds the shared remaining allocation')
        from llm.spark.client import SparkClient
        from .budget import task_client
        client = task_client(SparkClient(max_retries=0, timeout=60))
        result = review_recording(directory, *values, client=client)
        print(canonical_json(dict(status='fresh_development_thai_sense', result=result)), flush=True)
        return 0
    selected = boxes[:10]
    estimate = estimate_cost(*values, model_box_ids=selected)
    # Three remaining fact lists receive a fresh sense pass without summaries.
    other = [box for box in boxes if box not in selected]
    sense_bytes = sum(len(canonical_json([{'id': s['id'], 'en': s['text'], 'th': s['th']}
                    for s in statement_menu(*values, box)]).encode()) + 3000 for box in other)
    estimate['projected_cost_usd'] += float(calculate_cost(Usage(sense_bytes, len(other)*4096)))
    estimate.update(label='Projection: development language QA, not the demo hero',
                    extra_sense_calls=len(other), model_box_ids=selected,
                    native_thai_reader_reviewed=False)
    print(canonical_json(estimate), flush=True)
    if not args.spark:
        return 0
    from .run_prefetch import remaining_budget_usd
    if Decimal(str(estimate['projected_cost_usd'])) > remaining_budget_usd():
        raise ValueError('Language QA projection exceeds the shared remaining allocation')
    from llm.spark.client import SparkClient
    from .budget import task_client
    client = task_client(SparkClient(max_retries=0, timeout=45))
    directory = HERE / 'current-cache'
    alternatives = {}
    if args.screen_one:
        from .alternatives import screen_alternatives
        from .make_fixture import wait_for_capacity
        admission = wait_for_capacity()
        result = screen_alternatives(*values, selected[0])
        directory.mkdir(exist_ok=True)
        _write(directory / 'development-fast-screen.json', dict(admission=admission, data=result,
               label='Measured development fast screens; not the demo hero'))
        alternatives[selected[0]] = result
    index = prefetch(*values, directory, client=client, model_box_ids=selected,
                     alternatives_data=alternatives, prior_language_checks=prior_checks())
    from .review_recording import review_recording
    review = review_recording(directory, *values, client=client, box_ids=other)
    _write(directory / 'language-qa.json', dict(label='Development QA; retired source, not demo hero',
           binding=index['binding'], model_box_ids=selected, other_sense=review,
           prior_checks_sha256=sha256_json(prior_checks()), native_thai_reader_reviewed=False))
    print(canonical_json(dict(status='recorded_development_language_qa', boxes=len(boxes),
                              global_spend=json.loads(client.path.read_text())['used_usd'])), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
