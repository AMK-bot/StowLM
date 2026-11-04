"""Cheap securing telemetry; no legality queries or force evaluations."""


def securing_decode_metrics(decoders):
    """Aggregate each shared physical screen once across samples/backtracking.

    ``securing_calls`` is the existing screen ``calls`` counter (evaluations
    requested, including cache hits), not a count of decoded tokens. Hold-only
    prefixes can have zero calls; ``securing_active`` separately reports whether
    all present screens have usable bounds. Unknown attributes remain visible
    through the unavailable/fail_open counters.
    """
    totals = dict(securing_calls=0, evaluations=0, unavailable=0, fail_open=0,
                  securing_screens=0, securing_active=False, securing_metrics_complete=True)
    scopes = {}
    for decoder in decoders or ():
        engine = getattr(decoder, 'engine', None)
        screen = getattr(engine, 'securing_screen', None)
        query = getattr(engine, 'securing_metrics', None)
        if screen is not None and callable(query):
            values = query()
        else:
            # Pool/proxy decoders can transport the additive diagnostics API.
            # Missing scope identity cannot safely be summed across siblings.
            diagnostics = getattr(decoder, 'diagnostics', None)
            record = diagnostics() if callable(diagnostics) else None
            screens = record.get('screens') if isinstance(record, dict) else None
            if isinstance(screens, dict) and 'securing_screen' not in screens:
                continue  # Explicit diagnostic snapshot says the layer is off.
            from stowage.feasibility._engine import IncrementalEngine
            if type(engine) is IncrementalEngine:
                continue  # Known raw production engine; no securing adapter.
            values = screens.get('securing_screen') if isinstance(screens, dict) else None
            if not values or not values.get('metrics_scope'):
                totals['securing_metrics_complete'] = False
                continue
        scope = values.get('metrics_scope')
        required = ('securing_calls', 'evaluations', 'unavailable', 'fail_open', 'securing_active')
        if not scope or any(key not in values for key in required):
            totals['securing_metrics_complete'] = False
            continue
        # A sibling can finish earlier and transport an older cumulative
        # snapshot. Fold monotone counters with max, not first-seen selection;
        # decoder order must never affect measured work for a shared screen.
        previous = scopes.setdefault(scope, dict(securing_calls=0, evaluations=0,
            unavailable=0, fail_open=0, securing_active=True))
        previous['securing_active'] &= bool(values['securing_active'])
        for key in ('securing_calls', 'evaluations', 'unavailable', 'fail_open'):
            previous[key] = max(previous[key], values[key])
    totals['securing_screens'] = len(scopes)
    for values in scopes.values():
        for key in ('securing_calls', 'evaluations', 'unavailable', 'fail_open'):
            totals[key] += values[key]
    totals['securing_active'] = bool(scopes) and all(v['securing_active'] for v in scopes.values())
    if not totals['securing_metrics_complete']:
        totals['securing_active'] = False
        for key in ('securing_calls', 'evaluations', 'unavailable', 'fail_open'):
            totals[key] = None  # Unknown telemetry must never look like zero work.
    return totals
