"""Placement comparisons derived from cargo, geometry and the replay projection.

These are observations about this departure stow, never an optimality claim.
Every Thai numeric role is filled by the same code value as its English role.
"""
from __future__ import annotations

from stowage.conventions import cells_of_slot, format_slot, parse_slot

from experiments.mvp.pipeline.contract.schema import sha256_json


def additional_facts(instance, plan, report, box_id, replay):
    """Return bilingual fact rows; caller must first validate input binding."""
    ship = replay.final_state.ship
    sources = {name: obj.model_dump(mode='json') for name, obj in
               [('instance', instance), ('plan', plan), ('report', report), ('replay', ship)]}
    hashes = {name: sha256_json(value) for name, value in sources.items()}

    def ref(source, path):
        value = sources[source]
        for key in path.lstrip('/').split('/'):
            value = value[int(key)] if isinstance(value, list) else value[key.replace('~1', '/').replace('~0', '~')]
        return dict(source=source, sha256=hashes[source], path=path, value=value,
                    href=f'{source}:{hashes[source]}#{path}')

    boxes = {box.id: box for box in instance.containers}
    positions = {p.container_id: p for p in ship.placements}
    box, position = boxes[box_id], positions[box_id]
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    footprint = set(cells_of_slot(position.slot))
    own_stacks = {cells[cid].stack_id for cid in footprint}
    cargo_refs = {b.id: ref('instance', f'/containers/{i}') for i, b in enumerate(instance.containers)}
    placement_refs = {p.container_id: ref('replay', f'/placements/{i}') for i, p in enumerate(ship.placements)}
    mass_refs = {b.id: ref('instance', f'/containers/{i}/gross_mass_kg') for i, b in enumerate(instance.containers)}
    pod_refs = {b.id: ref('instance', f'/containers/{i}/pod') for i, b in enumerate(instance.containers)}
    all_refs = [ref('instance', '/vessel/stacks'), ref('replay', '/placements'),
                *(mass_refs[p.container_id] for p in ship.placements)]
    order_refs = [ref('instance', '/vessel/stacks'), ref('replay', '/placements'),
                  *(pod_refs[p.container_id] for p in ship.placements)]
    out = []

    def add(sid, en, th, refs, summary_en=None, summary_th=None):
        row = dict(id=sid, text=en, th=th, evidence=refs)
        if summary_en is not None:
            row.update(summary_en=summary_en, summary_th=summary_th)
        out.append(row)

    dg = ', '.join(f'class {entry.imdg_class}, UN {entry.un_number}'
                   for entry in [box.dg, *(box.dg.additional if box.dg else [])]) if box.dg else 'none'
    # Code, not a translator, assigns each numeric value to its field label.
    values = (f'ISO {box.iso_type}; length {box.length_ft} ft; height {box.height_m:g} m; '
              f'gross mass {box.gross_mass_kg:g} kg; DG {dg}; '
              f'reefer={str(box.reefer).lower()}; empty={str(box.empty).lower()}')
    thai_dg = ', '.join(f'ชั้น {entry.imdg_class}, UN {entry.un_number}'
                        for entry in [box.dg, *box.dg.additional]) if box.dg else 'ไม่มี'
    thai_reefer = 'ต้องใช้ปลั๊กสำหรับควบคุมอุณหภูมิ' if box.reefer else 'ไม่ต้องใช้ปลั๊กสำหรับควบคุมอุณหภูมิ'
    thai_empty = 'เป็นตู้เปล่า' if box.empty else 'ไม่ใช่ตู้เปล่า'
    thai_values = (f'ISO {box.iso_type}; ความยาว {box.length_ft} ft; ความสูง {box.height_m:g} m; '
                   f'มวลรวม {box.gross_mass_kg:g} kg; DG {thai_dg}; '
                   f'{thai_reefer}; {thai_empty}')
    add('cargo', f'{box_id} cargo: {values}.', f'ข้อมูลตู้ {box_id}: {thai_values}.', [cargo_refs[box_id]],
        f'{box_id} cargo has gross mass {box.gross_mass_kg:g} kg and ISO type {box.iso_type}',
        f'ตู้ {box_id} มีมวลรวม {box.gross_mass_kg:g} kg และชนิด ISO {box.iso_type}')

    bays = {parse_slot(cid).bay for cid in footprint}
    bay_members = [p.container_id for p in ship.placements
                   if any(parse_slot(cid).bay in bays for cid in cells_of_slot(p.slot))]
    bay_rank = 1 + sum(boxes[cid].gross_mass_kg > box.gross_mass_kg for cid in bay_members)
    bay_mean = sum(boxes[cid].gross_mass_kg for cid in bay_members) / len(bay_members)
    bay_label = ','.join(f'{bay:03d}' for bay in sorted(bays))
    location = 'deck' if parse_slot(position.slot).on_deck else 'hold'
    thai_location = 'บนดาดฟ้า' if parse_slot(position.slot).on_deck else 'ในระวาง'
    add('bay_profile',
        f'{box_id} is in the {location}; mass {box.gross_mass_kg:g} kg against support-bay group {bay_label} mean {bay_mean:g} kg; weight rank {bay_rank} of {len(bay_members)} (heaviest first, ties share rank). Each spanning box is counted once; fixed background cargo is excluded.',
        f'{box_id} อยู่{thai_location}; มวล {box.gross_mass_kg:g} kg เทียบกับมวลเฉลี่ยของกลุ่มเบย์รองรับ {bay_label} ที่ {bay_mean:g} kg; อันดับน้ำหนัก {bay_rank} จาก {len(bay_members)} (หนักที่สุดก่อน น้ำหนักเท่ากันมีอันดับร่วมกัน). นับตู้ที่คร่อมเบย์เพียงครั้งเดียว; ไม่รวมสินค้าพื้นหลังที่ตรึงไว้.', all_refs,
        f'{box_id} has weight rank {bay_rank} of {len(bay_members)} in support-bay group {bay_label} (heaviest first, ties share rank)',
        f'{box_id} มีอันดับน้ำหนัก {bay_rank} จาก {len(bay_members)} ในกลุ่มเบย์รองรับ {bay_label} (หนักที่สุดก่อน น้ำหนักเท่ากันมีอันดับร่วมกัน)')

    first_call = {}
    for call in instance.rotation.calls:
        if call.index > instance.call_index:
            first_call.setdefault(call.locode, call.index)
    earlier_below, later_above = set(), set()
    for sid in sorted(own_stacks):
        stack = stacks[sid]
        column = sorted((p for p in ship.placements
                         if any(cells[cid].stack_id == sid for cid in cells_of_slot(p.slot))),
                        key=lambda p: parse_slot(p.slot).tier)
        index = next(i for i, p in enumerate(column) if p.container_id == box_id)
        rank = 1 + sum(boxes[p.container_id].gross_mass_kg > box.gross_mass_kg for p in column)
        add(f'rank:{sid}',
            f'{box_id} weight rank in {sid}: {rank} of {len(column)} (heaviest first, ties share rank).',
            f'อันดับน้ำหนักของ {box_id} ใน {sid}: {rank} จาก {len(column)} (หนักที่สุดก่อน น้ำหนักเท่ากันมีอันดับร่วมกัน).', all_refs,
            f'{box_id} has weight rank {rank} of {len(column)} in {sid} (heaviest first, ties share rank)',
            f'{box_id} มีอันดับน้ำหนัก {rank} จาก {len(column)} ใน {sid} (หนักที่สุดก่อน น้ำหนักเท่ากันมีอันดับร่วมกัน)')
        low, high = index, index
        while low > 0 and boxes[column[low - 1].container_id].pod == box.pod:
            low -= 1
        while high + 1 < len(column) and boxes[column[high + 1].container_id].pod == box.pod:
            high += 1
        block = ', '.join(p.container_id for p in column[low:high + 1])
        add(f'pod_block:{sid}',
            f'In {sid}, {box_id} belongs to a contiguous POD {box.pod} block of {high-low+1} boxes: {block}. This block does not establish that no restow is needed.',
            f'ใน {sid}, {box_id} อยู่ในกลุ่มตู้ติดกันที่มี POD {box.pod} จำนวน {high-low+1} ตู้: {block}. กลุ่มนี้ไม่ได้ยืนยันว่าไม่ต้องยกพักตู้.', order_refs,
            f'{box_id} belongs to a contiguous POD {box.pod} block of {high-low+1} boxes in {sid}, which does not establish that no restow is needed',
            f'{box_id} อยู่ในกลุ่มตู้ติดกันที่มี POD {box.pod} จำนวน {high-low+1} ตู้ใน {sid} ซึ่งไม่ได้ยืนยันว่าไม่ต้องยกพักตู้')
        earlier_below.update(p.container_id for p in column[:index]
                             if first_call[boxes[p.container_id].pod] < first_call[box.pod])
        later_above.update(p.container_id for p in column[index + 1:]
                           if first_call[boxes[p.container_id].pod] > first_call[box.pod])

    if box.reefer:
        occupied = {cid for p in ship.placements for cid in cells_of_slot(p.slot)}
        plugs = [c for c in instance.vessel.cells if c.reefer_plug and getattr(c, f'accepts_{box.length_ft}')]
        free = sum(c.id not in occupied for c in plugs)
        add('reefer_capacity',
            f'{box_id} requires a reefer plug; {free} of {len(plugs)} length-compatible powered physical cells are unoccupied. Cell availability alone does not establish a legal supported slot.',
            f'{box_id} ต้องใช้ปลั๊กตู้เย็น; เซลล์กายภาพที่มีไฟและรองรับความยาวนี้ว่าง {free} จาก {len(plugs)} เซลล์. เซลล์ว่างเพียงอย่างเดียวยังไม่ยืนยันว่าเป็นตำแหน่งที่ถูกกฎและมีฐานรองรับ.',
            [cargo_refs[box_id], ref('instance', '/vessel/cells'), *placement_refs.values()],
            f'{free} of {len(plugs)} length-compatible powered cells are unoccupied, without a full legality check',
            f'เซลล์ที่มีไฟและรองรับความยาวนี้ว่าง {free} จาก {len(plugs)} เซลล์ โดยยังไม่ได้ตรวจสอบความถูกต้องตามกฎทั้งหมด')

    if box.dg:
        _dg_facts(add, instance, report, box, ship, cells, stacks, ref,
                  [cargo_refs[box_id], *all_refs])

    # The count object has no per-box attribution. Keep that limitation visible.
    if report.count is not None and report.count.future == replay.count.future:
        call = first_call[box.pod]
        future_index = next((i for i, count in enumerate(replay.count.future) if count.call_index == call), None)
        if future_index is not None:
            count = replay.count.future[future_index]
            earlier = ', '.join(sorted(earlier_below)) or 'none'
            later = ', '.join(sorted(later_above)) or 'none'
            earlier_th = earlier if earlier_below else 'ไม่มี'
            later_th = later if later_above else 'ไม่มี'
            add('restow',
                f'Projection at {box.pod} call {call}: whole-plan {count.restowed_boxes} restowed boxes, {count.restow_lifts} restow lifts and {count.hatch_cover_lifts} cover lifts. {box_id} has same-column earlier POD boxes below: {earlier}; same-column later POD boxes above it: {later}. Counts include hatch and indirect blockers; they are not attributed solely to this box. This fixed-departure-stow projection assumes no future loads and does not construct an executable future policy.',
                f'ประมาณการที่ {box.pod} เที่ยวแวะ {call}: ทั้งแผนมีตู้ยกพัก {count.restowed_boxes} ตู้, ยกพัก {count.restow_lifts} ครั้ง และยกฝาระวาง {count.hatch_cover_lifts} ครั้ง. {box_id} มีตู้ด้านล่างในแนวเดียวกันที่มี POD ก่อนหน้า: {earlier_th}; ตู้ด้านบนในแนวเดียวกันที่มี POD ภายหลัง: {later_th}. จำนวนรวมตู้ที่กีดขวางฝาระวางและกีดขวางทางอ้อมด้วย; ไม่ได้ระบุว่าเกิดจากตู้นี้เพียงตู้เดียว. ประมาณการนี้ยึดตำแหน่งตู้เมื่อออกเรือ สมมติว่าไม่มีตู้บรรทุกเพิ่ม และไม่ได้สร้างแผนการทำงานในอนาคตที่ปฏิบัติได้จริง.',
                [ref('report', f'/count/future/{future_index}'), ref('instance', '/rotation'),
                 ref('instance', '/call_index'), *order_refs],
                f'the whole-plan projection at {box.pod} is {count.restowed_boxes} restowed boxes, {count.restow_lifts} restow lifts and {count.hatch_cover_lifts} cover lifts, under the fixed-departure-stow assumption with no future loads',
                f'ประมาณการทั้งแผนที่ {box.pod} คือตู้ยกพัก {count.restowed_boxes} ตู้ ยกพัก {count.restow_lifts} ครั้ง และยกฝาระวาง {count.hatch_cover_lifts} ครั้ง ภายใต้สมมติฐานยึดตำแหน่งตู้เมื่อออกเรือและไม่มีตู้บรรทุกเพิ่ม')
    return out


