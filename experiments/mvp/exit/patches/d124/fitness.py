"""D105 paired population fitness on identical fresh training maps."""
from statistics import mean
from llm.exit.metrics import _rows
from llm.exit.outcomes import checker_unavailable, outcome_counts


def population_fitness(rows_by_adapter):
    if not rows_by_adapter:
        raise ValueError('population must be nonempty')
    rows_by_adapter = {adapter: list(values) for adapter, values in rows_by_adapter.items()}
    rows = {adapter: _rows(values) for adapter, values in rows_by_adapter.items()}
    first = next(iter(rows.values()))
    if not first or any(r.keys() != first.keys() for r in rows.values()):
        raise ValueError('population must cover the same nonempty paired maps')
    unavailable_ids = {str(row.get('instance_id', row.get('instance', row.get('id', ''))))
                       for values in rows_by_adapter.values() for row in values if checker_unavailable(row)}
    # Preserve identical-map pass counts. Unavailability cannot erase a
    # competitor's verified pass or shrink the scoring denominator.
    mutual = [identity for identity in first if all(r[identity][0] for r in rows.values())]
    counts = {a: sum(r[0] for r in values.values()) for a,values in rows.items()}
    means = {a: mean(values[i][1] for i in mutual) if mutual else None for a,values in rows.items()}
    # max retains the first member on ties. Without ALL-child mutual passes,
    # there is no paired moves observation and only pass counts can rank models.
    best = max(rows, key=lambda a: (counts[a], -means[a] if mutual else 0))
    return dict(best_adapter=best, pass_counts=counts, mutual_ids=mutual, mean_moves=means,
                outcomes={a: outcome_counts(v) for a,v in rows_by_adapter.items()},
                pass_count_denominator=len(first),
                available_pair_count=len(first)-len(unavailable_ids),
                unavailable_ids=sorted(unavailable_ids),
                map_count=len(first), scope='greedy own plans on fresh training maps; not held-out quality')
