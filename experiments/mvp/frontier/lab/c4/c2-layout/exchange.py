"""Copied C3 Benders legal whole-page descent; native full checking remains required."""
import math
from time import monotonic


def _selection_valid(model, chosen):
    boxes, stacks = set(), set()
    for index in chosen:
        column = model.columns[index]
        page = model.pages[column.page]
        if (index in model.banned or boxes.intersection(column.boxes)
                or stacks.intersection(page.stacks)
                or not model.legal(page, column.boxes) or not model.lash_ok(page, column.boxes)):
            return False
        boxes.update(column.boxes)
        stacks.update(page.stacks)
    return boxes == set(model.boxes)

def _column_geometry(model, page_index, boxes):
    """Exact geometric-half-height cargo moments for a pure whole page."""
    page, T = model.pages[page_index], model.T
    cells = [model.p.cells[T.cell_ids[c]] for c in T.slot_cells[page.slots[0], :int(T.slot_n[page.slots[0]])]]
    x = math.fsum(cell.lcg_m for cell in cells)/len(cells)
    y = math.fsum(cell.tcg_m for cell in cells)/len(cells)
    z = max(float(T.stack_base_z[j]) for j in page.stacks)
    vertical = longitudinal = transverse = mass = 0.
    for b in boxes:
        box = model.p.boxes[T.box_ids[b]]
        height = box.height_m+(box.oog.over_height_m if box.oog else 0.)
        m = box.gross_mass_kg
        vertical += m*(z+height/2)
        dx = (box.oog.over_length_fore_m-box.oog.over_length_aft_m)/2 if box.oog else 0.
        dy = (box.oog.over_width_port_m-box.oog.over_width_starboard_m)/2 if box.oog else 0.
        longitudinal += m*(x+dx)
        transverse += m*(y+dy)
        mass += m
        z += height
    return vertical, longitudinal, transverse, z, mass

