"""Exact departure-only securing supplement to the provisional fast screen."""
from __future__ import annotations

from time import perf_counter

from stowage.checker.lashing import check_lashing
from stowage.checker.perf import evaluation_cache
from stowage.model import Instance, ShipState

SOURCE = "stowage.checker.lashing.check_lashing"
NOTE = (
    "Departure securing screened with the checker's actual loading condition and synthetic "
    "securing arrangement. Mid-voyage, arrival and all other full-checker parts remain pending."
)


def screen_securing(instance: Instance | dict, ship: ShipState | dict) -> tuple[list[dict], dict]:
    """Return original checker findings and a fail-closed departure screen.

    Call only after the static, rough-physics and crane-replay screens clear.
    ``metadata['passed']`` must also gate the provisional screen: an unexpected
    exception supplies no fabricated checker violation and cannot clear it.
    """
    started = perf_counter()
    metadata = {"phase": "departure", "source": SOURCE,
                "label": "Departure securing screen; full check running", "note": NOTE,
                "ran": False, "passed": False, "error": None, "max_fitting_ratio": None,
                "metrics": {}, "report": None, "full_check_pending": True}
    try:
        instance = Instance.model_validate(instance) if isinstance(instance, dict) else instance
        ship = ShipState.model_validate(ship) if isinstance(ship, dict) else ship
        # Same optional numerical workspace as the full checker. Reports,
        # mutable loading states and safety decisions are never cached here.
        with evaluation_cache():
            report = check_lashing(instance, ship, call_index=instance.call_index)
        broken = []
        for violation in report.violations:
            if violation.severity != "hard":
                continue
            row = violation.model_dump(mode="json")
            # Preserve every original field, adding only contract aliases and
            # a source label. No ratio, limit, message or box list is authored.
            row.update(boxes=list(violation.containers), source="departure_securing")
            broken.append(row)
        metadata.update(ran=report.ran,
                        passed=bool(report.ran and not report.error and not broken),
                        error=report.error, max_fitting_ratio=report.metrics.get("max_fitting_ratio"),
                        metrics=dict(report.metrics), report=report.model_dump(mode="json"))
    except Exception as error:
        broken = []
        metadata["error"] = f"Departure securing screen unavailable: {type(error).__name__}: {error}"
    metadata["wall_s"] = perf_counter()-started
    return broken, metadata
