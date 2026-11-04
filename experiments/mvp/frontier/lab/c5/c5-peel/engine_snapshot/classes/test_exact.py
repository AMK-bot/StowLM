# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-fvs/exact_tests.py.
"""Independent brute-force checks of the relocation relaxation on tiny graphs."""
from itertools import permutations
import random
import unittest

from .exact import solve_graph


def brute_force(vertices, ship_edges, stacks):
    best = None
    for order in permutations(vertices):
        rank = {v: i for i, v in enumerate(order)}
        if any(rank[a] >= rank[b] for a, b in ship_edges):
            continue
        moved = set()
        for stack in stacks:
            for i, lower in enumerate(stack):
                if lower not in rank:
                    continue
                for upper in stack[i + 1:]:
                    if upper not in rank or rank[upper] > rank[lower]:
                        moved.add(upper)
        best = len(moved) if best is None else min(best, len(moved))
    return best


class ExactTests(unittest.TestCase):
    def test_dfvs_can_delete_essential_ship_hub(self):
        # Every directed cycle contains v, so ordinary DFVS is exactly one.
        # Relocating v does not remove either of its essential ship paths.
        edges = [('b', 'v'), ('d', 'v'), ('v', 'a'), ('v', 'c')]
        result = solve_graph('abcdv', edges, [('b', 'a'), ('d', 'c'), ('v',)])
        self.assertEqual(result.lower_bound, 2)
        self.assertEqual(result.relocated, frozenset('ac'))

    def test_ship_precedence_survives_cut(self):
        # Ordinary FVS could remove B and claim one. A and C still conflict.
        result = solve_graph('ABC', [('A', 'B'), ('B', 'C')], ['ABC'])
        self.assertTrue(result.optimal)
        self.assertEqual(result.lower_bound, 2)
        self.assertTrue(result.certifies(2))
        self.assertFalse(result.certifies(2, restow_lifts=1))

    def test_nonload_blockers(self):
        result = solve_graph('AB', [], [('A', 'X', 'B', 'Y')])
        self.assertEqual(result.lower_bound, 2)
        self.assertEqual(result.mandatory_nonloads, frozenset('XY'))

    def test_yard_pairs_cannot_be_adjacent_only(self):
        result = solve_graph('ABC', [('A', 'C')], ['ABC'])
        self.assertEqual(result.lower_bound, 1)
        self.assertIn('C', result.relocated)

    def test_random_against_exhaustive_orders(self):
        rng = random.Random(41)
        vertices = tuple('ABCDE')
        for _ in range(35):
            ship_order = list(vertices)
            rng.shuffle(ship_order)
            ship_edges = [(a, b) for i, a in enumerate(ship_order)
                          for b in ship_order[i + 1:] if rng.random() < .35]
            yard = list(vertices)
            rng.shuffle(yard)
            split = rng.randrange(1, len(yard))
            stacks = [yard[:split], yard[split:]]
            result = solve_graph(vertices, ship_edges, stacks)
            self.assertTrue(result.optimal)
            self.assertEqual(result.lower_bound, brute_force(vertices, ship_edges, stacks))


if __name__ == '__main__':
    unittest.main()
