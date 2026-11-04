"""Size routing is resolved before any numerical assignment imports or work."""


def assignment_active(instance, *, enabled=True):
    """Explicit tiny/small metadata wins; absent metadata falls back to >60 loads."""
    if not enabled:
        return False
    size = getattr(instance, 'size_class', None)
    size = getattr(size, 'value', size)
    if isinstance(size, str):
        size = size.lower()
    if size in ('tiny', 'small'):
        return False
    if size in ('medium', 'large', 'full'):
        return True
    return len(getattr(instance, 'load_list', ())) > 60