def descend(model, chosen, *, deadline, passes=24, lcg_tolerance_m=.5,
            heel_limit_tm=100., roof_tolerance_m=0., protect_special=True, focus_boxes=()):
    """Lower exact cargo moment by legal swaps/transfers of whole-page boxes.

    Every move updates both affected whole columns and preserves concrete-box
    coverage. LCG and heel guards are search restrictions, not certificates.
    This avoids solving a large cold integer master to change two boxes.
    """
    started = monotonic()
    if not _selection_valid(model, chosen):
        return list(chosen), dict(status='invalid_seed', moves=0)
    columns = [model.columns[k] for k in chosen]
    geometry = [_column_geometry(model, c.page, c.boxes) for c in columns]
    total = [math.fsum(g[j] for g in geometry) for j in (0, 1, 2)]
    mass = math.fsum(g[4] for g in geometry)
    initial_moment, initial_lcg = total[0], total[1]
    roof_max = max(g[3] for g in geometry)+roof_tolerance_m
    protected = ({b for b in model.boxes if model.T.is_dg[b] or model.T.heat[b]
                  or model.T.fallback[b] or model.T.closes_stack[b]
                  or model.p.boxes[model.T.box_ids[b]].oog is not None}
                 if protect_special else set())
    moves = []
    focus = set(focus_boxes)
    examined = 0
    for iteration in range(passes):
        best = None
        for ia, a in enumerate(columns):
            pa = model.pages[a.page]
            for ib in range(ia+1, len(columns)):
                if monotonic() >= deadline:
                    break
                b = columns[ib]
                pb = model.pages[b.page]
                if pa.kind != pb.kind:
                    continue
                old_v = geometry[ia][0]+geometry[ib][0]
                old_x = geometry[ia][1]+geometry[ib][1]
                old_y = geometry[ia][2]+geometry[ib][2]

                def consider(one, two, kind):
                    nonlocal best, examined
                    examined += 1
                    if iteration == 0 and focus and not any(
                            model.T.box_ids[old] in focus and old != new
                            for old, new in list(zip(a.boxes, one))+list(zip(b.boxes, two))):
                        return
                    if (len(one) > len(pa.slots) or len(two) > len(pb.slots)
                            or not model.legal(pa, one) or not model.legal(pb, two)
                            or not model.lash_ok(pa, one) or not model.lash_ok(pb, two)):
                        return
                    ga = _column_geometry(model, a.page, one)
                    gb = _column_geometry(model, b.page, two)
                    gain = old_v-ga[0]-gb[0]
                    if gain <= 1e-5 or max(ga[3], gb[3]) > roof_max+1e-8:
                        return
                    new_x = total[1]-old_x+ga[1]+gb[1]
                    new_y = total[2]-old_y+ga[2]+gb[2]
                    if (abs(new_x-initial_lcg) > mass*lcg_tolerance_m+1e-6
                            or abs(new_y) > heel_limit_tm*1000.+1e-6):
                        return
                    rank = (gain, -abs(new_y), -max(ga[3], gb[3]))
                    if best is None or rank > best[0]:
                        best = rank, ia, ib, tuple(one), tuple(two), ga, gb, kind

                # Whole-column exchange, then single concrete-box exchanges.
                if not protected.intersection(a.boxes+b.boxes):
                    consider(b.boxes, a.boxes, 'whole_page_swap')
                for i in range(len(a.boxes)):
                    if monotonic() >= deadline:
                        break
                    if a.boxes[i] in protected:
                        continue
                    for j in range(len(b.boxes)):
                        if b.boxes[j] in protected:
                            continue
                        one, two = list(a.boxes), list(b.boxes)
                        one[i], two[j] = two[j], one[i]
                        consider(one, two, 'box_swap')
                # Lowering a roof may require changing the tier counts too.
                if (len(a.boxes) > 1 and len(b.boxes) < len(pb.slots)
                        and not (not pa.deck and pb.deck)):
                    for i, moved in enumerate(a.boxes):
                        if moved in protected:
                            continue
                        for j in range(len(b.boxes)+1):
                            consider(a.boxes[:i]+a.boxes[i+1:],
                                     b.boxes[:j]+(moved,)+b.boxes[j:], 'box_transfer')
                if (len(b.boxes) > 1 and len(a.boxes) < len(pa.slots)
                        and not (not pb.deck and pa.deck)):
                    for i, moved in enumerate(b.boxes):
                        if moved in protected:
                            continue
                        for j in range(len(a.boxes)+1):
                            consider(a.boxes[:j]+(moved,)+a.boxes[j:],
                                     b.boxes[:i]+b.boxes[i+1:], 'box_transfer')
            if monotonic() >= deadline:
                break
        if best is None:
            if iteration == 0 and focus:
                focus.clear()
                continue
            break
        rank, ia, ib, one, two, ga, gb, kind = best
        for j in range(3):
            total[j] += ga[j]+gb[j]-geometry[ia][j]-geometry[ib][j]
        first_page, second_page = columns[ia].page, columns[ib].page
        model.add(first_page, one)
        model.add(second_page, two)
        columns[ia] = model.columns[model.pool[first_page, one]]
        columns[ib] = model.columns[model.pool[second_page, two]]
        geometry[ia], geometry[ib] = ga, gb
        moves.append(dict(kind=kind, gain_kg_m=rank[0], cargo_kg_change_m=(total[0]-initial_moment)/mass))
        if monotonic() >= deadline:
            break
    result = [model.pool[c.page, c.boxes] for c in columns]
    assert _selection_valid(model, result)
    return result, dict(status='improved' if moves else 'local_stop', moves=moves,
        examined=examined, s=monotonic()-started,
        cargo_kg_change_m=(total[0]-initial_moment)/mass,
        cargo_moment_change_kg_m=total[0]-initial_moment,
        lcg_change_m=(total[1]-initial_lcg)/mass, heel_tm=total[2]/1000.)
