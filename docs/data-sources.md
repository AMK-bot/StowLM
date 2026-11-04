# Data sources — what's public for building our ships, cargo and rotations (researched 26 Oct 2026)

Hard rule still applies: public aggregates, standards and open datasets only; no customer data; nothing behind a login.
Real ships and services are used **as ranges and patterns, never copied as one identifiable ship or service**.
Key: **V** = opened/queried this session (spot-checked by the main session where marked ✓), **S** = search snippet only.

## Source and API index

| # | Source | Endpoint / URL | Access | Licence | Used for | Status |
|---|---|---|---|---|---|---|
| 1 | Larsen–Pacino stowage benchmark (vessels + load lists) | https://github.com/maritimelab/Stowage-Planning-Benckmark | Git clone | CC0 | Vessel schema; large ships; container mix prior | V ✓ |
| 2 | DTU MasterPlanning benchmark | `https://api.figshare.com/v2/articles/22293412` (1.1 MB zip) | Free download | GPL-3.0+ | Large-ship yardstick vs published MIP | V ✓ |
| 3 | IACS UR S11A (strength) | https://rs-class.org/upload/iblock/555/28054d54be595bcead7c0100f106d86f.pdf (official: iacs.org.uk) | Free PDF | — | SF/BM limits | V |
| 4 | KCS hull | https://t2015.nmri.go.jp/kcs_gc.html | Free download | Unstated | Hydrostatics check (~3,600 TEU) | V |
| 5 | DTC hull | OpenFOAM-dev `tutorials/resources/geometry/DTC-scaled.stl.gz` | Free | GPL-3 | Hydrostatics check (~14,000 TEU) | V |
| 6 | Ship-D parametric hulls | https://github.com/Vykonn/ShipD | Free | None stated → equations only | Hull generator | V |
| 7 | Feeder particulars (Damen 800, Conoship 350, Reederei Nord Topaz/SDARI, Bangkokmax class) | see §1 | Free web/PDF | Figures only | Feeder dimensions by size | V |
| 8 | Wärtsilä encyclopedia (stack load) | wartsila.com encyclopedia | Free | — | Stack weight limits | V |
| 9 | SMDG BAPLIE 2.2.1 / 3.1.1 | https://smdg.org (ship-planning documents) | Free PDF | Unstated | Output format | V |
| 10 | US Hazardous Materials Table 49 CFR 172.101 | `https://www.ecfr.gov/api/versioner/v1/full/{date}/title-49.xml?part=172&section=172.101` (`--compressed`) | API, no key | Public domain | Machine-readable bulk DG list; IMDG 3.2 is the authority (D10) | V ✓ |
| 11 | US vessel segregation 49 CFR 176.83 | same API, `part=176&section=176.83` | API, no key | Public domain | Superseded by IMDG 42-24 (D10); cross-check only | V ✓ |
| 12 | PubChem (CAMEO UN numbers + synonyms) | `https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/annotations/heading/UN%20Number/JSON`, `/rest/pug/compound/name/{name}/synonyms/JSON` | API, no key | Public | Hidden-DG wording | V ✓ |
| 13 | UN Comtrade (HS list + flows) | `https://comtradeapi.un.org/files/v1/app/reference/HS.json`; `public/v1/preview/...` | API; full queries need free key | UN terms | Commodities, port-pair mix | V ✓ |
| 14 | DCSA Booking 2.0.0 / eBL-SI 3.0.0 | `https://api.swaggerhub.com/apis/dcsaorg/DCSA_BKG/2.0.0`, `/DCSA_EBL/3.0.0` | Free, no login | — | Booking LLM output schema | V |
| 15 | UN/LOCODE | unlocode.unece.org; GitHub `datasets/un-locode` CSV | Free | CC BY 4.0 | Port codes | V |
| 16 | SMDG terminal code list | GitHub `smdg-org/Terminal-Code-List` | Free | Unstated | Terminal codes | V |
| 17 | Laem Chabang monthly calls/tonnes/TEU (PAT via MOT) | https://datagov.mot.go.th/en/dataset/s6-5-2; CKAN `api/3/action/package_show?id=s6-5-2` | API, no key | Open Data Common | Volumes, seasonality | V ✓ |
| 18 | Singapore container throughput (MPA) | `https://data.gov.sg/api/action/datastore_search?resource_id=d_da030f7028200d19ffcbe4a2d71af39c` | API, no key | SG Open Data Licence | Volumes, impact | V ✓ |
| 19 | Singapore vessel arrivals by type (MPA) | same API, `resource_id=d_8f264219109e61fffa87ac64dd5a9a65` | API, no key | SG Open Data Licence | Ship-size proxy | V ✓ |
| 20 | RCL service patterns | e.g. https://rclgroup.com/pdf/SvcPtrn/20250331-RMB8.PDF | Free PDF | — | Rotation patterns (not copied) | V |
| 21 | UNCTADstat (LSCI, port calls, time in port) | https://unctadstat.unctad.org/insights/theme/45 | Free download | UN terms | Impact, context | V (page) |
| 22 | Port Klang 2025 throughput | Malay Mail / PKA presentation | Web | — | Transhipment share | S |
| 23 | Hong Kong C&SD port statistics | info.gov.hk releases | Web | — | Empty-container share | S |
| 24 | aisstream.io | WebSocket API | Free key (account) | Unstated | Optional live demo | V (docs) |
| 25 | IMO resolutions: IS Code MSC.267(85), IMDG 42-24 MSC.556(108), SOLAS V MSC.99(73), VGM MSC.380(94) | wwwcdn.imo.org (§4) | Free PDF | IMO | Rules the checker encodes | V (HTTP 200) |
| 26 | IACS UR S11A, S1, L5, Rec. 48 | iacs.s3.af-south-1.amazonaws.com (§4) | Free PDF | IACS | Strength limits, loading-instrument contract, test tolerances | V (HTTP 200) |
| 27 | Indian Register container-securing guidelines (2025) | irclass.org (§4) | Free PDF | — | Published lashing method | V (HTTP 200) |
| 28 | Accident reports: Ever Smart, MOL Comfort, MSC Napoli, Annabella | gov.uk MAIB / Bahamas MA (§5) | Free PDF | MAIB: OGL v3 | Checker validation cases | V (HTTP 200) |
| 29 | Reference ships: Arctic Trader, Dina Trader, Vega Tau, 1,700 TEU Bangkokmax, KEH Bangkok-Max, MV Bardu | owner/broker/designer pages (§6) | Free | None stated → numbers only | Synthetic ship anchors | V (HTTP 200) |
| — | Ruled out | Equasis, IMO GISIS (login); DCSA carrier schedule APIs (customer access); US/Danish AIS (wrong region) | | | | |
| 30 | k-shift (Roberti–Pacino, Parreño-Torres) | https://data.dtu.dk/articles/dataset/Container_Stowage_Planning_-_k-shift_instances_-_Roberti-Parenno/22284475 | Free download | GPL-3.0+ | Multi-port rehandle validation; kept out of the repo | V |
| 31 | Junqueira integrated instances | https://github.com/JunqueiraCatarina/Instances | Git clone | None stated | Yard + ship validation; kept out of the repo | V |

