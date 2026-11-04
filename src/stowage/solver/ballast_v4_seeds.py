"""Finite trim proposals; these recipes never certify physical feasibility.

The 3-by-3 grid exactly follows physics/probe_trimseed.py. Pressing an aft
tank and emptying a forward tank moves weight aft; full/empty endpoints also
avoid intermediate free surfaces. The unchanged checker decides whether any
such proposal is legal (IMO MSC.267(85), Part B 3.1).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BallastSeed:
    label: str
    tank_masses_kg: dict[str, float]


def trim_seed_recipes(instance) -> tuple[BallastSeed, ...]:
    """Arrival fills, then nine trim proposals in the probe's exact order.

    Only adjustable tanks appear in a declaration. Fixed tanks remain under
    the ordinary checker's ownership. Missing adjustable arrival fills mean
    empty, just as in the checker; no replay state supplies the arrival base.
    Each matched tank uses its own density/capacity, without averaging pairs.
    """
    tanks = {tank.id: tank for tank in instance.vessel.tanks if tank.adjustable}
    base = {key: value for key, value in instance.onboard.tank_masses_kg.items()
            if key in tanks}
    seeds = [BallastSeed('inherited', dict(base))]
    # APPROXIMATION: this finite 3-by-3 search grid is incomplete. It changes
    # proposal ordering only; every accepted proposal needs the full checker.
    for aft in (('APT',), ('APT', 'WING-A'), ('WING-A',)):
        for forward in ((), ('FPT', 'FWD-DEEP'), ('FPT', 'FWD-DEEP', 'DB-F')):
            masses = dict(base)
            for prefix in aft:
                for tank_id, tank in tanks.items():
                    if tank_id.startswith(prefix):
                        masses[tank_id] = tank.capacity_m3 * tank.content_density_kg_m3
            for prefix in forward:
                for tank_id in tanks:
                    if tank_id.startswith(prefix):
                        masses[tank_id] = 0.
            label = f"fill{'+'.join(aft)}|empty{'+'.join(forward) or '-'}"
            seeds.append(BallastSeed(label, masses))
    return tuple(seeds)
