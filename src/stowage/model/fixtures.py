"""Small synthetic model fixtures and a fresh-instance builder for downstream tests.

These establish data contracts, not checker-approved loading or hydrostatic examples.
All hull/strength/tank numbers are APPROXIMATION: fixture placeholder pending 1.6/2.1.
"""

from __future__ import annotations

import math

from stowage.conventions import SYNTHETIC_OWNER_CODES, format_slot, make_container_id, stack_id
from stowage.model import Instance
from stowage.versions import component_versions


def build_instance(*, layover: bool = False, with_dg_doc: bool = False) -> Instance:
    """Return an independent, deterministic THLCH feeder-slice instance (stage A or B).

    The layover has a current-call discharge below a through box, allowing downstream
    move-engine tests to exercise temporary restow and hatch-cover access.
    Set ``with_dg_doc=True`` for populated synthetic DoC areas. The default leaves
    structured DoC unavailable so geometry tests can select stacks without also
    rewriting area memberships. This does not grant unrestricted DG permission.
    """
    ids = [make_container_id(SYNTHETIC_OWNER_CODES[0], serial) for serial in range(1, 7)]
    cells, stacks, covers = [], [], []
    for row, y in ((1, -1.3), (2, 1.3)):
        cover = f"HC-02-{row}"
        for bay, x in ((1, 46.9), (3, 53.1)):
            for deck, tiers, base in ((False, (2, 4), 2.0), (True, (82, 84), 9.0)):
                sid = stack_id(bay, row, deck)
                cids = [format_slot(bay, row, tier) for tier in tiers]
                cells.extend(dict(id=cid, stack_id=sid, accepts_45=True, reefer_plug=deck,
                                  lcg_m=x, tcg_m=y, vcg_base_m=base + i * 2.591)
                             for i, cid in enumerate(cids))
                stacks.append(dict(id=sid, bay=bay, row=row, on_deck=deck, cell_ids=cids,
                                   max_mass_kg=60000, max_height_m=6.0, base_z_m=base,
                                   hatch_cover_id=cover, dg_classes_allowed=["3", "9"]))
                stacks[-1]["dg_doc_area_id"] = ("WEATHER-DECK" if deck else "HOLD-01") if with_dg_doc else None
        covers.append(dict(id=cover, bays=[1, 2, 3],
                           hold_stack_ids=[stack_id(b, row, False) for b in (1, 3)],
                           deck_stack_ids=[stack_id(b, row, True) for b in (1, 3)],
                           mass_kg=12000, lcg_m=50, tcg_m=y, vcg_m=8.8))

    # APPROXIMATION: fixture placeholder, not a derived hull or certified loading manual.
    hull = dict(lpp_m=120, loa_m=128, beam_m=20, depth_m=11, design_draught_m=6,
                scantling_draught_m=7, fp_x_m=120, lightship_mass_kg=4000000,
                lightship_lcg_m=60, lightship_vcg_m=6,
                lightship_distribution=[(0, 60, 2000000), (60, 120, 2000000)],
                hydrostatics=[dict(draught_m=d, displacement_kg=m, kb_m=d / 2, kmt_m=10,
                                   lcb_m=60, lcf_m=60, tpc_kg_per_cm=18000, mct_nm_per_cm=2e6)
                              for d, m in ((4, 6000000), (6, 9600000), (7, 11400000))],
                cross_curves=[dict(heel_rad=math.radians(a),
                                   displacement_kg=[6000000, 9600000, 11400000],
                                   kn_m=[10 * math.sin(math.radians(a))] * 3)
                              for a in (10, 20, 30, 40, 50, 60)],
                stations=[dict(x_m=x, max_shear_n=1e8, min_shear_n=-1e8,
                               max_bending_nm=1e9, min_bending_nm=-1e9) for x in (0, 60, 120)],
                windage_area_m2=1200, windage_lever_m=8, max_draught_m=7,
                min_draught_fwd_m=3, max_trim_m=2, max_list_rad=math.radians(3), min_gm_m=0.3)
    tanks = []
    for name, kind, y, adjustable in (("WB-P", "ballast", 5, True),
                                      ("WB-S", "ballast", -5, True),
                                      ("FO-1", "fuel_oil", 0, False)):
        tanks.append(dict(id=name, kind=kind, content_density_kg_m3=1025 if adjustable else 950,
                          capacity_m3=100, heated=not adjustable, x_aft_m=10, x_fore_m=20,
                          adjustable=adjustable,
                          table=[dict(volume_m3=v, lcg_m=15, tcg_m=y, vcg_m=1 + v / 100,
                                      fsm_m4=50 if v == 50 else 0) for v in (0, 50, 100)]))
    # APPROXIMATION: fixture placeholder; these are synthetic DoC permissions/equipment premises, not approval.
    dg_doc_areas = [dict(
        id=area_id, kind=kind, stack_ids=[s["id"] for s in stacks if s["on_deck"] == deck],
        classes_allowed=["3", "9"], packing_forms_allowed=["packaged_goods_in_closed_freight_containers"],
        equipment_assumptions="APPROXIMATION: fixture placeholder; synthetic fire protection and response equipment",
        assumption="APPROXIMATION: fixture placeholder; synthetic DG authorization, no certified DoC",
        sources=["synthetic model fixture"], provenance=dict(created_by="model.fixtures"))
        for area_id, kind, deck in (("HOLD-01", "hold", False), ("WEATHER-DECK", "deck", True))]
    vessel = dict(id="synthetic_feeder.slice_b01-b03", name="Synthetic feeder geometry slice",
                  nominal_teu=len(cells), cells=cells, stacks=stacks, hatch_covers=covers,
                  tanks=tanks, dg_doc_areas=dg_doc_areas if with_dg_doc else [], heat_sources=[dict(id="FO-1", kind="heated_tank", x_min_m=10,
                                                x_max_m=20, y_min_m=-2, y_max_m=2,
                                                z_min_m=0, z_max_m=3)],
                  hull=hull, bridge=dict(x_m=15, eye_z_m=25, bow_x_m=128),
                  # APPROXIMATION: fixture placeholder background cargo block; not a derived load condition.
                  slice_of="synthetic_feeder", fixed_loads=[dict(x_m=80, y_m=0, z_m=4, mass_kg=200000,
                      kind="cargo", silhouette=dict(x_min_m=77, x_max_m=83, y_min_m=-1.3, y_max_m=1.3,
                                                    z_min_m=2, z_max_m=6))],
                  sources=["APPROXIMATION: fixture placeholder; synthetic hull, tank, strength and slice loads"])
    blocks = [dict(id=b, n_bays=6, n_rows=2, max_tiers=3, equipment_id="rtg", truck_lane_side="low",
                   reefer_rows=[1], dg_block=b == "B", x_m=x, y_m=0)
              for b, x in (("A", 0), ("B", 50))]
    port = dict(locode="THLCH", archetype="laem_chabang_like",
                yard=dict(id="synthetic_thlch", blocks=blocks),
                equipment=[dict(id="rtg", kind="RTG", stack_tiers=3, count=2)],
                # APPROXIMATION: legacy geometry slice retains independent yard3/quay2, with no unified H.
                max_crane_height_tiers=None, berthing_side="port",
                yard_max_tiers=3, quay_crane=dict(count=1, max_on_deck_tiers=2,
                                                max_lift_height_m=30, outreach_rows=2),
                # synthetic test berth, 8.0 m; not a production bound
                max_draught_m=8, sources=["synthetic fixture; no actual terminal operating limits",
                                         "synthetic test berth, 8.0 m; not a production bound"])
    containers = []
    for i, cid in enumerate(ids[:6 if layover else 4]):
        containers.append(dict(id=cid, iso_type="45R1" if i == 0 else "22G1",
                               length_ft=40 if i == 0 else 20,
                               height_m=2.896 if i == 0 else 2.591,
                               tare_kg=4200 if i == 0 else 2200,
                               gross_mass_kg=20000 if i == 0 else 12000,
                               pol="THBKK" if i >= 4 else "THLCH",
                               pod="THLCH" if i == 4 else "THSGZ" if i in (1, 3) else "THBKK",
                               reefer=i == 0, reefer_setpoint_c=-18 if i == 0 else None,
                               dg=dict(un_number="1263", imdg_class="3", packing_group="III",
                                       stowage_category="A", flashpoint_c=35,
                                       packing_form="packaged_goods_in_closed_freight_containers", ctu_closed=True,
                                       shipment_provenance=dict(created_by="model.fixtures")) if i == 1 else None,
                               special_stowage=["away_from_heat", "under_deck"] if i == 2 else [],
                               oog=dict(over_height_m=0.4) if i == 3 else None))
    yard_stacks = [dict(key=key, length_ft=length, boxes=[cid]) for key, length, cid in
                   (("A-001-01", 40, ids[0]), ("B-001-01", 20, ids[1]),
                    ("A-003-01", 20, ids[2]), ("A-004-01", 20, ids[3]))]
    return Instance.model_validate(dict(
        id="fixture_layover" if layover else "fixture_empty_ship", stage="B" if layover else "A",
        size_class="tiny", family={"vessel": "synthetic_feeder", "port": "laem_chabang_like"}, seed=11,
        vessel=vessel, port=port, call_index=0,
        rotation=dict(service="synthetic_thai_coastal", calls=[
            dict(locode=locode, index=i, max_on_deck_tiers=2, eta_s=i * 86400)
            for i, locode in enumerate(("THLCH", "THBKK", "THSGZ"))]),
        yard=dict(yard_id="synthetic_thlch", stacks=yard_stacks),
        onboard=dict(vessel_id=vessel["id"],
                     placements=[dict(slot="0010102", container_id=ids[4]),
                                 dict(slot="0010104", container_id=ids[5])] if layover else [],
                     tank_masses_kg={"WB-P": 51250, "WB-S": 51250, "FO-1": 47500}),
        containers=containers, load_list=ids[:4], discharge_list=[ids[4]] if layover else [],
        provenance=dict(versions=component_versions(), seed=11, created_by="model.fixtures")))
