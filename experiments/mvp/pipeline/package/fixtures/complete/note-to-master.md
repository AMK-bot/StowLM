Half-ship section: 190 boxes planned in 8 of 14 bays. Other bays hold 268 fixed synthetic background boxes (3,856 t). Stability and strength include them. No ship can sail on this plan alone. Synthetic ships and cargo, simulated port. Full checker passed; local demo receipt. Stored plan; original computation date unrecorded; rechecked now. Stored synthetic development fixture; source provenance is recorded separately. Pipeline: not frozen yet. Checker coverage: 54 active, 8 inactive.

Half-ship section: 190 boxes planned in 8 of 14 bays. Other bays hold 268 fixed synthetic background boxes (3,856 t). Stability and strength include them. No ship can sail on this plan alone.

# Note to the master

[Deterministic template fallback]

Checker: PASS for the submitted plan under the active model rules; this is synthetic demonstration cargo.

Half-ship section: 190 boxes planned in 8 of 14 bays. Other bays hold 268 fixed synthetic background boxes (3,856 t). Stability and strength include them. No ship can sail on this plan alone.

Departure GM 2.54 m; minimum 0.15 m; synthetic securing-model maximum 3.96 m.

Departure draught aft 7.47 m, forward 7.63 m; hull maximum 8.70 m, forward minimum 3.04 m; berth/channel limit unknown.

Departure trim 0.16 m by the head (aft minus forward -0.16 m); signed model limits -1.24 to 1.24 m.

Departure heel 0.006 degrees; absolute model limit 2.00 degrees.

Departure displacement 13,564.284 t; model limits 5,200.000 to 16,211.274 t. Weight account including fixed background cargo is in stability-summary.md.

5 DG boxes in the planned section; slots: 0020882, 0110304, 0180282, 0180602, 0260182. Details are in manifest.csv.

6 reefer boxes in the planned section; slots: 0020106, 0100804, 0110604, 0170404, 0190404, 0260202. Details are in manifest.csv.

2 OOG boxes in the planned section; slots: 0180882, 0260502. Details are in manifest.csv.

Tight limit 1: stack height at stack 02504H: 0.028 m spare; associated model limit 5.820 m (0.48% spare). Source: rules.stack_height_m.

Tight limit 2: forward draught: 1.066 m spare; associated model limit 8.700 m (12.25% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_fwd_m.

Tight limit 3: aft draught: 1.230 m spare; associated model limit 8.700 m (14.13% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_aft_m.

Tight limits are ranked by fractional spare against their own model limits across every reported margin, including sampled voyage conditions; they are not a continuous-voyage assessment.

No confirmed voyage instruction is recorded in this package.

Ballast unchanged from arrival: 1,552 t. Tank contents are listed in tank-plan.csv; no cargo-work transfer programme is checked. Tank quantities are in tank-plan.csv; no cargo-work adjustment programme is validated.

Cargo-work list estimates and flags are in crane-sequence.csv and cargo-work.json. HEEL-BALLAST-01: stop before any lift flagged over 1 degree until the officer in charge has established a safe, separately checked ballast and crane-work programme. This named hold is an instruction to obtain that programme; no ballast adjustment is claimed.

All 54 active rules ran. 8 are off, including stability during cargo work. Lashing is our approximation, with synthetic gear, because class rules aren't public. Stability during loading isn't checked. The in-port rule is off.
