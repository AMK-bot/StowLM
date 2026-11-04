"""Factory aliases in already-imported solvers cannot bypass mask observation."""
from stowage.feasibility import make_feasibility_engine as imported_factory
from eval.mask_observation import observe_mask_diagnostics


def test_factory_imported_before_observer_is_captured_below_alias_boundary(empty_ship_instance, monkeypatch):
    from stowage.feasibility._engine import IncrementalEngine
    def initialize(self, instance, space, config):
        self.instance = instance
        self.provenance = instance.provenance
        self.diagnostics = {'global_screen': 'unavailable: pre-imported solver alias'}
    monkeypatch.setattr(IncrementalEngine, '__init__', initialize)
    with observe_mask_diagnostics() as observations:
        engine = imported_factory(empty_ship_instance, object())
        assert isinstance(engine, IncrementalEngine)
    assert any(d['global_screen'] == 'unavailable: pre-imported solver alias' for d in observations)
    assert IncrementalEngine.__init__ is initialize
