"""Stage ablations and additive budgets; defaults retain the 0.9.0 floor."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    e9_fix: bool = True
    bound: bool = True
    starts: bool = True
    search: bool = True
    fusion: bool = True
    classes: bool = True
    finish: bool = True
    archive_size: int = 12
    bound_share: float = .15
    page_share: float = .55
    fusion_share: float = .20
    classes_share: float = .10

    def __post_init__(self):
        if self.archive_size < 2:
            raise ValueError('archive_size must be at least two')
        shares = (self.bound_share, self.page_share, self.fusion_share, self.classes_share)
        if any(not 0 <= share <= 1 for share in shares) or sum(shares) > 1.000000001:
            raise ValueError('nonnegative stage shares must sum to at most one')