## 1. Ships by size

| Size | Main dimensions (public anchors) | Layout / limits | Sources |
|---|---|---|---|
| **~500 TEU** | ~110–120 m × 18–19 m, T ~6.5 m — **interpolated** between Conoship 350 TEU (100 m × 15.9 m, T 5.65 m) and Damen 800 (140.6 m × 21.8 m, T 7.33 m, 788 TEU, 90 plugs) | Scaled from Larsen–Pacino schema | Conoship [V], Damen spec sheet [V, figures only — "no reproduction"] |
| **~800 TEU** | LOA 134.44 m, Lpp 124.41 m, B 22.50 m, design T 8.70 m; synthetic middle feeder has 816 physical TEU cells (D33) | Scaled KCS hull with type-168 dimensions (§6); nominal class 800 TEU | Dina Trader / Annabella [V, §6]; Damen and RCL 1,018 TEU ships remain larger comparison points |
| **~1,700 TEU ("Bangkokmax")** | LOA ≤ 172 m, B ~28 m, T 9.5 m scantling / 8.2 m Bangkok Port limit; ~1,380 TEU at 14 t homogeneous; ~350 plugs | Scaled | Alphaliner via Maritime Gateway (196 Bangkokmaxes in intra-Asia) [V]; Reederei Nord Topaz 1700 / SDARI class [V] |
| **~7k / 10k / 15k TEU** | Use as-is | Full per-bay/stack/cell data | Larsen–Pacino vessels S/M/L [V ✓] |
| Hull validation | KCS (~3,600 TEU: Lpp 230 m, B 32.2 m, T 10.8 m, C_B 0.6505) and DTC (~14,000 TEU) | — | KCS offsets/IGES [V]; DTC STL in OpenFOAM tutorials (GPL-3) [V] |

**Core vessel sources**
- **Larsen & Pacino benchmark vessels** — https://github.com/maritimelab/Stowage-Planning-Benckmark — **CC0** [V ✓].
  (The DTU figshare copy, article 11913324, is CC BY-NC-ND 4.0 [V ✓] — use the GitHub copy; cite Larsen & Pacino, *Maritime
  Economics & Logistics* 2020.) Plain text per vessel: per-bay LCG, shear/bending limits, lightship weight + VCG, buoyancy
  per draught; hydrostatic table (displacement → LCG limits, KM); tanks (capacity, CGs, bay coverage); per-stack TCG, max
  height, max weight 20'/40' (median ~108 t / 161 t), VCG; per-cell reefer flag. **This is the schema our `Vessel` model
  copies.** SF/BM units undocumented — cross-check against IACS S11A magnitudes.
- **DTU MasterPlanning benchmark** — figshare 22293412, **GPL-3.0+**, 1.1 MB [V ✓]. 162 instances on the same 3 vessels
  (5/7/10 ports × 0/15/30 % on board × 60/70/80 % utilisation) with published MIP results (optimal for S/M, 2–5 % gap on L
  at 1 h; arXiv 2307.07573). **Our large-ship yardstick** — resolves the Day 1 "DTU benchmark" item.
- **IACS UR S11A** (container-ship longitudinal strength) — wave BM/SF formulas; valid for L 90–500 m, so all our sizes
  (500 TEU near the lower bound). Third-party copy [V]: https://rs-class.org/upload/iblock/555/28054d54be595bcead7c0100f106d86f.pdf ;
  official local copy: `data/raw/iacs/ur-s11a.pdf`. Wave loads are one input to strength design, not vessel-specific
  allowable still-water SF/BM envelopes. S11A §2.2.2 (PDF p8) requires envelopes covering loading-manual conditions
  and designer extremes. Vessel structure, design loading conditions and class-approved limits are also needed;
  synthetic limits must retain their structural assumptions and approximation provenance (D22/D27).
