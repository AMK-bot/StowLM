"""Publish concrete replay candidates before optional search can fail."""


def publish(context, scored, *, checked=False, report=None):
    if scored is None:
        return
    name = '_engine_checked_page_candidates' if checked else '_engine_page_candidates'
    rows = context.setdefault(name, [])
    # Keep the concrete Scored, including any independently checked tank recipe.
    if not any(item is scored for item in rows):
        rows.append(scored)
        callback = context.get('publish_candidate')
        if callback is not None:
            try:
                callback(scored, checked=checked, report=report)
            except BaseException as error:
                # A caller's early-stop signal belongs to the caller, not to an
                # optional-search exception recovery guard.
                context['_engine_page_callback_error'] = error
                raise


def recover(context, error):
    if context.get('_engine_page_callback_error') is error:
        raise error
    context.setdefault('method_metadata', {}).setdefault('errors', []).append(
        f'{type(error).__name__}: {error}')
    selected = {}
    for scored in context.get('_engine_page_candidates', ()):
        selected.setdefault(tuple(scored.actions), scored)
    return [scored.actions for scored in sorted(selected.values(), key=lambda row: row.objective)]
