"""Experiment-only final occupancy audit for tier and DG instructions.

CloseBay retains its loading/restowing-only contract. Tier and DG constraints
also apply to cargo remaining aboard, including boxes with no explicit move.
"""
from llm.instructions.apply import ConstrainedInstance, apply
from llm.instructions.schema import DgKeepAway, MaxTiers
from stowage.moves import apply_plan

from experiments.mvp.reader.constraints import InfeasibleInstruction


class OccupancyConstrainedInstance(ConstrainedInstance):
    def __init__(self, base):
        super().__init__(base.instance, list(base.constraints), set(base._all_boxes),
                         {box: set(cells) for box, cells in base._per_box.items()})
        # Exclude CloseBay from final-occupancy semantics, even in compositions.
        self.occupancy = ConstrainedInstance(base.instance)
        for constraint in base.constraints:
            if isinstance(constraint, (MaxTiers, DgKeepAway)):
                self.occupancy = apply(base.instance, constraint, base=self.occupancy)

    def violations(self, plan):
        out = super().violations(plan)  # Keep every intermediate destination check.
        if not self.occupancy.constraints:
            return out
        replay = apply_plan(self.instance, plan)
        if not replay.legal:
            return out + ['plan failed physical move replay']
        for placement in replay.final_state.ship.placements:
            reason = self.occupancy.forbids(placement.container_id, placement.slot)
            if reason:
                out.append(f'final {placement.container_id} at {placement.slot}: {reason}')
        return out

    def retained_violations(self):
        discharged = set(self.instance.discharge_list)
        out = []
        for placement in self.instance.onboard.placements:
            if placement.container_id not in discharged:
                reason = self.occupancy.forbids(placement.container_id, placement.slot)
                if reason:
                    out.append(f'retained {placement.container_id} at {placement.slot}: {reason}')
        return out


def strengthen_constraint(constrained, *, reject_retained=False):
    """Upgrade ordinary constraints at all entry paths; leave hatch adapters intact."""
    if isinstance(constrained, ConstrainedInstance):
        if not isinstance(constrained, OccupancyConstrainedInstance):
            constrained = OccupancyConstrainedInstance(constrained)
        if reject_retained:
            errors = constrained.retained_violations()
            if errors:
                raise InfeasibleInstruction('fixed retained cargo conflicts with instruction: ' + '; '.join(errors))
    return constrained


def apply_reader_constraint(instance, answer, *, reject_retained=False):
    return strengthen_constraint(apply(instance, answer), reject_retained=reject_retained)