- **Ship-D parametric hulls** (MIT DeCoDE) — https://github.com/Vykonn/ShipD [V README]: 45-parameter hull equations,
  feasibility constraints, 30k hulls with hydrostatics at 10 draughts. **No licence stated** — use the published
  equations (paper), not the dataset, unless clarified.
- **Wärtsilä encyclopedia** — hatch-cover stack limits: Panamax 90 t / 120 t (20'/40'), post-Panamax 100 / 140 t [V].
- SMDG **BAPLIE 2.2.1 / 3.1.1** message guides (free PDFs) for output format realism [V].
- Not usable: Equasis, IMO GISIS (login). DNV Vessel Register / DNV rules: unverified.

## 2. Cargo, dangerous goods, bookings

- **Container-list prior: Larsen–Pacino load lists** (27 lists, 123,431 containers) [V]: 33 % 20' / 67 % 40'; 52 % dry,
  43 % high-cube, 4.6 % reefer; weight classes 3 t 8.5 %, 6 t 10 %, 9 t 13 %, 14 t 12 %, 21 t 34 %, 27 t 23 %.
  Deep-sea, not feeder — adjust: **empties ~20 %** (Hong Kong C&SD 2022: 3.37M of 16.7M TEU) [S], reefer ~5 %,
  DG ~5–10 % (weak source).
- **DG list, machine-readable backup: 49 CFR 172.101 Hazardous Materials Table** via eCFR API (public domain) [V];
  entries we use are checked against IMDG 42-24 Ch. 3.2 (§4, D10):
  `https://www.ecfr.gov/api/versioner/v1/full/{date}/title-49.xml?part=172&section=172.101` — needs an
  `Accept-Encoding` that allows compression [✓]. Proper shipping name, class, UN no., PG, vessel stowage columns 10A/10B.
- **Segregation: IMDG 42-24** (§4, D10) — the rule in force. 49 CFR 176.83 (same eCFR API) [V ✓] is kept only as a
  cross-check: general table by class, container-ship table 176.83(f), hatchless table 176.83(l).
- **Hidden-DG wording: PubChem** (free, no key) [V ✓]. UN-number annotations (CAMEO Chemicals) via
  `rest/pug_view/annotations/heading/UN%20Number/JSON`; synonyms per compound. Calcium hypochlorite → "Bleaching powder",
  "Chlorine of lime", "HTH (bleaching agent)", "Pittchlor"… — **exactly the disguise the booking LLM must catch.**
  Test split held out by UN number (`docs/evaluation.md`).
- **Misdeclaration context**: WSC 2025 10.36 % deficient of 93,079 risk-targeted inspections (overstates true rate);
  NCB 5–8 % of DG containers misdeclared; ~25 % of serious incidents trace to misdeclaration (CINS) [S].
  **No public split of declared DG by IMDG class** — our class mix is an assumption.
- **Commodities: UN Comtrade** [V ✓] — `comtradeapi.un.org/files/v1/app/reference/HS.json` = 8,262 HS codes, no key;
  keyless `public/v1/preview` for flows (e.g. Thailand→Singapore 2024 top HS2: 85, 71, 27, 84, 89). Full API needs a free
  key. No sea-mode split.
- **Booking schema: DCSA Booking 2.0.0 and eBL/SI 3.0.0** (SwaggerHub, no login) [V] — `DangerousGoods`,
  `RequestedEquipment`, `Commodity`, `ActiveReeferSettings`… → the booking LLM's output schema.
- **Codes**: UN/LOCODE (CC BY 4.0, GitHub CSV mirror) [V]; SMDG terminal code list (GitHub) [V]; ISO 6346 check digit
  (mod 11) and size-type codes from public descriptions [S]; UN/EDIFACT directories 403'd [S].

## 3. Rotations and port volumes

- **RCL service-pattern PDFs** [V] — e.g. https://rclgroup.com/pdf/SvcPtrn/20250331-RMB8.PDF : THLCH–SGSIN–MYPKG–INKAT–
  MYPKG–SGSIN–THLCH weekly, terminals and transit days. → rotation generator template, **pattern-inspired, not copied**.
- **Laem Chabang monthly calls / tonnes / TEU, 2018 → Aug 2026** — Ministry of Transport CKAN portal
  https://datagov.mot.go.th/en/dataset/s6-5-2 (XLS, Open Data Common); CKAN API `package_show` works [V ✓].
- **Singapore (MPA) on data.gov.sg** — CKAN `datastore_search`, no key [V ✓]: monthly container throughput in thousand TEU
  (`d_da030f7028200d19ffcbe4a2d71af39c`, 1995 → Sep 2026; Sep 2026 = 3,824.89k TEU) and monthly arrivals + GT by vessel type
  (`d_8f264219109e61fffa87ac64dd5a9a65`, "Container" row) → average container-ship size at Singapore.
- **Port Klang 2025**: 15.14M TEU, ~56 % transhipment (8.41M), 3.41M import, 3.32M export [S, Malay Mail/PKA].
  Malaysia open data has throughput by port only to 2019 (CC BY) [S].
