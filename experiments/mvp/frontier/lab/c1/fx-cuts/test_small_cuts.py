"""Algorithm tests: exact binary optimum and surrogate decomposition."""
import itertools
import random
import unittest

from types import SimpleNamespace

from small_cuts import _energy, binary_energy, solve_binary


class BinaryTests(unittest.TestCase):
    def brute_value(self, unary, pairwise):
        return min(binary_energy(labels, unary, pairwise)
                   for labels in itertools.product((0, 1), repeat=len(unary)))

    def test_random_energies_match_exhaustive(self):
        rng = random.Random(409)
        algorithms = set()
        for n in range(1, 8):
            for _ in range(25):
                unary = [(rng.randrange(-5, 6), rng.randrange(-5, 6)) for _ in range(n)]
                pairwise = {(i, j): ((rng.randrange(-5, 6), rng.randrange(-5, 6)),
                                     (rng.randrange(-5, 6), rng.randrange(-5, 6)))
                            for i in range(n) for j in range(i + 1, n)
                            if rng.random() < .6}
                labels, algorithm = solve_binary(unary, pairwise)
                algorithms.add(algorithm)
                self.assertAlmostEqual(binary_energy(labels, unary, pairwise),
                                       self.brute_value(unary, pairwise))
        self.assertEqual(algorithms, {'min-cut', 'enumeration'})

    def test_reversible_nonsubmodular_pair_uses_cut(self):
        unary = [(0, 3), (1, 0)]
        pairwise = {(0, 1): ((3, 0), (0, 3))}
        labels, algorithm = solve_binary(unary, pairwise)
        self.assertEqual(algorithm, 'min-cut')
        self.assertEqual(binary_energy(labels, unary, pairwise), self.brute_value(unary, pairwise))

    def test_frustrated_cycle_is_not_called_a_cut(self):
        unary = [(0, 0)] * 3
        pairwise = {(0, 1): ((1, 0), (0, 1)),
                    (1, 2): ((1, 0), (0, 1)),
                    (0, 2): ((1, 0), (0, 1))}
        _, algorithm = solve_binary(unary, pairwise)
        self.assertEqual(algorithm, 'enumeration')
        with self.assertRaises(ValueError):
            solve_binary(unary, pairwise, enumeration_limit=2)

    def test_swap_energy_is_exact_pairwise_decomposition(self):
        class Static:
            def __getitem__(self, key):
                return key != (0, 1)

        table = SimpleNamespace(static=Static(), box_index={b: i for i, b in enumerate('abcde')},
                                slot_index={s: i for i, s in enumerate('ABCDE')})
        class Proxy:
            p = SimpleNamespace(engine=SimpleNamespace(T=table))

            @staticmethod
            def cost(a, sa, b, sb):
                # Deliberately symmetric, nontrivial dependence on both choices.
                return ((ord(a) + ord(b)) * (ord(sa) + ord(sb)) + ord(sa) * ord(sb)) % 13

        assignment = dict(zip('abcde', 'ABCDE'))
        pairs = [('a', 'b'), ('c', 'd')]
        unary, pairwise = _energy(Proxy(), assignment, pairs)
        deltas = []
        for labels in itertools.product((0, 1), repeat=2):
            candidate = dict(assignment)
            for (a, b), flip in zip(pairs, labels):
                if flip:
                    candidate[a], candidate[b] = candidate[b], candidate[a]
            direct = sum(Proxy.cost(a, candidate[a], b, candidate[b])
                         for a, b in itertools.combinations(candidate, 2))
            direct += sum(1e6 for b, slot in candidate.items()
                          if not table.static[table.box_index[b], table.slot_index[slot]])
            deltas.append(direct - binary_energy(labels, unary, pairwise))
        self.assertEqual(len(set(deltas)), 1)


if __name__ == '__main__':
    unittest.main()
