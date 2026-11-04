"""Exhaustive independent width checks for tiny two-attribute posets."""
from itertools import product
from types import SimpleNamespace

from graph_model import chain_cover


def verify():
    checked = 0
    for n in range(6):
        ids = [f'b{i}' for i in range(n)]
        for attrs in product(tuple(product(range(2), repeat=2)), repeat=n):
            def comparable(i, j):
                a, b = attrs[i], attrs[j]
                return (i != j and a[0] >= b[0] and a[1] >= b[1]
                        and (a != b or i < j))

            # Brute-force every subset rather than reuse matching logic.
            best = 0
            for mask in range(1 << n):
                chosen = [i for i in range(n) if mask & (1 << i)]
                if all(not comparable(i, j) and not comparable(j, i)
                       for at, i in enumerate(chosen) for j in chosen[at + 1:]):
                    best = max(best, len(chosen))
            table = SimpleNamespace(box_index={b: i for i, b in enumerate(ids)},
                                    mass=[a[1] for a in attrs],
                                    rank=[a[1] for a in attrs])
            p = SimpleNamespace(instance=SimpleNamespace(load_list=ids),
                                engine=SimpleNamespace(T=table),
                                pod=lambda b: attrs[table.box_index[b]][0])
            result = chain_cover(p)
            assert result['width'] == best, (attrs, result, best)
            for chain in result['chains']:
                indices = [table.box_index[b] for b in chain]
                assert all(comparable(i, j) for at, i in enumerate(indices)
                           for j in indices[at + 1:])
            assert len(result['antichain']) == best
            # Class and mass representations coincide on this fixture.
            assert chain_cover(p, by_class=True)['width'] == best
            checked += 1
    print(f'PASS: {checked} attribute assignments, n=0..5; brute-force widths and chain witnesses')


if __name__ == '__main__':
    verify()
