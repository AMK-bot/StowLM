"""Exact departure visibility supplement; the full voyage check stays pending."""
from __future__ import annotations

from time import perf_counter

from stowage.checker.perf import evaluation_cache
from stowage.checker.visibility import check_visibility
from stowage.model import Instance, ShipState

SOURCE = "stowage.checker.visibility.check_visibility"
NOTE = (
    "Departure visibility screened with the checker's actual loading condition and declared "
    "obstruction geometry. Mid-voyage, arrival and remaining full-checker parts are pending."
)


def screen_visibility(instance: Instance | dict, ship: ShipState | dict) -> tuple[list[dict], dict]:
    """Preserve checker findings; metadata.passed must also gate a clear screen.

    Run after the earlier screens, including departure securing, have cleared.
    Unexpected failures supply no invented checker violation and fail closed.
    """
    started = perf_counter()
    metadata = {"phase": "departure", "source": SOURCE,
                "label": "Departure visibility screen; full check running", "note": NOTE,
                "ran": False, "passed": False, "error": None,
                "blind_distance_m": None, "limit_m": None,
                "metrics": {}, "report": None, "full_check_pending": True}
    try:
        instance = Instance.model_validate(instance) if isinstance(instance, dict) else instance
        ship = ShipState.model_validate(ship) if isinstance(ship, dict) else ship
        with evaluation_cache():
            report = check_visibility(instance, ship, call_index=instance.call_index)
        broken = []
        for violation in report.violations:
            if violation.severity != "hard":
                continue
            row = violation.model_dump(mode="json")
            row.update(boxes=list(violation.containers), source="departure_visibility")
            broken.append(row)
        metadata.update(ran=report.ran,
                        passed=bool(report.ran and not report.error and not broken),
                        error=report.error,
                        blind_distance_m=report.metrics.get("blind_distance_m"),
                        limit_m=report.metrics.get("limit_m"),
                        metrics=dict(report.metrics), report=report.model_dump(mode="json"))
    except Exception as error:
        broken = []
        metadata["error"] = f"Departure visibility screen unavailable: {type(error).__name__}: {error}"
    metadata["wall_s"] = perf_counter()-started
    return broken, metadata
