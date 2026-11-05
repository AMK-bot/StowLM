<!-- Figures come from experiments/impact-estimate/impact_v6.py and are checked in its marked draft, experiments/impact-estimate/impact-v6.md. This copy carries later wording edits (D316, D363, D364) that the draft doesn't; no figure differs. D376 replaced the unsourced 5/10/15% restow shares with evidence-based avoidable shares of 0.3/0.9/2.2%. v5 is at docs/archive/impact-v5-2026-11-01.md. -->

# The size of the prize

**About $90 billion a year of port-call cost; every 1% is about $1 billion.** *Exposure projection.*

**About $2.3 billion a year of extra crane work and berth time that a better plan can act on — projection, not a result.**

**All losses (the film's and the submission's figure):** bad load plans cost about $0.35 to $6.6 billion a year worldwide, mid about $2.4 billion, across restow fees, fuel, lost slots, delay and safety, and 0.03 to 5.6 deaths a year (mid 0.7). All projection; sources and method in [load-plan-losses.md](load-plan-losses.md). The pool below is the narrower part a better plan acts on directly, extra crane work and berth time, and it is the figure the app's Impact panel shows.

The exposure frame combines verified commercial terminal-market estimates with modeled vessel time at berth: **$85.3–98.1 billion/year**. The rounded lower-end frame is separate from the improvement pool below.

| Annual improvement pool, gross USD billions | Low | Mid | High |
|---|---:|---:|---:|
| Extra ship-restow lifts | 0.06 | 0.42 | 2.07 |
| Extra yard-rehandle lifts | 0.11 | 0.71 | 3.29 |
| Vessel time freed at berth | 0.12 | 1.13 | 7.09 |
| Carrier planning labor exposed to improvement | 0.02 | 0.05 | 0.09 |
| **Total annual prize** | **0.3** | **2.3** | **12.5** |

**Laem Chabang projection:** about **$40 million a year at Laem Chabang if scaled by its share of calls** (projection): 9,325 of about 520,000 calls is 1.8% of the mid-case pool, on the same global inputs and average workload per call. Half the pool is lifts, which scale with TEU rather than calls, and the port's calls skew to feeders, so this is a share of calls, not a port estimate. The [2024 port count](https://thaibsaa.com/images/documents/Annual24-25_Final.pdf) includes all container-vessel sizes, not just eligible feeders.

| Capture of that prize, USD millions/year | Low prize | Mid prize | High prize |
|---|---:|---:|---:|
| 1% | 3 | 23 | 125 |
| 5% | 15 | 115 | 627 |
| 20% | 61 | 461 | 2,508 |

### Terminal-revenue cross-check

The verified terminal-revenue range is **$63–76 billion a year** (unrounded source estimates below).

