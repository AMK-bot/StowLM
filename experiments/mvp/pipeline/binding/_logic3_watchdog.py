"""Row-boundary discovery of measurement-only watchdog protection."""
from .review3.watchdog import install as _install_watchdog
_instrumentation = _install_watchdog()
