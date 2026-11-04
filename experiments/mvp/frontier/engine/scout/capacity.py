"""Optimistic cell counts for selecting a page-construction family."""


def family_capacity(model, *, deck_cap=None):
    closed = getattr(model, 'closed_hold', ())
    return sum((min(len(page.slots), deck_cap) if page.deck and deck_cap is not None
                else len(page.slots)) for page in model.pages
               if page.kind == '20' and (page.deck or page.cover not in closed))


def minimum_deck_cap(model):
    """Smallest cap fitting cell demand in uncapped geometry, not a proof."""
    need = sum(float(model.T.units[b]) for b in model.boxes)
    maximum = max((len(page.slots) for page in model.pages
                   if page.deck and page.kind == '20'), default=0)
    for cap in range(maximum+1):
        if family_capacity(model, deck_cap=cap)+1e-9 >= need:
            return cap
    # Keeping the whole physical deck cannot introduce an artificial shortage.
    return maximum
