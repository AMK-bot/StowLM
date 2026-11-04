"""Physical stack masses from attributable fixed cargo and planned boxes.

FixedLoad aggregates remain the physics mass terms. This checker reads their
individual boxes solely to audit support-column capacity, without adding mass
again to the ship or to the move objective. It uses the same half-mass support
convention and 20/mixed versus 40-only ratings as the ordinary static core.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import math

from stowage.conventions import CONTAINER_HEIGHT_STD_M, CONTAINER_LENGTH_M, CONTAINER_WIDTH_M
from stowage.checker.slots import cells_of_slot
from stowage.model.vessel import Cell, FixedCargoBox, Stack


def fixed_cargo_records(instance):
    """Read explicit fixed boxes once, with stable physical reference identities.

    These are local checker views, never additions to the instance's cargo,
    load list, physics masses or move objective. Physical addresses identify
    fixed boxes because FixedCargoBox has no container-id field. Callers keep
    their slot references and exclude these synthetic identities from the
    Violation.containers ISO-id field. Existing D42 checks diagnose malformed
    box/aggregate evidence independently.
    """
    from types import SimpleNamespace
    from stowage.conventions import format_slot
    from stowage.checker.slots import parse_slot

    unique = {}
    for load in instance.vessel.fixed_loads:
        boxes = load.get('cargo_boxes') if isinstance(load, dict) else getattr(load, 'cargo_boxes', None)
        if not isinstance(boxes, (list, tuple)):
            continue
        for value in boxes:
            try:
                data = value if isinstance(value, dict) else value.model_dump(mode='python')
                box = FixedCargoBox.model_validate(data)
            except (ValueError, TypeError, AttributeError, OverflowError):
                continue
            unique.setdefault(tuple(box.cell_ids), box)
    result = []
    for ids, record in sorted(unique.items()):
        address = parse_slot(ids[0])
        bay = address.bay if len(ids) == 1 else (address.bay + parse_slot(ids[1]).bay) // 2
        slot = format_slot(bay, address.row, address.tier)
        identity = 'fixed:' + ','.join(ids)
        placement = SimpleNamespace(container_id=identity, slot=slot)
        box = SimpleNamespace(id=identity, gross_mass_kg=record.gross_mass_kg,
                              height_m=record.height_m, length_ft=record.length_ft,
                              tare_kg=0.0, oog=None, special_stowage=[],
                              reefer=False, dg=None)
        result.append((placement, box, record))
    return result


def d42_background_capacity_violations(instance, ship, emit, *, note=None, background_mass_by_stack=None):
    """Enforce attributable support loads before aggregate evidence validation.

    Mutated/model_construct box records are revalidated from data, independently
    of FixedLoad.mass_kg/CG. Legacy aggregate mass is used once only when explicit
    audit support shares or unique physical-column geometry bind it. A broken aggregate therefore cannot hide an observable
    stack overload. Original pinned/snapshotted parent ratings take precedence
    over caller-provided catalogs; without a recoverable parent the supplied
    physical catalogs are authoritative. Return the checked background stack ids,
    so legacy audit claims cannot duplicate their authoritative mass checks. If
    supplied, background_mass_by_stack receives actual background-only sums for
    audit integrity comparisons before planned mass is added. No hull builder
    is called.
    """
    vessel = instance.vessel

    def missing(message):
        emit('checker.missing_vessel_data', f'D42 background stack capacity: {message}')

    def payload(record):
        return dict(record) if isinstance(record, dict) else record.model_dump(mode='python')

    def records(values, model, label):
        result = {}
        if not isinstance(values, (list, tuple)):
            missing(f'{label} must be a record list.')
            return result
        for index, record in enumerate(values):
            try:
                data = payload(record)
                numeric = (('lcg_m', 'tcg_m', 'vcg_base_m') if model is Cell else
                           ('max_mass_kg', 'max_mass_20_kg', 'max_mass_40_kg', 'max_height_m', 'base_z_m'))
                for field in numeric:
                    value = data.get(field)
                    if value is None and field in ('max_mass_20_kg', 'max_mass_40_kg'):
                        continue
                    if (isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value)):
                        raise ValueError(f'{field} must be a finite physical number')
                parsed = model.model_validate(data)
                if parsed.id in result:
                    missing(f'{label} repeats {parsed.id}.')
                else:
                    result[parsed.id] = parsed
            except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                missing(f'{label}[{index}] is invalid: {exc}')
        return result

    # Do not normalize aggregate FixedLoads here: validation of their mass/CG
    # relationship must not short-circuit the independent per-box mass sums.
    supplied = []
    legacy = []
    if not isinstance(vessel.fixed_loads, (list, tuple)):
        missing('fixed_loads must be a record list.')
        return set()
    for index, load in enumerate(vessel.fixed_loads):
        boxes = load.get('cargo_boxes') if isinstance(load, dict) else getattr(load, 'cargo_boxes', None)
        if boxes is None:
            kind = load.get('kind') if isinstance(load, dict) else getattr(load, 'kind', None)
            if kind == 'cargo':
                try:
                    data = payload(load)
                    for field in ('mass_kg', 'x_m', 'y_m', 'z_m'):
                        value = data.get(field)
                        if (isinstance(value, bool) or not isinstance(value, (int, float))
                                or not math.isfinite(value) or (field == 'mass_kg' and value < 0)):
                            raise ValueError(f'{field} must be a finite physical number')
                    if data['mass_kg'] > 0:
                        legacy.append((index, data))
                except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                    missing(f'fixed_loads[{index}] is invalid: {exc}')
            continue
        if not isinstance(boxes, (list, tuple)):
            missing(f'fixed_loads[{index}].cargo_boxes must be a record list.')
            continue
        kind = load.get('kind') if isinstance(load, dict) else getattr(load, 'kind', None)
        if kind != 'cargo':
            missing(f'fixed_loads[{index}] supplies cargo_boxes without kind cargo.')
        for box_index, box in enumerate(boxes):
            try:
                supplied.append(FixedCargoBox.model_validate(payload(box)))
            except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                missing(f'fixed_loads[{index}].cargo_boxes[{box_index}] is invalid: {exc}')
    if not supplied and not legacy:
        return set()

    fixed_cells = records(vessel.fixed_load_cells, Cell, 'fixed_load_cells')
    fixed_stacks = records(vessel.fixed_load_stacks, Stack, 'fixed_load_stacks')
    decision_cells = records(vessel.cells, Cell, 'cells')
    decision_stacks = records(vessel.stacks, Stack, 'stacks')
    for cid in fixed_cells.keys() & decision_cells.keys():
        missing(f'fixed and decision cell catalogs overlap at {cid}.')
    for sid in fixed_stacks.keys() & decision_stacks.keys():
        missing(f'fixed and decision stack catalogs overlap at {sid}.')
    local_cells = {**decision_cells, **fixed_cells}
    local_stacks = {**decision_stacks, **fixed_stacks}
    cells, stacks = local_cells, local_stacks
    try:
        from stowage.vessel.fleet import _parameter_bytes, list_fleet, load_vessel
        from stowage.vessel.fleet_slices import recover_parent

        from stowage.conventions import canonical_vessel_id
        from stowage.vessel.fleet_conditions import _verify_parent_parameter_sources
        root = canonical_vessel_id(vessel.slice_of or vessel.id)
        parent = None
        if root in list_fleet():
            declared = {source.partition('.toml sha256:')[2] for source in vessel.sources
                        if source.startswith('params/fleet/') and '.toml sha256:' in source
                        and canonical_vessel_id(source.partition('.toml sha256:')[0]
                            .removeprefix('params/fleet/')) == root}
            if declared:
                try:
                    if len(declared) != 1:
                        raise ValueError('contradictory parent digests')
                    _verify_parent_parameter_sources(vessel, root, _parameter_bytes(root))
                except ValueError:
                    missing('pinned original parent parameter digest is stale or mismatched.')
                else:
                    parent = load_vessel(root, with_hull=False)
        else:
            snapshots = [source[len('stowage.slice_parent:v1:'):] for source in vessel.sources
                         if source.startswith('stowage.slice_parent:v1:')]
            if snapshots:
                if any(source.startswith('stowage.slice_condition:') for source in vessel.sources):
                    parent = recover_parent(vessel)
                else:
                    # The producer's recovery also needs a loading-condition
                    # audit to distinguish original stores from synthetic loads.
                    # Capacity-only recovery needs just its validated geometry;
                    # missing C3 qualification is handled by the legacy note.
                    import base64
                    import zlib
                    from stowage.model import Vessel

                    try:
                        packed = base64.b64decode(snapshots[-1], validate=True)
                        parent = Vessel.model_validate_json(zlib.decompress(packed))
                    except (ValueError, zlib.error) as exc:
                        raise ValueError('invalid original parent capacity snapshot') from exc
                    if canonical_vessel_id(parent.slice_of or parent.id) != root:
                        raise ValueError('original parent capacity snapshot has a different root vessel')
        if parent is not None:
            cells = records([*parent.cells, *parent.fixed_load_cells], Cell, 'original parent cells')
            stacks = records([*parent.stacks, *parent.fixed_load_stacks], Stack, 'original parent stacks')
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError, OSError) as exc:
        missing(f'original parent recovery failed: {exc}')

    columns = defaultdict(list)
    background = set()
    occupied = set()
    for box in supplied:
        for cid in box.cell_ids:
            cell = cells.get(cid)
            if cell is None or cell.stack_id not in stacks:
                missing(f'background support {cid} has no physical cell/stack evidence.')
                continue
            sid, stack = cell.stack_id, stacks[cell.stack_id]
            local = fixed_cells.get(cid)
            if (local is None or local.stack_id != sid or sid not in fixed_stacks
                    or cid not in fixed_stacks[sid].cell_ids):
                missing(f'background support {cid} is absent from reciprocal fixed-load catalogs.')
            if cid not in stack.cell_ids:
                missing(f'background support {cid} is absent from original stack {sid}.')
                continue
            if not getattr(cell, f'accepts_{box.length_ft}'):
                missing(f'background support {cid} does not accept length {box.length_ft}.')
            if cid in occupied:
                missing(f'background cargo overlaps physical support {cid}.')
            occupied.add(cid)
            columns[sid].append((box.gross_mass_kg / len(box.cell_ids), box.length_ft))
            background.add(sid)

    # Legacy aggregates have no box length. Attribute only exact numerical
    # geometry matches to one real column; never snap a point to its nearest
    # cell or divide an unexplained mass between possible long-box supports.
    # The micrometre allowance is the model's CG consistency tolerance.
    def close(a, b):
        return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-6)

    audit_groups = []
    if legacy:
        try:
            from stowage.vessel.fleet_conditions import slice_condition

            info = slice_condition(vessel)
            raw = info.get('background_cells') if isinstance(info, dict) else None
            if isinstance(raw, list):
                deck_groups = defaultdict(list)
                all_tokens = []
                for record in raw:
                    if not isinstance(record, dict):
                        continue  # The legacy integrity checker diagnoses it.
                    cid, sid, mass = record.get('cell_id'), record.get('stack_id'), record.get('mass_kg')
                    if (cid not in cells or sid not in stacks or cells[cid].stack_id != sid
                            or cid not in stacks[sid].cell_ids or isinstance(mass, bool)
                            or not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass <= 0):
                        continue
                    token = (sid, mass, cells[cid])
                    all_tokens.append(token)
                    if stacks[sid].on_deck:
                        deck_groups[sid].append(token)
                    else:
                        audit_groups.append([token])
                audit_groups.extend(deck_groups.values())
                # A legacy aggregate spanning several explicitly attributed
                # columns can be bound to the complete supplied cell inventory
                # only if its actual mass and all first moments agree.
                if len({sid for sid, _, _ in all_tokens}) > 1:
                    audit_groups.append(all_tokens)
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
            pass  # Integrity errors are independently hard in the legacy audit.

    used_audit_groups = set()
    for index, load in legacy:
        candidates = set()
        silhouette = load.get('silhouette')
        for cid, cell in cells.items():
            stack = stacks.get(cell.stack_id)
            if (stack is None or cid not in stack.cell_ids or not close(load['x_m'], cell.lcg_m)
                    or not close(load['y_m'], cell.tcg_m)
                    or not stack.base_z_m-1e-6 <= load['z_m'] <= stack.base_z_m+stack.max_height_m+1e-6):
                continue
            if silhouette is not None:
                try:
                    envelope = silhouette if isinstance(silhouette, dict) else silhouette.model_dump()
                    if not (envelope['x_min_m'] >= cell.lcg_m-CONTAINER_LENGTH_M[20]/2-1e-6
                            and envelope['x_max_m'] <= cell.lcg_m+CONTAINER_LENGTH_M[20]/2+1e-6
                            and envelope['y_min_m'] >= cell.tcg_m-CONTAINER_WIDTH_M/2-1e-6
                            and envelope['y_max_m'] <= cell.tcg_m+CONTAINER_WIDTH_M/2+1e-6
                            and envelope['z_min_m'] >= stack.base_z_m-1e-6
                            and envelope['z_max_m'] <= stack.base_z_m+stack.max_height_m+1e-6):
                        continue
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
            candidates.add(stack.id)
        matched = []
        for group_index, group in enumerate(audit_groups):
            if group_index in used_audit_groups:
                continue
            try:
                mass = math.fsum(value for _, value, _ in group)
                if not math.isfinite(mass):
                    raise ValueError('nonfinite audited mass sum')
                if not close(mass, load['mass_kg']):
                    continue
                centroid = (math.fsum(value/mass*getattr(cell, attr) for _, value, cell in group)
                            for attr in ('lcg_m', 'tcg_m', 'vcg_base_m'))
                x, y, z = centroid
            except (ValueError, OverflowError):
                missing('supplied legacy audit mass or first moment is nonfinite.')
                continue
            if close(x, load['x_m']) and close(y, load['y_m']) and close(z+CONTAINER_HEIGHT_STD_M/2, load['z_m']):
                shares = defaultdict(float)
                for sid, value, _ in group:
                    shares[sid] += value
                matched.append((group_index, dict(shares)))
        # Equivalent audit groups that describe the same actual support shares
        # do not create ambiguity; different column allocations do.
        allocations = {tuple(sorted(shares.items())) for _, shares in matched}
        if len(candidates) == 1:
            sid = next(iter(candidates))
            length = None
            if any(set(shares) == {sid} for _, shares in matched):
                length = 20  # Explicit C3 cell attribution, not an ISO mass cap.
            columns[sid].append((load['mass_kg'], length))
            background.add(sid)
        elif len(allocations) == 1:
            shares = dict(next(iter(allocations)))
            for sid, mass in shares.items():
                columns[sid].append((mass, 20))
                background.add(sid)
            used_audit_groups.update(group_index for group_index, _ in matched)
        else:
            if note is not None:
                note(f'ASSUMPTION: background fixed_loads[{index}] stack capacity unverified; '
                     'actual aggregate has no unique physical support-column attribution.')
            continue
        if len(candidates) == 1 and length is None and note is not None:
            note(f'ASSUMPTION: background fixed_loads[{index}] length unavailable; '
                 'stack mass uses the largest supplied physical column rating as an upper-bound screen; '
                 'size-specific capacity remains unverified.')

    if background_mass_by_stack is not None:
        for sid in sorted(background):
            try:
                mass = math.fsum(value for value, _ in columns[sid])
                if not math.isfinite(mass):
                    raise ValueError('nonfinite background support-column mass')
                background_mass_by_stack[sid] = mass
            except (ValueError, OverflowError) as exc:
                missing(f'stack {sid}: {exc}')

    cargo = {box.id: box for box in instance.containers}
    for placement in ship.placements:
        box = cargo.get(placement.container_id)
        if box is None:
            continue  # The ordinary core diagnoses unknown planned cargo.
        try:
            footprint = cells_of_slot(placement.slot)
            mass = box.gross_mass_kg
            if (isinstance(mass, bool) or not isinstance(mass, (int, float))
                    or not math.isfinite(mass) or mass <= 0 or box.length_ft not in (20, 40, 45)):
                raise ValueError('planned box has invalid gross mass or length')
            for cid in footprint:
                cell = cells.get(cid)
                if cell is None:
                    if cid in local_cells:
                        missing(f'planned support {cid} is absent from original parent geometry.')
                    continue
                sid = cell.stack_id
                if sid not in background:
                    continue
                if sid not in stacks or cid not in stacks[sid].cell_ids:
                    missing(f'planned support {cid} has inconsistent stack membership.')
                    continue
                columns[sid].append((mass / len(footprint), box.length_ft))
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            missing(f'planned box {placement.container_id} cannot be combined with background: {exc}')

    for sid in sorted(background):
        entries, stack = columns[sid], stacks[sid]
        lengths = {length for _, length in entries}
        limit = stack.max_mass_kg
        if 20 in lengths and stack.max_mass_20_kg is not None:
            limit = stack.max_mass_20_kg
        elif None in lengths:
            # Unknown legacy length can be 20-only, long-only or mixed. The
            # largest applicable physical rating is a proof bound: exceeding
            # it violates every supplied size interpretation. It is not a new
            # physical approximation or a per-cell ISO R capacity.
            limit = max(value for value in (stack.max_mass_kg, stack.max_mass_20_kg,
                                             stack.max_mass_40_kg) if value is not None)
        elif lengths <= {40, 45} and stack.max_mass_40_kg is not None:
            limit = stack.max_mass_40_kg
        try:
            mass = math.fsum(value for value, _ in entries)
            if not math.isfinite(mass):
                raise ValueError('nonfinite combined support-column mass')
        except (ValueError, OverflowError) as exc:
            missing(f'stack {sid}: {exc}')
            continue
        if mass > limit:
            emit('expert.physical.stack_weight',
                 f'D42 background and planned cargo exceed physical stack {sid} mass limit.',
                 measured=mass, limit=limit, unit='kg')

    return background
