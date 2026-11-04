"""Summarise search_*.json into markdown tables (witness per condition + A 2.2 vs B 2.3 over all candidates)."""
import json, glob
rows, ab = [], []
for f in sorted(glob.glob("search_feeder_*_C*.json")):
    d = json.load(open(f))
    for c in d["candidates"]:
        if "a2_2_passed" in c:
            ab.append((d["ship"], d["condition"], c["label"], c["gm"], c["passed"], tuple(c["a2_2_passed"]), tuple(c["b2_3_passed"]),
                       c["a2_2"]["A2.2.3 max GZ angle >= 25 deg"]["value"],
                       [k[len("containership_"):] for k, x in c["b2_3"].items() if x["margin"] < -1e-12],
                       [k.split(" ")[0] + " " + k.split(" ")[1] for k, x in c["a2_2"].items() if not x["passed"]]))
    w = d.get("witness")
    rows.append((d["ship"], d["condition"], w, d.get("finisher_on_witness_condition"), d.get("ballast_capacity_t"), len(d["candidates"])))
print("## witnesses")
for s, c, w, fin, cap, n in rows:
    if not w:
        print(s, c, "NO WITNESS after", n, "candidates"); continue
    st = w.get("strength", {}); la = w.get("lashing", {}); vi = w.get("visibility", {})
    print(f"| {s} | {c} | {w['label']} {w['ballast_t']:.0f} t / {cap:.0f} | {w['boxes']} ({w['boxes_hold']}/{w['boxes_deck']}) {w['cargo_t']:.0f} t"
          f" | {w['gm']:.2f} | {w['ta']:.3f}/{w['tf']:.3f}/{w['trim']:.2f}/{w['list_deg']:.2f} | phi_f {w['phi_f_deg']:.1f} wx {w['weather_ratio']:.2f}"
          f" | A {w['a2_2_passed']} B {w['b2_3_passed']} | SF {max(st.get('sf_pos_ratio',0), st.get('sf_neg_ratio',0)):.2f} hog {st.get('bm_hog_ratio',0):.2f} sag {st.get('bm_sag_ratio',0):.2f}"
          f" | {fin['ballast_status'] if fin else None} {fin['wall_s'] if fin else 0:.0f}s | {w['check_s']:.1f}s |")
    print("    lashing:", {k: round(x, 3) for k, x in la.items()}); print("    visibility:", {k: round(x, 1) for k, x in vi.items()})
print("## A vs B over all candidates")
for r in ab:
    print(r[0], r[1], r[2], f"GM {r[3]:.2f}", "PASS" if r[4] else "", "A", r[5], "B", r[6], f"peak {r[7]:.1f}", "Bfail", r[8], "Afail", r[9])
