"""Instance generators: load lists and onboard sets (plan 2.5, ``cargo.py``), random-yard instances and the size
ladder (plan 3.5, ``random_yard.py``), simulated-yard stage-A instances (plan 4.3, ``instances.py``), layover
instances (10.3, ``layover.py``), two-load-port instances (19.1, ``two_port.py``). All seeded; every Instance
records ``provenance`` (``stowage.frozen.current_provenance``). Home only; not frozen."""

__all__ = ["generate_simulated_instance", "GenerationReport"]


def __getattr__(name):
    """Load simulated generation lazily, including for its ``python -m`` CLI."""
    if name in __all__:
        from stowage.generate import instances
        return getattr(instances, name)
    raise AttributeError(name)