def _dg_facts(add, instance, report, box, ship, cells, stacks, ref, all_refs):
    from stowage.checker.rules_imdg import _doc_areas, _doc_permissions

    candidates = set()
    for cell in cells.values():
        address = parse_slot(cell.id)
        slots = [cell.id] if box.length_ft == 20 else [format_slot(address.bay + d, address.row, address.tier)
                                                    for d in (-1, 1) if 0 < address.bay + d <= 998]
        for slot in slots:
            footprint = cells_of_slot(slot)
            if all(cid in cells and getattr(cells[cid], f'accepts_{box.length_ft}') for cid in footprint):
                candidates.add(slot)
    areas = _doc_areas(instance)
    allowed = [slot for slot in sorted(candidates)
               if not _doc_permissions(instance, box, slot, instance.call_index, cells, stacks, areas)]
    add('dg_allowed',
        f'{box.id} has {len(allowed)} of {len(candidates)} length-compatible cell footprints permitted by declared DG Document of Compliance permissions and geometry. Occupancy, support, stowage provisions, segregation and full legality are not screened by this count.',
        f'{box.id} มีตำแหน่งชุดเซลล์ที่รองรับความยาวและได้รับอนุญาตตามเอกสาร DG Document of Compliance ที่ประกาศกับรูปทรงเรือ {len(allowed)} จาก {len(candidates)} ตำแหน่ง. จำนวนนี้ยังไม่ได้ตรวจสอบการครอบครอง ฐานรองรับ ข้อกำหนดการจัดเก็บ การแยกสินค้า หรือความถูกต้องตามกฎทั้งหมด.',
        [ref('instance', '/vessel/cells'), ref('instance', '/vessel/dg_doc_areas'),
         ref('instance', '/rule_overrides'), *all_refs],
        f'{len(allowed)} of {len(candidates)} length-compatible cell footprints meet declared DG Document of Compliance permissions and geometry, without a full legality check',
        f'ชุดเซลล์ที่รองรับความยาวนี้ {len(allowed)} จาก {len(candidates)} ตำแหน่งตรงตามสิทธิ์ DG Document of Compliance ที่ประกาศและรูปทรงเรือ โดยยังไม่ได้ตรวจสอบความถูกต้องตามกฎทั้งหมด')
    part = report.parts.get('rules')
    ids = ('imdg.segregation.7_2_4', 'imdg.segregation.7_4_3')
    failed = {v.rule_id for v in report.violations}
    if part is not None:
        failed.update(v.rule_id for v in part.violations)
    complete = (part is not None and part.ran and not part.error
                and part.metrics.get('n_checked_boxes') == len(ship.placements)
                and report.passed and not any(rule in failed for rule in (*ids, 'checker.missing_vessel_data')))
    rules = ', '.join(ids)
    if complete:
        en = f'DG segregation rules {rules}: the rules checker ran over this departure plan and reported no violation of these rules. This is a plan-scope pass, not a measured box-specific distance.'
        th = f'กฎการแยก DG {rules}: ตัวตรวจสอบกฎทำงานกับแผนเมื่อออกเรือนี้และไม่รายงานการละเมิดกฎเหล่านี้. นี่เป็นผลผ่านในระดับแผน ไม่ใช่ระยะห่างเฉพาะตู้ที่วัดได้.'
    else:
        en = f'DG segregation rules {rules}: the supplied report does not establish a completed plan-scope pass of these rules.'
        th = f'กฎการแยก DG {rules}: รายงานที่ให้มายังยืนยันไม่ได้ว่าตรวจสอบกฎเหล่านี้ครบและผ่านในระดับแผน.'
    rule_refs = [ref('report', '/parts/rules/' + key) for key in ('ran', 'error', 'metrics', 'violations')] if part else []
    add('dg_rules', en, th, [*rule_refs, ref('report', '/passed'), ref('report', '/violations')],
        en.rstrip('.'), th.rstrip('.'))
