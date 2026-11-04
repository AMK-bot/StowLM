"""Explicit checker support inventory for IMDG 42-24 segregation provisions.

Source: IMO MSC.556(108), 7.2.8 (SG) and 7.2.5.2 (SGG), transcribed in
``data/segregation_codes.json``. A source definition alone is not an assessor.
``fail_closed`` means no native assessment: the existing explicit external
``verified_segregation_codes`` route is still required, including for lone DG
where the checker requests it. It is not an automatic exemption.

Group rules use positive DGL/declarative membership; they do not infer chemical
composition or exhaustive nonmembership from a proper shipping name.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True)
class SegregationSupport:
    code: str
    status: Literal['implemented_class', 'implemented_conditional', 'fail_closed']
    required_facts: tuple[str, ...]
    reason: str


# These targets are consumed directly by the pair checker, rather than being a
# second documentation table that could claim support without executing it.
CLASS_SEPARATION = MappingProxyType({
    'SG17': frozenset({'5.1'}),
    'SG19': frozenset({'7'}),
    'SG25': frozenset({'2.1', '3'}),
})


def _support(code, status, facts, reason):
    return SegregationSupport(code, status, facts, reason)


_CLASS_FACTS = ('other.imdg_class', 'other.subsidiary_risks', 'ship_type',
                'CTU closed/open classification', 'actual placement geometry')
_GROUP_FACTS = ('DGL/declaration segregation-group membership of the other entry',
                'ship_type', 'CTU closed/open classification', 'actual placement geometry')

SEGREGATION_SUPPORT = MappingProxyType({
    **{code: _support(code, 'implemented_class', _CLASS_FACTS,
        f'Separated from class(es) {", ".join(sorted(targets))}; level 2 via the containership table.')
       for code, targets in CLASS_SEPARATION.items()},
    **{code: _support(code, 'implemented_class',
        ('other.imdg_class', 'ship_type', 'CTU closed/open classification', 'actual placement geometry'),
        f'Apply the general segregation table as class {surrogate}, then its ship-table level.')
       for code, surrogate in (('SG5', '3'), ('SG6', '5.1'))},
    'SG14': _support('SG14', 'implemented_class',
        ('other.imdg_class with class-1 compatibility group', 'actual placement geometry'),
        'Separated from class 1 except 1.4S; class-1 general compatibility remains fail-closed.'),
    'SG26': _support('SG26', 'implemented_class',
        ('other.imdg_class', 'both placements on deck', 'actual CTU athwartships envelopes'),
        'Additional containership clearance: two container spaces athwartships from classes 2.1 and 3; '
        'does not replace the general level or SG25.'),
    **{code: _support(code, 'implemented_conditional', _GROUP_FACTS,
        f'Positive declared/DGL {group} membership triggers level 2; no membership inferred from names.')
       for code, group in (('SG35', 'SGG1'), ('SG36', 'SGG18'), ('SG38', 'SGG2'),
                            ('SG49', 'SGG6'), ('SG60', 'SGG16'))},
    'SG46': _support('SG46', 'implemented_conditional',
        ('other.un_number', 'actual placement geometry'),
        'Other entry UN1017 identifies chlorine and triggers level 2.'),
    **{code: _support(code, 'implemented_conditional', ('DGL/declaration segregation_codes',),
        'Explicit segregation-group membership marker consumed by applicable group rules; '
        'the marker alone adds no distance and proves no chemical nonmembership.')
       for code in ('SGG1', 'SGG2', 'SGG6', 'SGG8', 'SGG18')},
    **{code: _support(code, 'fail_closed', ('verified_segregation_codes', fact), reason)
       for code, fact, reason in (
        ('SG27', 'other explosive chlorate/perchlorate composition',
         'Explosive composition and its conditional segregation are not assessed natively.'),
        ('SG31', 'other lead/compound group membership',
         'The away-from SGG9 assessment is not implemented.'),
        ('SG34', 'ammonium composition and chlorate/perchlorate/explosive contents',
         'The composition-dependent chlorate/perchlorate requirement is not assessed natively.'),
        ('SG50', 'foodstuff inventory and applicable ship-specific foodstuff segregation',
         'Foodstuff segregation cross-references are not assessed natively.'),
        ('SG53', 'combustible material inventory inside the same CTU',
         'Same-CTU combustible contents are not assessed natively; external verification is required.'),
        ('SG72', 'complete applicable 7.2.6.3 compatibility facts',
         'The cross-referenced 7.2.6.3 compatibility tables are not assessed natively.'),
       )},
})


def classify_segregation_code(code: str) -> SegregationSupport:
    """Unknown codes stay unimplemented; prefix recognition grants no support."""
    return SEGREGATION_SUPPORT.get(code) or _support(code, 'fail_closed',
        ('verified_segregation_codes', 'resolved applicable segregation provision'),
        'No native assessment is classified for this code; an unknown group marker cannot grant clearance.')