| Opened source, 2024 estimate | Revenue, USD billions | Implied USD/primary lift | Mid-case restow line / revenue |
|---|---:|---:|---:|
| [360iResearch, reproduced by GII](https://www.giiresearch.com/report/ires1803580-container-terminal-operator-market-by-container.html) | 62.67 | 102 | 0.7% |
| [TechSci, direct publisher release](https://www.techsciresearch.com/news/24170-container-terminal-operations-market.html) | 75.5 | 123 | 0.6% |

The mid-case restow line is **$0.42 billion**, inside terminal revenue, not additional to it. Add **$22.6 billion** of full mid-case vessel time at berth to the market-estimate span to build the exposure frame; do not add the improvement pool again. Publisher definitions differ and include storage/intermodal services; these are plausibility benchmarks, not audited world receipts. Maersk reports **USD 337 revenue/move** in its [2024 terminal accounts](https://www.maersk.com/~/media_sc9/maersk/corporate/sustainability/files/resources/2024/maersk-annual-report-2024.pdf?h=576&hash=39BA5083CD82827B64A382757FDF50A8&w=1024), including the whole workflow. The unverified IBISWorld estimate is excluded.

## The scale path

The path to scale is **more compute for the search, a larger model for reading**. Future steps are projections.

| Step | What it unlocks | What must be true at that step |
|---|---|---|
| **Half-ship sections of 500–800 TEU feeders today** | Feeder-section documents and confirmed instructions → checked plan → handover, with Engine 0.13.0 in the engine slot. | Demonstrate the frozen loop inside the design envelope; publish task 3.1 measurements. |
| **A firm's own engine in the slot — potential** | A carrier, terminal or stowage-software vendor keeps its planning software and adds the pipeline around it: papers read, instructions confirmed, the full checker gate, a signed handover. | Its software returns a plan in the pipeline's format through the binding, with its own source pin and its own way to honour confirmed rules; a fresh held-out run through the same checker measures it. No third-party engine has been ported. |
| **1,700 TEU next — projection** | Larger regional feeders, including occupied arrivals and discharge. | A larger model reads the broader inputs; more search compute and vessel data support checked layover plans. |
| **8,000 TEU — projection** | Dense cargo, more cranes and connected port calls. | Search, vessel-specific checks and data exchange work at that load; independent planners validate the benefit. |
| **20,000 TEU — projection** | Planning across large ocean-going vessels and terminal networks. | Multi-port decisions, operational integration and support survive representative trials and earn adoption. |

## Pitch paragraph

Port calls represent about **$90 billion a year in cost exposure**; every **1%** is about **$1 billion**. **Projection:** better plans can act on about **$2.3 billion a year** of extra crane work and berth time, with **5% capture worth about $115 million a year**. Today's pilot runs the loop on synthetic feeder sections, **24 of 24 held-out voyages passing the full checker** (the planning step, from a clean load list; frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim; two earlier attempts stopped on our own guard, both published; report: `experiments/mvp/pipeline/sealed/runs/final-3/REPORT.md`, task 3.1), from messy documents and a planner's words to a checked plan and handover. StowLM delivers that loop as an app with the planning engine in one slot: Engine 0.13.0 today, and by design a firm's own planning software, which no one has ported yet. More compute for the search, a larger model for reading: the path is **half-ship sections of 500–800 TEU feeders today, then 1,700, 8,000 and 20,000 TEU ships** (projection). Check: world terminal revenue $63–76 billion a year · inputs: 920M TEU, 520k calls, 0.9 % avoidable restows at $75, 5 % less berth time.

## Video line

“If scaled: $2.3 billion annually in extra crane work and berth time, inside a $90 billion bill. Projection, not result.”

On-screen card:

> PROJECTION $2.3B/yr pool in ~$90B/yr exposure | 920M TEU; 520k calls; 0.9% avoidable restows @ $75  
> Requires full-ship, multi-port planning; rates hold worldwide; avoidable work; terms that permit operational use. No measured saving.
> Check: world terminal revenue $63–76 billion a year · inputs: 920M TEU, 520k calls, 0.9 % avoidable restows at $75, 5 % less berth time.

## What must be true

**Full-ship, multi-port planning works; these rates hold worldwide; the extra work is avoidable; operational use needs terms that permit it: today's Muse Glimmer usage policy prohibits use related to operating transportation technologies where harm is possible; a deployment would need Meta's permission or a base model whose terms allow it, plus class approval; none of it has been measured.** The Meta Model API Acceptable Use Policy lists the same use and bars relying on Outputs for safety decisions without a qualified person reviewing them, so API terms must permit operational use too, whichever engine is in the slot. Earlier departure must free usable vessel time. This is a gross opportunity scenario, not an audited global loss, net savings, software revenue or a result achieved. Lift prices proxy what carriers are charged; billed reductions can transfer revenue between carrier and terminal and are not world marginal-cost savings; carrier planner labor must sit outside terminal tariff labor, and charter time excludes fuel dollars. World throughput is treated as productive load/discharge handling, without another transshipment multiplier; audit restows already included before adding extra lifts. Box mix, one container per lift, fleet-weighted time, rates, wages and adoption need validation. The planner pool includes productive work, so capture—not the full pool—is the potential benefit. Costs of compute, integration and support must be deducted in a business case; the engine is not competitive at large scale yet. Today's eligible feeder-call count is unknown, no operational saving is measured, and the pilot, legacy throughput-adoption scenarios and global-prize tables must not be added. The 'runs the loop' sentence cites task 3.1 (final-3) with its D284 label. The pool counts better plans whichever engine makes them: with a firm's own engine in the slot, lift and berth savings belong to that engine and are unmeasured, and the pipeline's own share is mainly planner attention and rework (the pilot rows below), never added to the pool.

## What builds the global number

[UNCTAD](https://unctadstat.unctad.org/insights/theme/245) reports **920,300,805 TEU** of port handling in 2024. Assumed **1.5 TEU per physical lift** converts it to **613.5 million primary lifts**. [UNCTAD RMT 2025, Chapter IV](https://unctad.org/system/files/official-document/rmt2025ch4_en.pdf) puts container calls near **260,000 per half-year** across 2024: a rounded derived **520,000 calls/year**, covering IMO-numbered ships of **at least 1,000 GT**, not the smaller CPPI sample.

| Input: global transfer/selection assumptions | Low | Mid | High |
|---|---:|---:|---:|
| Avoidable ship-restow share of all ship moves | 0.3% | 0.9% | 2.2% |
| Extra yard lifts / productive loaded box | 3.74% | 9.23% | 21.43% |
| Primary throughput needing that yard retrieval | 50% | 50% | 50% |
| USD per extra ship lift | 30 | 75 | 150 |
| USD per extra yard lift | 10 | 25 | 50 |
| Berth hours/call | 15 | 26.25 | 45 |
| Berth time removed if the extra work disappears | 2% | 5% | 10% |
| Selected charter assessment, USD/day | 17,855 | 39,725 | 72,722 |
| Carrier planner hours/call | 2 | 2.5 | 3 |
| Planner value, USD/hour | 20 | 39.58 | 60 |
| **Computed extra ship lifts/year, millions** | **1.8** | **5.6** | **13.8** |
| **Computed extra yard lifts/year, millions** | **11.5** | **28.3** | **65.7** |

**Avoidable-restow shares of 0.3/0.9/2.2% are assumptions built on the one measured rate (D376).** [Sri Lanka Ports Authority](https://www.parliament.lk/uploads/documents/paperspresented/annual-report-srilanka-ports-authority-2018.pdf) reports restowing at Colombo of **83,935 of 6,209,068 TEU in 2017 (1.35%)** and **101,879 of 7,047,486 TEU in 2018 (1.45%)**, and **196,269 of 6.86 million TEU in 2022 (2.9%)** ([The Morning, citing SLPA data](https://www.themorning.lk/articles/sBZ9a4XsIt2nHTa9Z1xm)). That counts every restow, at one transhipment hub; it is 2.7–5.7% of lifts if each restow counts as two. We take all restows as **1.5/3/5.5%** of ship moves and the avoidable part as **20/30/40%**, a band that rests only on vendor claims ([Loadmaster.ai](https://loadmaster.ai/ai-based-restow-minimization-in-deepsea-container-port-vessel-planning/), [Planner on Duty](https://planneronduty.com/)). The earlier 5/10/15% shares had no source and ran about 8–17 times above this evidence; trade figures of 15–20% of moves trace to a thesis that never mentions restows. Jensen and Ajspur's [constructed stress voyage, 2022 Table 10](https://www.sealytix.com/media/gm4flxf5/revenuemanagementinlinershippingaddressingthevesselcapacitychallenge.pdf) needs **17–28% of all moves as restows even when moves are minimized**; that is required work under fixed cargo conditions, not measured planning waste. Yard ratios use **23.67/633, 33.24/360 and 18/84** simulated reshuffles per loaded container on real terminal plans in [Legato and Mazza, WSC 2013, Table 2](https://informs-sim.org/wsc13papers/includes/files/305.pdf). Yard and ship lifts are distinct equipment operations; the yard model applies only to the assumed loaded half of productive throughput. Auditing whether a terminal's reported throughput already includes restows remains a gate.

### Prices across regions

| Primary public source | Published rate and scope |
|---|---|
| [Colombo SAGT, 2025 §§3/6](https://d3bo42cmohq4cn.cloudfront.net/2025/05/SAGT-Tariff-2025-Final-08.05.2025-1.pdf) | **USD 46.50–80 per ship lift**, or **93–160 per two-lift pair**; CY sorting **USD 25–47/container**. |
| [Yusen, Los Angeles, December 2025, Rule 9-015](https://yti.com/wp-content/uploads/2025/12/YTI-Terminal-Tariff-Effective-December-1-2025-V2.pdf) | **USD 205.93** cell shift; **411.89** via-quay pair, computed **205.945/lift**. |
| [Peel Liverpool L2, 2026 Part III](https://www.peelports.com/media/bdkl4swb/2026-common-user-charges-for-l2-container-terminal-ver-1.pdf) | **GBP 192.92/restow lift**, **57.03/additional yard lift**. |
| [Jurong Singapore, April 2026, C1](https://www.jp.com.sg/wp-content/uploads/2026/04/20260401-Jurong-Port-Schedule-of-Rates-Tariff-Increase-eff-1-Apr-2026-1.pdf) | **SGD 72.50/container** landing/reshipping; event billing, not a verified per-lift price. |
| [CMA CGM Hong Kong local charges](https://www.cma-cgm.com/assets/public/documents/HONG%20KONG%20-%20LOCAL%20SCES_3.pdf) | **HKD 700/container** extra terminal/stack move; service scope differs from ship restows. |
| [Maersk Annual Report 2024, Terminals pp.36–37](https://www.maersk.com/~/media_sc9/maersk/corporate/sustainability/files/resources/2024/maersk-annual-report-2024.pdf?h=576&hash=39BA5083CD82827B64A382757FDF50A8&w=1024) | **USD 258 terminal cost/move**, financially consolidated: actual full-workflow accounting, not marginal extra-lift cost. |

The adopted sensitivities are **USD 30/75/150 per extra ship lift** and **10/25/50 per yard lift**. The yard low has a [published USD 10/rehandle modeling assumption](https://www.scitepress.org/papers/2017/61930/61930.pdf); its simulated improvement is not a StowLM result. Regional list tariffs give price context, not a measured world marginal-cost distribution. Non-USD quotes stay in their original currencies; no unsourced exchange-rate conversion is used. More regions and exact work definitions are in the [price audit](../experiments/impact-estimate/anchor-sources-v6.md#broader-extra-lift-prices-revised-source-survey).

For ship time, [CPPI 2020–2024, Figure 3.6 and Table 5.1](https://ppp.worldbank.org/sites/default/files/2026-01/The%20Container%20Port%20Performance%20Index%202020%20to%202024%20-%20Trends%20and%20Lessons%20Learned.pdf) supplies **75% berth share** and call-size port-time context. We select **20/35/60 port hours**, multiply by that share, then assume the stated departure gain. [VHBS, 29 October 2026](https://www.vhbs.de/index.php?id=28&L=1) supplies the selected **12-month** charter assessments. Planner effort uses [published 2–3 hours per port](https://www.jstage.jst.go.jp/article/pjsai/JSAI2019/0/JSAI2019_2C5E502/_pdf/-char/ja); wage context is the [BLS May 2025 U.S. logistician median, USD 82,320/year](https://www.bls.gov/ooh/Business-and-financial/logisticians.htm), divided by an assumed **2,080 hours**. Worldwide weighting remains a model selection.

    primary lifts = world TEU / TEU per lift
    extra ship lifts = primary lifts × restow share / (1 − restow share)
    extra yard lifts = primary lifts × eligible yard fraction × rehandle ratio
    berth value = calls × berth hours × reducible share × charter/day / hours per day
    planner pool = calls × planner hours × hourly value
    annual prize = ship-lift value + yard-lift value + berth value + planner pool
    capture = annual prize × capture fraction, applied once

### Climate potential, separately

[IMO's Fourth GHG Study](https://www.imo.org/en/mediacentre/pages/whatsnew-1596) puts total shipping at **2.89% of global CO2 in 2018**; this is context, not the intervention's emissions denominator. [PEMA, 2011 §3.2](https://pema.org/wp-content/uploads/2022/09/PEMA-IP2-Energy-and-Environmental-Efficiency-in-Ports-and-Terminals.pdf) reports **6 kWh per electric quay move including 2 kWh auxiliaries**; assuming only **4 kWh** disappears, paired with the [IEA 2024 generation factor of 0.445 kg CO2/kWh](https://www.iea.org/reports/electricity-2025/emissions), gives **1.78 kg CO2 per comparable extra quay lift**. [EPA 2022](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P1014J1S.TXT) supplies selected auxiliary loads **340/700/1,320 kW** and **217 g fuel/kWh × 3.206 g CO2/g fuel**. Applying them to the modeled earlier berth departure, plus electric quay lifts removed, gives conditional operational CO2 potential of **0.04 / 0.34 / 2.17 million tonnes/year**. Yard emissions remain unquantified; no emissions dollars are added. Local equipment, grid, shore-power use and added computation must replace these transfer assumptions.

## Today's forward pilot

Designed for a **half-ship section** of feeders of **500–800 TEU**, **195 boxes**, **70% fill of that section**, empty arrival and one loading call; synthetic ships and cargo, simulated port. [BSAA 2024 activity](https://thaibsaa.com/images/documents/Annual24-25_Final.pdf): Laem Chabang **9,325 container calls / 9,554,673 TEU**; Bangkok **1,381 feeder calls**. [RCL's historical regional service](https://www.rclgroup.com/PressReleaseArticle/NEWS1222) used **628-TEU ships**, confirmed in its [2020 fleet table](https://www.rclgroup.com/pdf/AnnualReports/2020_AnnualReport_ENG_V2.pdf).

Planning step on the held-out envelope maps (clean load list, frozen standard profile, this laptop, four maps at a time): mean 9.4 minutes per map; with one confirmed instruction, mean 11.6 minutes, planning only, reading timed apart (`sealed/runs/final-3/story-figures.json`; held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim; both earlier attempts published, not pooled; report: `experiments/mvp/pipeline/sealed/runs/final-3/REPORT.md`, task 3.1). Documents-to-plan time end to end, including intake, wasn't measured on held-out maps. Reader accuracy on its locked test (scored once): **118 of 128 exact** (92.19 %), 1 of 48 loosening requests accepted (v2: 120 of 128, 2 of 48); it passed the 90 % gate and missed two stricter ones; Muse Spark 126 of 128 is the reference; the same model before fine-tuning: **60 of 128**. The shipped intake (Spark reading whole documents) has no held-out accuracy figure; a row-level side test is reported in the write-up with its caveats (D273). Operational attention needs its own pilot study.

| Pilot input — assumptions unless marked sourced | Low | Mid | High |
|---|---:|---:|---:|
| Eligible adopting calls/year, an assumed portfolio | 52 | 260 | 1,040 |
| Primary productive physical load lifts/call | 100 | 150 | 195 |
| Extra attributable rehandle lifts / primary lift | 2% | 5% | 10% |
| Reduction in those extra lifts | 10% | 20% | 30% |
| Initial planner attention: baseline → assisted, hours/call | 2 → 1 | 2.5 → 0.75 | 3 → 0.5 |
| Subsequent instruction changes/call | 1 | 2 | 3 |
| Attention per change: baseline → assisted, minutes | 30 → 10 | 45 → 5 | 60 → 3 |
| Exceptional correction events avoided/call | 0.1 | 0.25 | 0.5 |
| Additional attention per correction, minutes | 30 | 60 | 90 |
| Loaded planner opportunity value, USD/hour | 20 | 30 | 40 |
| USD / avoided extra lift: sourced charge, assumed application | 20.86 | 20.86 | 20.86 |

| Computed pilot projection | Low | Mid | High |
|---|---:|---:|---:|
| Avoided extra physical lifts/call | 0.20 | 1.50 | 5.85 |
| Planner attention freed, hours/call | 1.38 | 3.33 | 6.10 |
| Handling-charge equivalent, USD/call | 4.17 | 31.29 | 122.03 |
| Labor opportunity equivalent, USD/call | 27.67 | 100.00 | 244.00 |
| **Gross equivalent, USD/call** | **31.84** | **131.29** | **366.03** |
| **Gross equivalent, USD/year** | **1,656** | **34,135** | **380,672** |
| Planner attention freed, hours/year | 71.9 | 866.7 | 6,344.0 |

The pilot preserves its earlier illustrative assumptions: disjoint initial work, later instruction changes and exceptional rework; carrier labor outside terminal charges. Its **USD 20.86/lift** historical [Chennai proxy](https://www.dpworld.com/india/-/media/project/dpwg/dpwg-tenant/apac/india/media-files/ports-and-terminals/chennai/tariff/cctl-sor---wef-1st-may-2025.pdf?rev=ceba2186d9df4e2e928898993b4792ec) is retained only for continuity here. The global prize uses the broader price evidence.

Existing software spending is a route to adoption, and the engine slot is the design for it: a vendor or a carrier's own planning team could keep its engine and put the pipeline around it (potential; no third-party engine has been ported). Commercial publisher estimates are **USD 798.24 million** for container TOS ([PW Consulting, 2025 estimate](https://pmarketresearch.com/worldwide-container-terminal-operating-system-market-research/)) and **USD 1.8 billion** for stowage software and services across ship types ([DataIntelo, 2025 estimate](https://dataintelo.com/report/digital-stowage-planning-software-market)). Overlapping analyst categories, not additive revenue targets. Potential leads: feeder operators and planning teams at Laem Chabang and Bangkok; no partnership or outreach implied. Freed vessel slots and slower steaming are unsized upsides, not added to either total.

The legacy 500-box sizing paragraph in pitch §7 is deleted; it is outside the current envelope. Source checks excluded unverified IBISWorld revenue, ONE regional tariff amounts, NREL energy, and the Westports USD benchmark; see the audit notes. Official UNCTAD PDF and CSV were read directly.

Draft under decision D254 (in the project decision log, not published), 1 November 2026. [Calculator](../experiments/impact-estimate/impact_v6.py), [recorded projection](../experiments/impact-estimate/fixtures/impact-v6.json), [tests](../experiments/impact-estimate/test_impact_v6.py), [run instructions](../experiments/impact-estimate/README.md), [price/ratio audit](../experiments/impact-estimate/anchor-sources-v6.md), [calls/time/climate audit](../experiments/impact-estimate/scale-sources-v6.md). No sealed data opened.
