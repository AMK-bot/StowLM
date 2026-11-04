# Provenance: copied from experiments/mvp/frontier/lab/c3/c2-layout/pages.py.
"""Fast C2 pages with an explicit closed-hold design and asymmetric deck ports."""
import numpy as np
from .layout_pages import Model as BaseModel
from .pages_fast import Page, Pattern


class Model(BaseModel):
    def __init__(self, problem, *, closed_hold=(), **kwargs):
        self.closed_hold = frozenset(closed_hold)
        super().__init__(problem, **kwargs)

    def _static(self):
        out = super()._static().copy()
        if self.closed_hold:
            T = self.T
            forbidden = [s for s in range(len(T.slot_ids))
                         if any(not T.stack_deck[int(j)] and T.stack_cover[int(j)] in self.closed_hold
                                for j in T.slot_stacks[s] if j >= 0)]
            out[:, forbidden] = False
        return out

    def port_of(self, page):
        port = self.layout.get(page.cover)
        if page.deck:
            if page.cover in self.closed_hold:
                return None
            if self.layout_deck == 'no_restow':
                if getattr(self, 'asymmetric_deck', True):
                    return port if port is not None and port < max(self.ports) else None
                return port
            if not self.layout_deck:
                return None
        return port

    def hold_gain(self, box):
        gain = super().hold_gain(box)
        if self.closed_hold and self.T.e6_threshold is not None and self.T.mass[box] >= self.T.e6_threshold:
            gain += 100.
        return gain
