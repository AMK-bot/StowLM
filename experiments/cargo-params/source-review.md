# Cargo source review

Read date: **2026-10-27**. Primary public sources only. No login, customer
records or downloaded executable code. This note records evidence and limits;
it does not establish real feeder demand distributions.

## Empty fraction

[Hong Kong C&SD, 2 March 2023 release, Containers section](https://www.info.gov.hk/gia/general/202303/02/P2023030200268.htm)
reports calendar 2022 throughput of 16.69 million TEU, including 3.37 million
empty TEU. The ratio is approximately 0.201917. This is whole-port TEU traffic,
including river movements. A 0.20 probability of an empty physical box at Laem
Chabang remains an **assumption**: neither the statistical unit nor geography
nor outbound-call population is the same. The initial search returned the
official full section; a later direct open timed out.

## Rotation patterns

The public [RCL service index](https://rclgroup.com/Routes) links to the
following primary PDFs, opened successfully. The older `rclgroup.com/pdf/SvcPtrn`
URLs sometimes fail; its current media CDN works. Use the papers as regional
connectivity examples, not as a copied identifiable service or evidence for POD
shares. Published schedules are historical patterns, not a confirmed current
operating promise.

| Document | PDF page | Supported pattern |
| --- | --- | --- |
| [RMB8, 20250331](https://www-imedia.rclgroup.com/CorpWEB/PDF/SvcPtrn/20250331-RMB8.pdf) | 1 | THLCH–SGSIN–MYPKG–INKAT–MYPKG–SGSIN–THLCH, weekly |
| [RTS, 20250912](https://www-imedia.rclgroup.com/CorpWEB/PDF/SvcPtrn/RCL-ServiceMap-ASEANFeeder-RTS-20250912.pdf) | 1 | SGSIN–THBKK–THLCH–SGSIN |
| [RNV2, 20241025](https://www.rclgroup.com/pdf/SvcPtrn/20241025-RNV2.PDF) | 1 | KRICH–KRPUS–CNSHA–VNSGN–THLCH–THBKK–THLCH–VNSGN |
| [RSK2, 20250912](https://www-imedia.rclgroup.com/CorpWEB/PDF/SvcPtrn/RCL-ServiceMap-NorthAsiaASEAN-RSK2-20250912.pdf) | 1 | CNNSA–CNSHK–HKHKG–KHKOS–THLCH–VNSGN–PHBTG–PHMNL–CNNSA |

The short LCH/SIN/PKG, LCH/HCM/SIN and LCH/HK/HCM synthetic closed loops
are **approximations** inspired by this connectivity. Omitting intermediate
ports and changing call order must stay labelled as assumptions. No public
POD-level physical-box shares were found.

## DG sample audit

Authoritative source: [IMO MSC.556(108), IMDG 42-24, chapter 3.2](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf),
local previously downloaded text at `data/raw/imdg-42-24/msc556-108.txt`.
This session's remote reopen timed out, so the source checks below use the local
manifested extraction. Page numbers are one-based PDF pages. DGL class checks
do not validate every packing group, subsidiary risk or special provision.

| UN | Primary class/division | Main DGL PDF page |
| --- | --- | --- |
| 0336 | 1.4G (retain compatibility group G) | 590 |
| 1006 | 2.2 | 598 |
| 1017 | 2.3; subsidiary hazards require DGL entry | 598 |
| 1263 | 3; multiple packing-group entries | 610 |
| 1333 | 4.1; cerium slabs, ingots or rods | 614 |
| 1361 | 4.2 | 616 |
| 1402 | 4.3 | 619 |
| 1748 | 5.1 | 639 |
| 1789 | 8; multiple packing-group entries | 642 |
| 1950 | 2; division determined by SP63 | 651 |
| 1978 | 2.1 | 652 |
| 2810 | 6.1; multiple packing-group entries | 694 |
| 2915 | 7 | 699 |
| 3109 | 5.2; formulation-specific qualifications remain | 712 |
| 3373 | 6.2; category-B provisions remain | 736 |
| 3480 | 9 | 746 |

UN 1950 alone does **not** unambiguously identify division 2.1. Recommend UN
1978 propane for the simple division-2.1 draw, or an explicitly classified
flammable-aerosol variant. [BAM's public substance information](https://www.dgg.bam.de/dgginfo/shortinfo/un/5ba497hy)
and SP63 explain that aerosol division depends on contents. [NOAA CAMEO UN
1978](https://cameochemicals.noaa.gov/unna/1978) independently confirms propane
as division 2.1. The full IMDG record must supply subsidiary hazards, packing
group variants, compatibility group, stowage and segregation. A UN draw must
fail explicitly if task 1.3 has not implemented its entry; do not silently
discard unsupported draws and change class frequencies.

All DG frequencies and one-UN-per-class choices are **synthetic assumptions**,
not estimates of commercial class prevalence. Sampling all nine classes tests
rule coverage; actual port/carrier acceptance still needs separate validation.

## Equipment-rating caution

[RCL public container specifications, Ratings tables](https://rclgroup.com/ContainerSpecification)
give examples: 20GP maximum gross mass 30,480 kg with tare 2,185 kg; 40HC maximum
gross mass 32,500 kg with tare 3,700 kg. These support example equipment profiles,
not a universal ISO maximum for every container of that length. The consumer
must use the actual equipment/CSC rating. A common lower synthetic cap can be
an explicitly labelled assumption. No paid ISO text was accessed.

## Expert and remaining gaps

Questions in `docs/domain.md`: Q2 weight-order tolerance, Q8 typical cargo and
volumes, Q11 heavy threshold/under-deck policy, Q12 special-request frequency and
meaning, Q13 OOG frequency, overhang and blocked-slot semantics. These sources
do not calibrate feeder gross mass by POD/reefer, special-request shares or OOG
shares. Any correlations and dimensions remain assumptions pending X1.

Compute: read-only web/source-text inspection plus a sub-second, single-core
stdlib page-location query; no heavy job, GPU, paid service or download.
