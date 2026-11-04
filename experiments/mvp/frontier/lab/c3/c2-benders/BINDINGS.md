# Accepted D185 witness: physical bindings

`random.medium.185100000.0.2`: **121/121 requested boxes loaded; 114 lifts; unchanged full checker passed with 0 violations in 23.23 s.**

Values below are extracted from the saved explicit voyage-phase reports. The accepted witness files were read only and their recorded hashes were checked.

| Phase | Solid GM (m) | KG (m) | FSC (m) | Stability GM (m) | Securing GM (m) | Roll period (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| departure | 1.701216 | 7.017374 | 0.106939 | 1.594277 | 1.410294 | 13.054422 |
| mid_voyage | 1.615375 | 7.052998 | 0.110722 | 1.504653 | 1.388702 | 13.155519 |
| arrival | 1.419827 | 7.207993 | 0.114782 | 1.305045 | 1.192439 | 14.196928 |

Stability GM equals solid GM minus FSC. Securing GM comes from its distinct native loading calculation, including actual tilted-liquid tangent free surfaces; these two GM values are not interchangeable.

| Phase | Aft draft (m) | Forward draft (m) | Draft limit (m) | Headroom (mm) | Nominal end freeboard (m) |
| --- | ---: | ---: | ---: | ---: | ---: |
| departure | 6.736532 | 7.022356 | 7.050000 | 27.644 | 2.177644 |
| mid_voyage | 6.449943 | 6.953118 | 7.050000 | 96.882 | 2.246882 |
| arrival | 6.196584 | 6.834420 | 7.050000 | 215.580 | 2.365580 |

Nominal end freeboard here is hull depth minus the larger end draft. It is a geometric summary, not a regulatory load-line freeboard calculation.

| Phase | Total GZ area (m rad) | Required area (m rad) | Area margin (m rad) | Maximum rod ratio | Rod headroom (%) |
| --- | ---: | ---: | ---: | ---: | ---: |
| departure | 0.297864197 | 0.275428498 | 0.022435698 | 0.990776504 | 0.922350 |
| mid_voyage | 0.313754287 | 0.281488039 | 0.032266248 | 0.987415084 | 1.258492 |
| arrival | 0.291164308 | 0.291006173 | 0.000158135 | 0.955614766 | 4.438523 |

Required total area is recovered exactly from measured area minus the checker’s signed margin; it includes the phase-specific containership form factor and applicable tightening.

Reproduce this JSON-only extraction with `summarize_bindings.py`. Machine-readable values and witness hashes: `results/physical-bindings.json`.
