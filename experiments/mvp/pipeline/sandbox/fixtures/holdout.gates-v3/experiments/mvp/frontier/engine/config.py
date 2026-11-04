"""Stage ablations and additive budgets; defaults retain the 0.9.0 floor."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    assignment_lp: bool = True
    lift_polish: bool = True
    peel_ladder: bool = True
    targeted_constructor: bool = True
    proof_cutoff: bool = True
    moment_pricing: bool = True
    dense_layout: bool = True
    dense_fixed_charge_lns: bool = True
    layout_diagnostics: bool = True
    capacity_deck_cap: bool = True
    post_pass_exchange: bool = True
    candidate_triage: bool = True
    fixed_stow_screen: bool = True
    unary_certificate: bool = True
    peel_seam: bool = True
    assignment_budget_s: float = 300.
    assignment_finalize_s: float = 180.
    assignment_tries: int = 2
    lift_polish_s: float = 30.
    peel_proposal_s: float = 240.
    peel_finalize_s: float = 120.
    peel_candidates: int = 2
    proof_work_limit: float = 10.
    proof_outer_s: float = 900.
    target_work_limit: float = 2.
    post_pass_exchange_s: float = 60.
    e9_fix: bool = True
    bound: bool = True
    starts: bool = True
    search: bool = True
    fusion: bool = True
    classes: bool = True
    finish: bool = True
    restow_bound: bool = True
    witness_start: bool = True
    layout_search: bool = True
    asymmetric_deck: bool = True
    oog_repair: bool = True
    unary_cache: bool = True
    medium_bound: bool = True
    fast_pricing: bool = True
    aggregation: bool = True
    restricted_master: bool = True
    cached_mask: bool = True
    diagnostic_screen: bool = True
    securing_cuts: bool = True
    ballast_lp: bool = True
    certified_bound: bool = True
    certified_start: bool = True
    arrival_pages: bool = True
    arrival_bound: bool = True
    mixed_pages: bool = True
    sight_roofs: bool = True
    oog_reservations: bool = True
    unused_covers: bool = True
    fixed_charge_lns: bool = True
    capacity_cuts: bool = True
    fixed_charge_lp: bool = True
    warm_polish: bool = True
    work_count_search: bool = True
    area_securing: bool = True
    area_permutation: bool = True
    area_securing_s: float = 120.
    lp_rounds: int = 40
    polish_rounds: int = 8
    dive_nodes: int = 80
    layout_rungs: int = 12
    lns_iterations: int = 6
    archive_size: int = 12
    bound_share: float = .15
    page_share: float = .55
    fusion_share: float = .20
    classes_share: float = .10

    def __post_init__(self):
        if min(self.assignment_budget_s, self.assignment_finalize_s, self.lift_polish_s,
               self.peel_proposal_s, self.peel_finalize_s,
               self.proof_work_limit, self.proof_outer_s,
               self.target_work_limit, self.post_pass_exchange_s) < 0:
            raise ValueError("new work limits must be nonnegative")
        if self.assignment_tries < 1 or self.peel_candidates < 1:
            raise ValueError('assignment and peel candidate limits must be positive')
        if self.archive_size < 2:
            raise ValueError('archive_size must be at least two')
        if self.area_securing_s < 0 or any(value < 1 for value in
                (self.lp_rounds, self.polish_rounds, self.dive_nodes,
                 self.layout_rungs, self.lns_iterations)):
            raise ValueError('positive work limits and nonnegative area search budget required')
        shares = (self.bound_share, self.page_share, self.fusion_share, self.classes_share)
        if any(not 0 <= share <= 1 for share in shares) or sum(shares) > 1.000000001:
            raise ValueError('nonnegative stage shares must sum to at most one')
