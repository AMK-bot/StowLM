"""Evidence checks shared by complete and incremental static checking."""

import math


# ISO 668:2020/Amd 1:2022, replacement Table 2, printed pp.1–2
# (PDF pp.5–6): R=36,000 kg for the modeled 20/40/45-foot Series 1 sizes.
# Public primary preview, SIST (national standards body):
# https://preview.sist.si/sist-preview/81611/d390bd2275d8473ebb13701cc77e0dfd/ISO-668-2020-Amd-1-2022.pdf
ISO668_R_KG_BY_LENGTH = {20: 36000.0, 40: 36000.0, 45: 36000.0}


def cargo_rating_violation(box, placement, emit, *, note=None):
    """Recheck plate data because assignment/model_construct bypass validators."""
    rating = box.max_gross_mass_kg
    # CSC Annex I Reg. 1(1)(b), 1(2)(a), reproduced in IMDG 42-24
    # §1.1.2.3.1, data/raw/imdg-42-24/msc556-108.pdf, PDF p.21:
    # "Maximum operating gross mass (kg and lb)" is an individual plate field.
    # A nominal ISO size is not evidence of that container's certified rating.
    if rating is None:
        # ASSUMPTION: absent plate data uses the Series 1 table envelope as a
        # hard screening fallback, per the leader's clarified legacy policy.
        # This never certifies an individual box, whose older rating may be lower.
        fallback = ISO668_R_KG_BY_LENGTH.get(box.length_ft)
        if note is not None:
            note(f'ASSUMPTION: container {box.id}: '
                 + (f'plate rating unavailable; ISO 668 R fallback applied ({fallback:g} kg); '
                    if fallback is not None else 'plate rating unavailable; no sourced ISO 668 R fallback; ')
                 + 'certified rating unverified.')
        if (fallback is not None and isinstance(box.gross_mass_kg, (int, float))
                and math.isfinite(box.gross_mass_kg) and box.gross_mass_kg > fallback):
            emit('solas.vi2.verified_gross_mass',
                 'Container gross mass exceeds the assumed ISO 668 R fallback; individual CSC rating is unverified.',
                 [placement], measured=box.gross_mass_kg, limit=fallback, unit='kg')
    elif (isinstance(rating, bool) or not isinstance(rating, (int, float))
            or not math.isfinite(rating) or rating <= 0):
        emit('checker.missing_vessel_data',
             'Container CSC certified maximum gross mass evidence is unavailable or invalid.', [placement])
    elif (isinstance(box.gross_mass_kg, (int, float))
          and math.isfinite(box.gross_mass_kg) and box.gross_mass_kg > rating):
        emit('solas.vi2.verified_gross_mass',
             'Container verified gross mass exceeds its supplied CSC maximum operating gross mass.',
             [placement], measured=box.gross_mass_kg, limit=rating, unit='kg')


