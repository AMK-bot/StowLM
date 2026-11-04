# Provenance: adapted from lab/c2/c2-layout/pages_cached.py and pages.py; RMP from lab/c2/c2-cgscale/rmp_method.py.
"""Page construction options shared by the fast and original pricing engines.

Mixed patterns and per-bay sight can later extend the model factory without
changing the layout queue. Certificates refer only to one append-only pool.
"""
from .pages_fast import Model as FastModel
from .pages import Model as OriginalModel
from .rmp import restricted_master


class LayoutPages:
    def __init__(self, problem, *, unary_cache=True, asymmetric_deck=True,
                 restricted_master=True, aggregation=True, **kwargs):
        self.unary_cache = unary_cache
        self.asymmetric_deck = asymmetric_deck
        self.use_restricted_master = restricted_master
        self.aggregate_tiers = aggregation
        self.page_cuts = []
        super().__init__(problem, **kwargs)
        if not hasattr(self, 'seed_chosen'):
            self.seed_chosen = None

    def port_of(self, page):
        port = self.layout.get(page.cover)
        if page.deck and self.layout_deck == 'no_restow':
            if getattr(self, 'asymmetric_deck', True):
                return port if port is not None and port < max(self.ports) else None
            return port
        return port if self.layout_deck or not page.deck else None

    def _static(self):
        # Cache belongs to one immutable problem, and every model owns its copy.
        name = '_engine_layout_unary_fast' if isinstance(self, FastModel) else '_engine_layout_unary_original'
        use_cache = getattr(self, 'unary_cache', True)
        cached = getattr(self.p, name, None) if use_cache else None
        self.unary_cache_hit = cached is not None
        if cached is None:
            cached = super()._static()
            if use_cache:
                setattr(self.p, name, cached.copy())
        return cached.copy()

    def generate(self, *, active_boxes, active_stacks, deadline, phase=1, **kwargs):
        root = super().generate(active_boxes=active_boxes, active_stacks=active_stacks,
                                deadline=deadline, phase=phase, **kwargs)
        if (getattr(self, 'use_restricted_master', True) and phase == 1
                and set(active_boxes) == set(self.boxes)
                and set(active_stacks) == set(self.stacks)
                and root['uncovered'] <= 1e-7 and self.seed_chosen is None):
            chosen, record = restricted_master(self, root['x'], deadline=deadline,
                                               cuts=getattr(self, 'page_cuts', ()))
            if not hasattr(self, 'rmp_history'):
                self.rmp_history = []
            self.rmp_history.append(record)
            if chosen is not None:
                self.seed_chosen = chosen
        return root

    def respects_cuts(self, chosen):
        selected = set(chosen)
        return all(len(selected.intersection(cut['columns'])) <= cut['rhs']
                   for cut in self.page_cuts if cut.get('certified'))

    def dive(self, **kwargs):
        if self.seed_chosen is not None and not self.respects_cuts(self.seed_chosen):
            self.seed_chosen = None
        # The old dive has no seed shortcut. Validate it by the same gates used
        # by the fast implementation before returning a restricted-master seed.
        if not isinstance(self, FastModel) and self.seed_chosen is not None:
            boxes, stacks = set(), set()
            valid = True
            for index in self.seed_chosen:
                column = self.columns[index]
                page = self.pages[column.page]
                if (index in self.banned or boxes.intersection(column.boxes)
                        or stacks.intersection(page.stacks)
                        or not self.legal(page, column.boxes)
                        or not self.lash_ok(page, column.boxes)):
                    valid = False
                    break
                boxes.update(column.boxes)
                stacks.update(page.stacks)
            if valid and boxes == set(self.boxes):
                return list(self.seed_chosen), dict(reason='restricted master incumbent', left=0)
            self.seed_chosen = None
        chosen, row = super().dive(**kwargs)
        if chosen is not None and not self.respects_cuts(chosen):
            return None, dict(row, reason='certified securing cut', left=len(self.boxes))
        return chosen, row


class Model(LayoutPages, FastModel):
    pass


class SlowModel(LayoutPages, OriginalModel):
    pass
