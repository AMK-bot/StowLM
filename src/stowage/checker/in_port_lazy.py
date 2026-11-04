"""D74 exact lazy refinement of the existing nearest-ideal HEEL sweep.

Capacity bounds are OUTER bounds, never claims of physical admissibility. A
passing ideal is an INNER singleton certificate. Only binding states need their
full original interval computed; numerical errors in requested proofs propagate.
Unused interval endpoints are deliberately not evaluated, rather than evaluated
and discarded. All base/window physical checks remain the caller's obligation.

Proof (DESIGN.md B.1 and in_port_programme.heel_path): each original interval is
inside its capacity outer bound and contains its certified ideal. The sweep
selects the unique path nearest the first ideal, then nearest the second given
the first, and so on: a lexicographic nearest-ideal path. If its outer solution
uses only certified ideals at unknown states, that solution also belongs to all
original intervals. Since the original feasible paths form a subset of the
outer feasible paths, no original path can improve that solution. Thus it is
the SAME path the eager sweep selects, not merely another passing witness.
An infeasible outer problem proves the original infeasible immediately. Otherwise
refine every unknown state whose chosen point is not its certified ideal, and
repeat. At least one state becomes exact each round, so the algorithm terminates
in at most n refinement rounds; worst case it computes every original interval.

The inclusion premise concerns physical bands/certificates. It does not claim
that a numerical error an unnecessary eager endpoint solve might encounter was
computed or ruled out. Nonfinite base states and all requested proof errors
still fail internally. Exact chosen-path checks remain mandatory for deviations.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite

from stowage.checker.in_port_programme import heel_path


@dataclass(frozen=True)
class LazyHeelPathResult:
    path: object
    intervals: tuple
    refined_indices: tuple[int,...]
    unrefined_indices: tuple[int,...]
    refinement_rounds: int


def _interval(value):
    if value is None:
        return None
    if hasattr(value,'lo_kg') and hasattr(value,'hi_kg'):
        lo,hi=value.lo_kg,value.hi_kg
    else:
        try:
            lo,hi=value
        except (TypeError,ValueError) as error:
            raise ValueError('invalid HEEL outer/refined interval') from error
    if not all(isfinite(number) for number in (lo,hi)):
        raise ValueError('nonfinite HEEL outer/refined interval')
    return (float(lo),float(hi)) if lo<=hi else None


def lazy_heel_path(outer_intervals,ideal_splits_kg,*,certified_ideals,refine,
                   exact_indices=(),arrival_split_kg,parameters,call_index):
    """Refine only binding unknown states; return the eager sweep's same path.

    ``refine(indices)`` returns a mapping for every requested state, containing
    its ORIGINAL certified interval or None for a physically empty interval.
    It must not replace an endpoint numerical failure with an empty interval.
    ``exact_indices`` identifies supplied full intervals (e.g. window endpoint
    intersections); other supplied ranges are outer bounds only. A certified
    ideal must be known to lie in the original interval under the policy above.
    """
    bands=[_interval(value) for value in outer_intervals]
    ideals=tuple(ideal_splits_kg)
    certificates=tuple(certified_ideals)
    count=len(bands)
    exact=set(exact_indices)
    if len(ideals)!=count or len(certificates)!=count or not count:
        raise ValueError('one HEEL ideal and certificate are required per interval')
    if any(not isinstance(index,int) or isinstance(index,bool) or not 0<=index<count for index in exact):
        raise ValueError('invalid exact HEEL interval index')
    if any(not isinstance(value,bool) for value in certificates):
        raise ValueError('HEEL ideal certificates must be explicit booleans')
    for index,(band,ideal) in enumerate(zip(bands,ideals,strict=True)):
        if not isfinite(ideal) or ideal<0:
            raise ValueError('nonfinite or negative ideal HEEL split')
        if index not in exact and (band is None or band[0]<0 or not band[0]<=ideal<=band[1]):
            raise ValueError('HEEL outer bound must contain its physical ideal')
    original_outer=tuple(bands)
    requested=set()
    rounds=0

    def refine_indices(indices):
        nonlocal rounds
        indices=tuple(sorted(indices))
        if not indices:
            return
        supplied=refine(indices)
        if not isinstance(supplied,Mapping) or set(supplied)!=set(indices):
            raise ValueError('HEEL refinement must return every requested state exactly once')
        for index in indices:
            band=_interval(supplied[index])
            outer=original_outer[index]
            if band is not None and (band[0]<outer[0] or band[1]>outer[1]):
                raise ValueError('refined HEEL interval exceeds its proven outer bound')
            bands[index]=band
            exact.add(index)
            requested.add(index)
        rounds+=1

    refine_indices(index for index in range(count) if index not in exact and not certificates[index])
    while True:
        path=heel_path(bands,ideals,arrival_split_kg=arrival_split_kg,
                       parameters=parameters,call_index=call_index)
        if not path.feasible:
            break
        binding=[index for index in range(count) if index not in exact
                 and path.path_kg[index]!=ideals[index]]
        if not binding:
            break
        refine_indices(binding)
    return LazyHeelPathResult(path,tuple(bands),tuple(sorted(requested)),
        tuple(index for index in range(count) if index not in exact),rounds)
