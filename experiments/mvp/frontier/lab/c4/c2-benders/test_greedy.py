from time import monotonic
from types import SimpleNamespace as NS

from greedy import first_fit


def test_prefix_securing_guard_prevents_greedy_overload_before_final_column():
    t = NS(units=[2]*4, mass=[1.]*4, share=[1.]*4, height=[2.6]*4,
           box_ids=list('abcd'), stack_base_z=[0.]*4)
    m = NS(T=t, pages=[NS(kind='long', stacks=(0, 1), slots=(0, 1, 2, 3),
                         limit_kg=4., deck=True),
                       NS(kind='long', stacks=(2, 3), slots=(4, 5, 6, 7),
                         limit_kg=4., deck=True)],
           order=list(range(4)), special=set(), heavy=[False]*4,
           deadline=monotonic()+3., banned=set(), pool={}, columns=[],
           legal=lambda page, boxes: len(boxes) <= 4,
           lash_ok=lambda page, boxes: len(boxes) <= 2)
    def add(k, boxes):
        key = k, tuple(boxes)
        if key not in m.pool:
            m.pool[key] = len(m.columns)
            m.columns.append(NS(page=k, boxes=tuple(boxes)))
    m.add = add
    chosen = first_fit(m, set(range(4)), set(range(4)))
    assert chosen is not None
    assert sorted(len(m.columns[k].boxes) for k in chosen) == [2, 2]
    assert {b for k in chosen for b in m.columns[k].boxes} == set(range(4))


def test_rare_special_twenty_claims_its_only_stack_before_long_pages():
    def model():
        t = NS(units=[2, 1], mass=[20000., 22000.], share=[10000., 22000.],
               height=[2.6, 2.6], box_ids=['long', 'reefer20'], stack_base_z=[0.]*4)
        pages = [NS(kind='20', stacks=(0,), slots=(0,), limit_kg=30000., deck=False),
                 NS(kind='long', stacks=(0, 1), slots=(1,), limit_kg=30000., deck=False),
                 NS(kind='long', stacks=(2, 3), slots=(2,), limit_kg=30000., deck=False)]
        m = NS(T=t, pages=pages, order=[0, 1], special={1}, heavy=[True, True],
               deadline=monotonic()+3., banned=set(), pool={}, columns=[],
               static={(0, 0): False, (0, 1): True, (0, 2): True,
                       (1, 0): True, (1, 1): False, (1, 2): False},
               legal=lambda page, boxes: len(boxes) <= 1, lash_ok=lambda page, boxes: True)
        def add(k, boxes):
            key = k, tuple(boxes)
            if key not in m.pool:
                m.pool[key] = len(m.columns)
                m.columns.append(NS(page=k, boxes=tuple(boxes)))
        m.add = add
        return m
    naive = model()
    assert first_fit(naive, {0, 1}, set(range(4))) is None
    protected = model()
    chosen = first_fit(protected, {0, 1}, set(range(4)),
                       special20_first=True, rarity_first=True)
    assert chosen is not None
    assert {protected.columns[k].page for k in chosen} == {0, 2}
    reverse = model()
    assert first_fit(reverse, {0, 1}, set(range(4)), kind_order=('20', 'long')) is not None
