"""Reuse the exact state-free unary table between layout trials on one problem."""
from pages import Model as BaseModel


class Model(BaseModel):
    def _static(self):
        # The inherited filter depends only on the fixed instance, empty arrival
        # and problem.engine.T, not the layout, securing oracle or search state.
        # Keep a private copy so later instance-local mutations cannot leak.
        cached = getattr(self.p, '_c2_layout_unary', None)
        if cached is None:
            cached = super()._static()
            self.p._c2_layout_unary = cached.copy()
        return cached.copy()