def background_capacity_violations(vessel, emit, *, note=None, checked_stacks=(),
                                   planned_mass_by_stack=None, actual_mass_by_stack=None):
    """Check supplied background evidence; disclose unavailable D29 qualification.

    Background audit fields are project evidence, not mandatory standard fields.
    Their absence is an explicit assumption note. Supplied contradictory data and
    demonstrated physical overloads remain hard. Parent geometry is recovered
    without hull physics; claimed audit ratings never replace original ratings.
    For D42 ``checked_stacks``, physical mass has already been checked from box
    records; audit masses remain integrity evidence, not another load estimate.
    """
    import hashlib

    from stowage.conventions import CONTAINER_HEIGHT_STD_M, CONTAINER_LENGTH_M, CONTAINER_WIDTH_M
    from stowage.model.vessel import FixedLoad
    from stowage.vessel.fleet import _parameter_bytes, list_fleet, load_vessel
    from stowage.vessel.fleet_conditions import CONDITION_PREFIX, CONDITION_VERSION, slice_condition
    from stowage.vessel.fleet_slices import recover_parent

    absent = set()

    def disclose():
        if absent and note is not None:
            note('ASSUMPTION: background stack capacity unverified; unavailable fields: '
                 + ', '.join(sorted(absent))
                 + '; omitted qualification does not certify the background cargo against original stack limits.')

    def missing(message):
        emit('checker.missing_vessel_data', f'Fixed background cargo capacity evidence: {message}')

    def number(value, *, positive=False):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (positive and value == 0)):
            raise ValueError('invalid numeric background record')
        return value

    def close(a, b):
        return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-6)

    def points_equal(actual, expected):
        def key(point):
            for value in (point.x_m, point.y_m, point.z_m):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value)):
                    raise ValueError('invalid fixed-load position')
            number(point.mass_kg)
            if point.silhouette is not None:
                for field in type(point.silhouette).model_fields:
                    value = getattr(point.silhouette, field)
                    if (isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value)):
                        raise ValueError('invalid fixed-load silhouette position')
                if any(getattr(point.silhouette, f'{axis}_min_m') >= getattr(point.silhouette, f'{axis}_max_m')
                       for axis in 'xyz'):
                    raise ValueError('reversed or empty fixed-load silhouette extent')
            return point.kind, point.x_m, point.y_m, point.z_m, point.mass_kg
        aa, bb = sorted(actual, key=key), sorted(expected, key=key)
        if len(aa) != len(bb):
            return False
        for a, b in zip(aa, bb):
            if a.kind != b.kind or not all(close(x, y) for x, y in zip(key(a)[1:], key(b)[1:])):
                return False
            if (a.silhouette is None) != (b.silhouette is None):
                return False
            if a.silhouette is not None:
                if not all(close(getattr(a.silhouette, field), getattr(b.silhouette, field))
                           for field in type(a.silhouette).model_fields):
                    return False
        return True

    try:
        # Legacy four-value points remain noncargo loads. Normalize a local
        # view before reading attributes; malformed raw inputs fail closed.
        loads = [FixedLoad.model_validate(load) for load in vessel.fixed_loads]
        has_cargo = any(load.kind == 'cargo' and load.mass_kg != 0 for load in loads)
        has_audit = any(source.startswith(CONDITION_PREFIX) for source in vessel.sources)
        # Qualification absence never masks invalid supplied physical numbers.
        if not points_equal(loads, loads):
            raise ValueError('invalid fixed-load geometry')
        if not has_cargo and not has_audit:
            return
        if not has_audit:
            absent.add('slice_condition.background_capacity_audit')
            return
        info = slice_condition(vessel)
        if not isinstance(info, dict):
            raise ValueError('invalid background audit document')
        for field in ('background_cells', 'background_stacks', 'base_fixed_loads'):
            value = info.get(field)
            if value is None:
                absent.add(field)
            elif not isinstance(value, list):
                raise ValueError(f'invalid {field} collection')
        records, stack_records = info.get('background_cells'), info.get('background_stacks')
        if info.get('version') is None:
            absent.add('version')
        elif info['version'] != CONDITION_VERSION:
            if records is None and stack_records is None:
                absent.add('current_background_capacity_audit_version')
            else:
                raise ValueError('stale versioned cell/stack audit')
        # Validate supplied records even when another qualification is absent.
        complete_cells = records is not None
        seen = set()
        for index, record in enumerate(records or []):
            if not isinstance(record, dict):
                raise ValueError('invalid background cell record')
            for field in ('cell_id', 'stack_id', 'mass_kg'):
                if record.get(field) is None:
                    absent.add(f'background_cells[{index}].{field}')
                    complete_cells = False
                elif field == 'mass_kg':
                    number(record[field], positive=True)
                elif not isinstance(record[field], str):
                    raise ValueError('invalid background cell/stack attribution')
            cid = record.get('cell_id')
            if cid is not None:
                if cid in seen:
                    raise ValueError('repeated background cell attribution')
                seen.add(cid)
        declared_stacks = {}
        for index, record in enumerate(stack_records or []):
            if not isinstance(record, dict):
                raise ValueError('invalid background stack record')
            sid = record.get('stack_id')
            if sid is None:
                absent.add(f'background_stacks[{index}].stack_id')
                continue
            if not isinstance(sid, str) or sid in declared_stacks:
                raise ValueError('invalid or repeated background stack attribution')
            for field in ('max_mass_20_kg', 'mass_kg', 'max_cell_mass_kg', 'on_deck'):
                if record.get(field) is None:
                    absent.add(f'background_stacks[{sid}].{field}')
                elif field == 'on_deck':
                    if not isinstance(record[field], bool):
                        raise ValueError('invalid background stack deck flag')
                else:
                    number(record[field])
            declared_stacks[sid] = record
        # D38 waives unavailable qualification, never an overload already
        # demonstrated by supplied mass/capacity. Do this before missing parent
        # provenance can return. Actual catalog limits take precedence over an
        # audit's rating; D42/aggregate columns checked from actual mass above
        # remain excluded so contradictory audit mass cannot invent overloads.
        local_stacks = {stack.id: stack for stack in [*vessel.stacks, *vessel.fixed_load_stacks]}
        for sid, audit in declared_stacks.items():
            if audit.get('mass_kg') is None:
                continue
            if actual_mass_by_stack is not None and sid in actual_mass_by_stack:
                if not close(audit['mass_kg'], actual_mass_by_stack[sid]):
                    missing(f'stack {sid} supplied audit mass contradicts actual attributable background mass.')
            if sid in checked_stacks:
                continue
            physical = local_stacks.get(sid)
            cap = (physical.max_mass_20_kg if physical.max_mass_20_kg is not None else physical.max_mass_kg
                   ) if physical is not None else audit.get('max_mass_20_kg')
            if cap is None:
                continue
            number(cap)
            planned = (planned_mass_by_stack or {}).get(sid, 0.0)
            number(planned)
            mass = math.fsum((audit['mass_kg'], planned))
            if not math.isfinite(mass):
                raise ValueError('nonfinite supplied background and planned stack mass')
            if mass > cap + 1e-5:
                emit('expert.physical.stack_weight',
                     f'Supplied background and planned cargo exceed stack {sid} mass limit.',
                     measured=mass, limit=cap, unit='kg')
        base = info.get('base_fixed_loads')
        if base is not None:
            base = [FixedLoad.model_validate(record) for record in base]
        from stowage.conventions import canonical_vessel_id
        from stowage.vessel.fleet_conditions import _verify_parent_parameter_sources
        root = canonical_vessel_id(vessel.slice_of or vessel.id)
        if root in list_fleet():
            declared = {source.partition('.toml sha256:')[2] for source in vessel.sources
                        if source.startswith('params/fleet/') and '.toml sha256:' in source
                        and canonical_vessel_id(source.partition('.toml sha256:')[0]
                            .removeprefix('params/fleet/')) == root}
            if not declared:
                absent.add('parent_parameter_sha256')
                return
            try:
                if len(declared) != 1:
                    raise ValueError('contradictory parent digests')
                _verify_parent_parameter_sources(vessel, root, _parameter_bytes(root))
            except ValueError as exc:
                raise ValueError('stale or mismatched pinned parent parameter digest') from exc
            parent = load_vessel(root, with_hull=False)
        else:
            if not any(source.startswith('stowage.slice_parent:v1:') for source in vessel.sources):
                absent.add('original_parent_snapshot')
                return
            parent = recover_parent(vessel)
        cells = {cell.id: cell for cell in parent.cells}
        stacks = {stack.id: stack for stack in parent.stacks}
        decision_stacks = {stack.id for stack in vessel.stacks}
        invalid = []
        # Independently check any supplied stack assertions, even without cells.
        for sid, audit in declared_stacks.items():
            if sid not in stacks or sid in decision_stacks:
                raise ValueError('unmappable or overlapping background stack attribution')
            stack = stacks[sid]
            cap = stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
            number(cap)
            if audit.get('max_mass_20_kg') is not None and not close(audit['max_mass_20_kg'], cap):
                invalid.append(f'stack {sid} claimed rating disagrees with original parent limit')
            cell_cap = 24999.0 if stack.on_deck else 30480.0
            if ((audit.get('max_cell_mass_kg') is not None and not close(audit['max_cell_mass_kg'], cell_cap))
                    or (audit.get('on_deck') is not None and audit['on_deck'] != stack.on_deck)):
                invalid.append(f'stack {sid} supplied cell/deck premise contradicts original parent geometry')
            if (sid not in checked_stacks and audit.get('mass_kg') is not None
                    and audit['mass_kg'] > cap+1e-5):
                emit('expert.physical.stack_weight',
                     f'Fixed background cargo exceeds original parent stack {sid} mass limit.',
                     measured=audit['mass_kg'], limit=cap, unit='kg')
        if not complete_cells:
            if invalid:
                missing('; '.join(invalid))
            return
        by_stack = {}
        for record in records:
            cid, sid, mass = record['cell_id'], record['stack_id'], record['mass_kg']
            if (cid not in cells or sid not in stacks or cells[cid].stack_id != sid
                    or cid not in stacks[sid].cell_ids or sid in decision_stacks or not cells[cid].accepts_20):
                raise ValueError('unmappable or overlapping background cell attribution')
            by_stack.setdefault(sid, {})[cid] = mass
        if set(declared_stacks) - set(by_stack):
            raise ValueError('supplied background stack records contradict the attributed cells')
        expected = []
        for sid, masses in by_stack.items():
            stack, audit = stacks[sid], declared_stacks.get(sid, {})
            if sid not in declared_stacks:
                absent.add(f'background_stacks[{sid}]')
            mass = sum(masses.values())
            cap = stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
            if (sid not in checked_stacks and mass > cap+1e-5
                    and (audit.get('mass_kg') is None or not close(audit['mass_kg'], mass))):
                emit('expert.physical.stack_weight',
                     f'Fixed background cargo exceeds original parent stack {sid} mass limit.',
                     measured=mass, limit=cap, unit='kg')
            # Existing C3 dry20 premises are neither universal ISO maxima nor CSC ratings.
            cell_cap = 24999.0 if stack.on_deck else 30480.0
            if ((audit.get('mass_kg') is not None and not close(audit['mass_kg'], mass))
                    or (audit.get('max_cell_mass_kg') is not None and not close(audit['max_cell_mass_kg'], cell_cap))
                    or (audit.get('on_deck') is not None and audit['on_deck'] != stack.on_deck)):
                invalid.append(f'stack {sid} audit disagrees with original parent or cell mass')
            ordered = [masses.get(cid, 0.) for cid in stack.cell_ids]
            if any(value > cell_cap+1e-5 for value in ordered):
                invalid.append(f'stack {sid} exceeds its declared C3 cell mass premise')
            if any(upper > lower+1e-5 for lower, upper in zip(ordered, ordered[1:])):
                invalid.append(f'stack {sid} has unsupported or inverted background tier masses')
            if sum(value > 0 for value in ordered)*CONTAINER_HEIGHT_STD_M > stack.max_height_m+1e-9:
                invalid.append(f'stack {sid} exceeds original parent height limit')
            points = [FixedLoad(x_m=cells[cid].lcg_m, y_m=cells[cid].tcg_m,
                                z_m=cells[cid].vcg_base_m+CONTAINER_HEIGHT_STD_M/2,
                                mass_kg=value, kind='cargo') for cid, value in masses.items()]
            if stack.on_deck:
                from stowage.model.vessel import FixedLoadSilhouette
                physical = [cells[cid] for cid in masses]
                expected.append(FixedLoad(
                    x_m=sum(p.x_m*p.mass_kg for p in points)/mass,
                    y_m=sum(p.y_m*p.mass_kg for p in points)/mass,
                    z_m=sum(p.z_m*p.mass_kg for p in points)/mass,
                    mass_kg=mass, kind='cargo', silhouette=FixedLoadSilhouette(
                        x_min_m=min(c.lcg_m for c in physical)-CONTAINER_LENGTH_M[20]/2,
                        x_max_m=max(c.lcg_m for c in physical)+CONTAINER_LENGTH_M[20]/2,
                        y_min_m=min(c.tcg_m for c in physical)-CONTAINER_WIDTH_M/2,
                        y_max_m=max(c.tcg_m for c in physical)+CONTAINER_WIDTH_M/2,
                        z_min_m=min(c.vcg_base_m for c in physical),
                        z_max_m=max(c.vcg_base_m for c in physical)+CONTAINER_HEIGHT_STD_M)))
            else:
                expected.extend(points)
        if base is not None and not points_equal(base, parent.fixed_loads):
            raise ValueError('base fixed loads do not match original parent loads')
        if any(load.kind == 'cargo' and load.mass_kg > 0 for load in parent.fixed_loads):
            absent.add('base_fixed_cargo_stack_attribution')
        if not points_equal(loads, [*parent.fixed_loads, *expected]):
            invalid.append('actual fixed-load masses, positions or silhouettes disagree with attributed background cells')
        if invalid:
            missing('; '.join(invalid))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError) as exc:
        missing(str(exc) or 'invalid parent geometry or background audit')
    finally:
        disclose()