- **UNCTADstat**: LSCI, port calls, median time in port (container ships ~0.7–0.8 days) [V theme page]. Thailand container
  port-call stats are suppressed ("–"); viewer is JavaScript, values need a manual download.
- AIS: aisstream.io (free key after account, live WebSocket only, no TEU field) — optional demo realism; US/Danish AIS
  useless for Asia. Carrier schedule APIs (DCSA) need customer access — out.

## 4. Rules and standards in force (reviewed 27 Oct 2026 — the checker encodes these)

Currency checked against what's in force in Oct 2026. **Open checks closed by plan 0.5 (27 Oct 2026; files and page
references in `data/raw/<source>/NOTES.md`):**
- IMDG 42-24 annex (MSC.556(108)) contains §7.2.4 (PDF p487), the container-ship segregation tables of 7.4.3
  (pp506–507) and the Dangerous Goods List (pp581–752). No D10 fallback needed.
- IACS revisions: **L5 Rev.4 (June 2020)**, **S1 Rev.7 (May 2010)**, **Rec. 48 Rev.1 (June 2020)**; **S11A (June 2015)**
  confirmed against the official 2024 volume; an independent 2026 currency confirmation is still caveated.
- IRS lashing accelerations: Main Rules Pt.3 Ch.5 §§2.2.3.1–3 (printed p9 / PDF p729) of the
  [July 2026 edition](https://www.irclass.org/media/8639/main_rules_july_2026-edition.pdf); the
  [September 2026 amendment](https://www.irclass.org/media/8771/mr-rcn-1-sept-2026.pdf) applies from January 2027.
- eCFR 49 CFR 172.101 is fetched as the **2026-10-23** snapshot (the API returned 404 for the current date).

| Rule | Status | What the checker encodes | URL |
|---|---|---|---|
| **IMO 2008 IS Code**, MSC.267(85), Part A 2.2 + 2.3 | Mandatory baseline; Administration may select the B 2.3 alternative to A 2.2 for eligible ships | A 2.2 baseline: GZ areas ≥ 0.055 m·rad to 30°, ≥ 0.09 to 40°/φf, ≥ 0.03 between 30°–40°/φf; GZ ≥ 0.20 m at ≥ 30°; max GZ at ≥ 25°; GM₀ ≥ 0.15 m. Weather criterion A 2.3: lw1 = P·A·Z/(1000·g·Δ), P = 504 Pa, lw2 = 1.5·lw1, φ0 ≤ 16° or 80 % of deck-edge immersion, area b ≥ area a, roll φ1 = 109·k·X1·X2·√(r·s). Local source: `data/raw/is-code/msc267-85.pdf`, PDF pp12–17 | https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.267(85).pdf (22.9 MB) |
| IS Code Part B 2.3 (containerships > 100 m) | Recommendatory alternative selected by the Administration under B 2.3.1; not a cumulative second set | Areas 0.009/C, 0.016/C, 0.006/C, 0.029/C; GZ ≥ 0.033/C at ≥ 30°; max GZ ≥ 0.042/C (C = form factor, 2.3.2.6). Local source: B 2.3.1–2.3.2, PDF pp25–27. **Project selection (D21):** checker uses B 2.3 instead of A 2.2.1–3 for its >100 m feeders, retaining A 2.2.4 GM and A 2.3 weather as a conservative project choice; this is not evidence of Administration approval | same |
| IS Code Part B 3.1 (required via A 2.1.2) | Required free-surface treatment | Correct GM/GZ for tanks <98% full; nominally full cargo tanks still require correction at 98% (B 3.1.1–2). Fixed-level tanks use actual filling; variable-level tanks use the maximum over envisaged filling limits, subject to 3.1.5–6 operating/transfer provisions (3.1.3). The greatest-effect transverse pair or single centreline tank per liquid type applies to **consumable liquids** (3.1.4). Ballast transfers account for the most onerous transitory stage (3.1.5). Local source: PDF p39; methods and small-tank exemptions continue on p40 | same |
| **IMDG Code Amendment 42-24**, MSC.556(108) | **Mandatory since 1 Jan 2026** (43-26 adopted May 2026: voluntary 2027, mandatory 1 Jan 2028) | DG list (3.2), segregation table (7.2.4), container-ship segregation (7.4.3), stowage categories | https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf (9.5 MB) |
| **IACS UR S11A** (containership longitudinal strength) | In force since 1 Jul 2016; no later revision found | Wave BM/SF design loads plus vessel-specific permissible still-water envelopes covering loading-manual and designer extremes (§2.2.2, local PDF p8), supported by structural capacity; not a size-only lookup. Applicability: L ≥ 90 m; 5 ≤ L/B ≤ 9, 2 ≤ B/T ≤ 6, 0.55 ≤ C_B ≤ 0.9; B > 32.26 m adds torsion/whipping. Re-read §2.3.2 before coding | https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/05/18115515/ur-s11a.pdf |
| **IACS UR S1** (loading manual + instrument) | In force (Rev.7, May 2010) | Approved loading instrument required for **Category I ships ≥100 m** (§S1.2.1, `data/raw/iacs/ur-s1rev7.pdf`, PDF p2), checking still-water SF/BM at read-out points (torsion/lateral loads where applicable). Classification is vessel-specific: §S1.1.2 p2 includes a Category II exception for ships <120 m whose design accounts for uneven cargo/ballast; other Category I grounds must also be checked. Project checker readouts follow this contract regardless of the synthetic ship's statutory classification | https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/05/18113352/ur-s1rev7.pdf |
| **IACS Rec. 48** (loading instruments) | Current | Read-out points at transverse bulkheads (+ mid-hold where specified); SF/BM/torsion accuracy ±5 % | https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/05/20100040/rec48rev1.pdf |
| **IACS UR L5** (stability software) | In force (Rev.4, June 2020) | **Checker test tolerances:** displacement ±2 %; KG, GMt ±1 % or 5 cm; GZ ±5 % or 5 cm; GZ area ±5 % or 0.0012 m·rad; free-surface moment ±2 %; draughts ±1 % or 5 cm; equilibrium angle ±1° | https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/02/17101147/ur-l5rev4.pdf |
| **SOLAS V/22** bridge visibility (+ MSC.1/Circ.1350/Rev.1 interpretation) | In force; ships **≥ 55 m** built on/after 1 Jul 1998 | Sea surface visible beyond min(2 L, 500 m) forward over ±10°; each blind sector ≤ 10° (≤ 5° within the forward view), total ≤ 20°, clear sectors ≥ 5° between | Text: https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.99(73).pdf ; 55 m threshold: https://www.irishstatutebook.ie/2021/en/si/0280.html , https://www.amsa.gov.au/about/regulations-and-standards/marine-notice-14-2022-ensuring-navigation-bridge-visibility ; interpretation: https://www.traficom.fi/files/media/regulation/22703-MSC_1-Circ_1350_rev_1_-_Tulkinta_SOLAS_V_22_1_6_Rev_1.pdf |
| **SOLAS VI/2** verified gross mass, MSC.380(94) + MSC.1/Circ.1475 | In force since 2016 | Declared container weights treated as exact (VGM before stowage); basis for the misdeclared-weight scenario | https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/resolution%20MSC-380-94.pdf |
| **Indian Register "Guidelines on Container Securing"** (Jul 2025, IACS member) | Current class guidance | Published lashing method: wind 15.7/6.4 kN (20') and 31.6/6.4 kN (40'); end stiffness 15.7 (closed) / 3.7 (door) kN/mm; limits: transverse racking 150 kN, longitudinal racking 75 kN, corner-casting tension 250 kN, corner-post compression 848 kN, lashing pull 150 kN horizontal / 225 kN vertical, fittings ≤ 85 % SWL. Accelerations: IRS Main Rules Pt.3 Ch.5 §§2.2.3.1–3 (fetched, plan 0.5) | https://www.irclass.org/media/7895/guidelines-on-container-securing_july-2025_new.pdf |
| Port limits | Current (terminal/port pages) | Max draught per call: Laem Chabang APM berths B1 15 m, B0/A0 14 m; Bangkok Port LOA ≤ 172 m, draught ≤ 8.2 m (S); Westports up to 18 m (S); Singapore PSA (S) | https://www.apmterminals.com/en/laem-chabang/about/our-terminal ; https://www.westportsholdings.com/wp-content/uploads/2022/06/Westport-Corporate-Brochure-2024-PDF.pdf ; https://www.globalpsa.com/wp-content/uploads/2026/04/PSA-SINGAPORE.pdf |

Stretch only: **second-generation intact stability criteria** (MSC.1/Circ.1627, interim, voluntary) — a level-1
parametric-roll vulnerability check per loading condition. Parametric roll is a leading cause of container losses.

Dropped (not plan-level, superseded, or paid): IACS UR S11 (S11A applies to containerships), S7 and S34 (design-stage),
CSS Code Annex 14 / MSC.1/Circ.1352 and MSC.1/Circ.1353 (crew access and securing-manual format), ISO 668/1496/6346
(paid — use only public facts: max gross mass, mod-11 check digit).

## 5. Validation cases (real conditions to test the checker against)

Built before UR S11A — their limits come from older class rules, so they validate magnitudes and output format, not
our S11A envelopes. Official accident reports; MAIB reports are under the Open Government Licence v3 (attribute).

| Case | Size | What it gives | URL |
|---|---|---|---|
| **Ever Smart** (MAIB 14/2020) | 7,024 TEU | Real stability-computer printout (Taipei departure: T 13.57 m, GM 0.949 m); CSM full-load GM 0.73/0.62 m; bay-70 stack-weight and lashing tables at GM 0.7 and 1.5 m; VGM vs actual weights | https://assets.publishing.service.gov.uk/media/5f15a2993a6f405c0f80ac37/2020-14-EverSmart-withAnnexes.pdf |
| **MOL Comfort** (Bahamas MA) | 8,110 TEU | Departure draughts F/M/A, GM 2.5 m, ballast/bunkers, BM 99 % and SF 94 % of permissible. Its loss led to UR S11A | https://www.bahamasmaritime.com/wp-content/uploads/2020/10/BMA-Investigation-Report-Loss-of-the-MOL-Comfort.pdf |
| **MSC Napoli** (MAIB 9/2008) | 4,419 TEU | Harbour/sea BM 88 %/116 % of limit (later 99 %); DNV/BV load and strength assessments; whipping study | https://assets.publishing.service.gov.uk/media/547c703ced915d4c0d000087/NapoliReport.pdf |
| **Annabella** (MAIB 21/2007) | 868 TEU | Local `data/raw/reference-ships/annabella.pdf` (+ page-numbered `.txt`, hashes/licence in MANIFEST): §1.5.3, printed p.16 / PDF p.22, gives holds 1–2: 96/136 t; 3–4: 150/240 t for 20′ or 30′/40′, based on GM 0.8 m. §1.5.1, printed pp.14–15 / PDF pp.20–21: plan valid up to GM 0.8 m. §2.5, printed p.25 / PDF p.31: at casualty GM 1.68 m, shipbuilder calculated hold 3 limit 90 t assuming lower-container lashing bars. No general GM-to-limit curve. §1.5.2 / Table 1, printed p.15 / PDF p.21: 11 departure GMs 0.94–1.88 m (narrative Antwerp GM 1.68 m versus table 1.83 m for 23 February; source discrepancy retained) | https://assets.publishing.service.gov.uk/media/547c7032e5274a429000007d/AnnabellaReport.pdf |

Pitch line: the checker enforces UR S11A — the rule written after MOL Comfort broke in two.

## 6. Reference ships for the synthetic fleet

Use their **geometry and capacities** as anchors and compute limits under current rules (S11A, IS Code), so our ships
are "new builds to today's rules". Owner/broker sheets carry no reuse licence — use numbers only, don't republish, and
don't name the ship in the demo ("a ~800 TEU feeder based on a public type-168 design").

| Our ship | Anchor | What it gives | URL |
|---|---|---|---|
| **~500 TEU** | Arctic Trader (532 TEU, Peterswerft 1996, owner brochure Feb 2026) | **Brochure figures, not current synthetic design values:** LOA 116.40, Lpp 107.80, B 19.2, D 8.25, T 7.06 m, DWT 3,850 t; 2 holds + hatch sizes; TEU hold/deck by 20'/40' + HC; stack limits hold 68 t, hatch 45/60 t; 92 plugs (36 hold / 56 deck); tanks (`data/raw/reference-ships/arctic-trader.pdf`, p1). **D27 supersedes depth/DWT for feeder_500 with 9.2 m / 6,832 t**; it identifies the brochure DWT as a misprint. See `experiments/physics-review/design/sourced-values.md` for secondary-source evidence and confidence labels; this inventory does not independently establish the real ship's corrected particulars | https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf |
| **~800 TEU** | Dina Trader (868 TEU, Sietas type 168, owner brochure) + Annabella (sister, §5) | Lpp 124.41, B 22.50, D 11.30, T 8.70 m, DWT 11,150 t, freeboard 2.609 m; 4 holds with dimensions; TEU closed-hold 77 / open-hold 524 / deck 267; tier heights per hold; 234 plugs; tanks (`data/raw/reference-ships/dina-trader.pdf`, p1). Annabella's stack limits are now locally traceable in `data/raw/reference-ships/annabella.pdf`, §1.5.3, printed p.16 / PDF p.22: holds 1–2 96/136 t and holds 3–4 150/240 t (20′ or 30′/40′), based on GM 0.8 m; §1.5.1 says valid up to 0.8 m. §2.5, printed p.25 / PDF p.31, gives 90 t for hold 3 at casualty GM 1.68 m with lower-container lashing bars. The source is MAIB, **not Dina's brochure**. These vessel-specific comparisons cannot supply synthetic-ship limits or a general GM-to-limit curve | https://mmred.no/images/Dina_Trader_Brochure-2022.pdf ; Annabella report linked in §5 |
| ~1,000 TEU cross-check (pre-D33 label; now checks the ~800 TEU ship from above) | Vega Tau (960 TEU, owner TC description) | LOA 139.10, Lpp 129.00, B 22.60, D 11.80, T 8.80 m; hold dims, hatch arrangement; ~620 TEU at 14 t; tank top 100/140 t + 12 t/m², hatches 60/90 t; 254 plugs by hold | https://www.vega-reederei.de/pdf/tc_VEGA_TAU.pdf |
| **~1,700 TEU** | 1,700 TEU Bangkokmax (NK, Japan 2010, sale listing) + KEH Bangkok-Max 2,000 design | LOA 171.99, B 27.6, D 14.0, T 9.52 m, DWT 21,413 t; 1,243 TEU at 14 t; 192 reefers; 5 holds. Design sheet: Bangkok draught 8.2 m vs max 10.5 m, deck/hold TEU split | https://www.eshiptrading.com/SS94778.html ; https://knudehansen.com/wp-content/uploads/2017/10/Product_Sheet_2000_TEU_Bangkok_Max_Container_Feeder_Vessel_11019.pdf |
| Hydrostatics check | MV Bardu (2,546 TEU, Swire TC description) | TPC 52.13 t/cm (tests waterplane area); homogeneous intake at 12/14/16/18 t; hatch-cover weights; stack loads | https://na.swireshipping.com/documents/pdfs/MV%20Bardu%20Specifications.pdf?rvn=27 |

## 7. Public move/yard benchmarks

Reviewed 27 Oct 2026. **BRP/CRP** retrieves containers from stacks with the fewest extra stack-to-stack moves;
**CPMP** sorts them before retrieval. A reported optimum is comparable only with the same variant, priority order,
height limit and objective. In particular, the Caserta author's 2017 correction enforces the `initial height + 2`
limit; earlier corridor-method results can differ. [Author correction](https://github.com/marcocaserta/BRP).

| Set and public source | Format / provenance | Published optimum evidence and use | Licence / access check |
|---|---|---|---|
| **RCSPPSuite**, 73 instances on 4 ships, 1,040–18,854 TEU, [public share](https://kilia.egnyte.com/fl/VfEPKOfYV2) | XML vessels, instances and results; anonymised real vessel and cargo data (Sivertsen, Reinhardt, Jensen 2025). Multi-port ship stowage, not a yard set | Example plans, not optima. Used to test the checker and a multi-port planner: `experiments/mvp/frontier/public/rcspp/VALIDATION.md` | No licence. Evaluation only (D197), in git-ignored `data/raw/rcsppsuite/` |
| **Caserta/CV BRP, 840 instances**, [Galle author repository](https://github.com/vgalle/binaryIP_CRP), `CRPTestcases_Caserta/`; original [Caserta archive](https://github.com/marcocaserta/BRP/blob/master/data.tar) | 21 classes × 40, from 9 to 100 boxes. Text `.dat`: stack/container counts, then per-stack count and priority list; see repository `Data Format.docx` and [reader](https://raw.githubusercontent.com/vgalle/binaryIP_CRP/master/readInstanceCaserta.jl). Reader reserves two extra tiers. | Galle's `Results/` supplies restricted-BRP computational results, including bounds/status; use only records whose lower and upper integer bounds coincide as proven targets. Caserta heuristic averages are not certificates for every instance. [Galle README](https://github.com/vgalle/binaryIP_CRP). | Public, no login. Neither repository's displayed root lists an explicit licence; **reuse permission unresolved**, including mirrored data. Do not infer a licence from GitHub visibility. |
| **Zhu distinct-priority BRP**, [Tanaka catalog](https://sites.google.com/site/shunjitanaka/brp), `Zhu_dataset.zip` | Locally inspected archive: 12,500 `.txt` files; header `stacks blocks`, then one row per stack: `height priorities...`. Example `3-10-27/10028.txt`: `10 27`, followed by ten stack rows. Dataset name/height rules must be retained. | Catalog references Zhu et al. (2012), Tanaka–Takii (2016), Tanaka–Mizuno (2018), and Jin–Tanaka (2023). These provide exact-method comparisons; **do not claim all 12,500 have known optima**. Join individual published proven results to exact filenames and variant before 4.10 scoring. | Public ZIP downloaded and tested, see local MANIFEST. Catalog's software-oriented Apache-2.0 notice does not explicitly establish third-party dataset rights; ZIP contains no licence. **Dataset licence unresolved**. |
| **TT16 grouped-priority BRP**, [Tanaka catalog](https://sites.google.com/site/shunjitanaka/brp), `dup_dataset.zip` | Derived from Zhu instances with duplicate priorities. This is the closest listed benchmark to interchangeable retrieval categories. Catalog documents the same stack-count / per-stack-list format. | Tanaka–Takii supplies a dedicated restricted solver for group priorities and published computational comparisons. Optima must be checked per instance, not borrowed from the distinct-ID source. | Public link exists; this session's direct download returned HTML rather than a ZIP (failed copy removed). Software Apache notice is not an explicit third-party data licence; **data rights and retrievable archive remain open**. |
| **Tricoire–Fechter–Beham larger BRP**, [Vienna author page](https://scm.univie.ac.at/research/blockrelocating/index.htm) | `large_instances.tar.gz`; separate Caserta and large-instance solution archives plus `solution_file_format.txt`. The latter documents result records. | Author releases solution data with *New insights on the block relocation problem*. A solution sequence is an upper bound; require the archive's proof/status field or matching published bound before labelling optimal. | Free links, no explicit dataset licence on the page. Archive fetch could not be verified with the browsing tool; **format and rights need follow-up before import**. |
| **CPMP: CV, BF, EMM, BZ, ZJY**, [Tanaka catalog](https://sites.google.com/site/shunjitanaka/pmp) | Catalog distinguishes original and regenerated copies: BF.zip; EMM regenerated by Tierney et al.; BZ regenerated by Tanaka–Tierney; original and converted ZJY. BF archive inspected locally: 721 files with `Label`, `Width`, `Height`, `Containers`, `Stack i : priorities...`; includes empty stacks. | [Tanaka–Tierney 2018](https://doi.org/10.1016/j.ejor.2017.05.046) reports all instances solved in three of its five tested datasets and 161 additional optima on the difficult CV/BF sets; therefore **not every CV/BF instance has a certified optimum**. [2019 extension](https://doi.org/10.1016/j.ejor.2019.04.005) reports additional optima. Preserve regeneration/version and per-instance proof status. | BF ZIP public and downloaded; no internal licence. Author page Apache-2.0 notice is software-oriented; original third-party data licences **unresolved**. Other archives catalogued, not downloaded. |
| **Integrated stack loading + CPMP/BRP (SP-k)**, [Kimms–Wilschewski dataset v2](https://berd-platform.de/records/xwmmg-dvw42), DOI 10.60914/xwmmg-dvw42 | `Data_SP-k.zip` (2.4 MB), described as instances, solutions and complete computational results. Includes grouped items. Exact internal schema unverified: download endpoint redirected to an expired signed URL in the browser tool. | [Open paper §§4–5](https://doi.org/10.1007/s00291-025-00825-y) optimises initial placements together with subsequent moves. This is yard integration, not vessel stowage. Dataset supplies solved cases; check solver status per instance. | Record is openly accessible but displayed metadata has **no explicit licence**. Article's licence does not automatically cover this separate archive; rights/schema open. |
| **BRP with appointment scheduling**, [Azab, Mendeley v1](https://data.mendeley.com/datasets/wctcbp5z4h/1), DOI 10.17632/wctcbp5z4h.1 | Modified literature data; files named after original authors. Pickup appointments change the problem; archive schema not inspected. | A useful optional extension; **not a substitute for fixed-order BRP optimum targets**. Source explicitly says the instances were changed for BRPAS. | **CC BY 4.0** explicitly displayed on dataset record; public, no login. Keep attribution and original provenance. |
| **Learning to retrieve containers (Shin et al.)**, [author code/data](https://github.com/operagang/CRP_RL), `benchmarks/Lee_instances/`, `benchmarks/Shin_instances/` | Includes benchmark parsing/evaluation utilities and generator; internals not executed. Use newly generated Shin instances preferentially if provenance differs for Lee copies. | [Paper](https://doi.org/10.1016/j.trc.2025.105496) reports DRL experiments across scales; heuristic scores are **not certified optima**. Useful additional learned-policy yardstick after classical 4.10 reproduction. | Code MIT; dataset explicitly **CC BY 4.0** in [LICENSE-DATA](https://raw.githubusercontent.com/operagang/CRP_RL/main/LICENSE-DATA). Check original Lee provenance before redistribution. |

**For 4.10:** start with small Caserta restricted-BRP instances matched to Galle's per-instance result status; then
use Zhu for unrestricted comparisons and TT16 for group priorities. This is a project recommendation, conditional on
clarifying original dataset reuse rights. Parse as inert text, confirm top/bottom orientation and priority direction
against each source's parser, and retain maximum height separately: a file's initial height is not necessarily its
allowed operational height. Record objective as *relocations*; add the fixed `N` retrievals only when reporting total
crane picks. Do not compare a restricted optimum against an unrestricted planner or impose a unique ID sequence on
grouped boxes. [BRP definitions and grouped-order semantics](https://doi.org/10.1007/s00291-025-00825-y),
[Galle reader](https://raw.githubusercontent.com/vgalle/binaryIP_CRP/master/readInstanceCaserta.jl).

**Local research copies:** `data/raw/move-benchmarks-tanaka/` holds BF and Zhu ZIPs plus the author-page notice;
`MANIFEST` records public URL, download date, byte count, SHA-256 and licence ambiguity. Archive contents were read
without executing anything. Keep these as research copies pending rights clarification; do not redistribute them as
permissively licensed data. No confidential terminal data was collected.

**Remaining data gaps:** integrated ship-stowage/yard papers do not automatically supply reproducible public yard
instances. For [the 2020 integrated stowage/loading-sequence paper](https://arxiv.org/abs/2006.06795), an independently
licensed public instance archive has not been verified in this pass. Synthetic StowLM cases must be labelled as such;
BRP/CPMP optima certify only their simplified stacking models.

## Gaps → what we synthesise (say so in the pitch)

1. **Per-call load/discharge volumes** — not public. Synthetic, constrained by port totals and ship sizes.
2. **Feeder bay/row/tier layouts, stack and tier weight tables, tank layouts** — not public. Scaled from Larsen–Pacino +
   Wärtsilä/Damen figures.
3. **Lightship weight distribution and KG for a real feeder** — not public. Empirical estimate (e.g. Watson–Gilfillan),
   labelled.
4. **Gross weight distribution for intra-Asia boxes** (VGM stays with carriers/terminals) — Larsen–Pacino classes as proxy.
5. **DG class mix and true misdeclaration rate** — assumption, cited as such.
6. ~~IMDG Code (paid)~~ — resolved: IMDG 42-24 is published free as IMO resolution MSC.556(108) (D10). Lashing remains an
   approximation unless the IRS method (§4) is implemented; bridge visibility now follows SOLAS V/22 (§4).
7. Ship-size mix of calls at Laem Chabang; intra-Asia fleet by TEU band (paywalled: Alphaliner/Clarksons/BRS).
8. **Yard layouts, equipment stack heights, gate-in patterns and placement practice** (v3, D11) — not public per
   terminal. From the expert (`docs/domain.md` questions) + public equipment specs; labelled assumptions otherwise.
9. **Public yard benchmarks** — found and catalogued in §7 (plan 0.4 / 0.10): BRP distinct/grouped priorities,
   CPMP, and integrated yard stack-loading sets. Published optima exist for subsets; third-party archive licences
   remain unresolved where stated. Public download alone is not a reuse licence. These are evaluation models, not
   evidence for a particular terminal layout or feeder operating policy.
10. **Out-of-gauge cargo and special stowage requests** (expert rules E8, E9): their share of bookings and typical
   overhang sizes aren't public per carrier. Labelled assumptions; ask the expert (domain.md questions 12–13).

## Licences to respect

CC0 (Larsen–Pacino GitHub) — free use. GPL-3.0+ (DTU MasterPlanning) — fine to use for evaluation; keep the data out of
our repo or keep it GPL-compatible. CC BY 4.0 (UN/LOCODE) — attribute. Singapore Open Data Licence, Thai Open Data Common
— attribute. Public domain (eCFR). Damen leaflet — numbers only, no reproduction. Ship-D — no licence → equations only. RCSPPSuite — no licence, anonymised real data → evaluation only (D197), never training, kept in git-ignored `data/raw/`, derived numbers only.
