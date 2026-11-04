Half-ship section: 87 boxes planned in 6 of 12 bays. Other bays hold 168 fixed synthetic background boxes (2,664 t). Stability and strength include them. No ship can sail on this plan alone. Synthetic ships and cargo, simulated port. Full checker passed; local demo receipt. Stored plan, computed on 2026-11-02T04:36:38.695362+00:00, rechecked now. Stored synthetic development fixture; source provenance is recorded separately. Live reading: StowLM Reader swa-last-5. Pipeline: frozen. Checker coverage: 54 active, 8 inactive.

Half-ship section: 87 boxes planned in 6 of 12 bays. Other bays hold 168 fixed synthetic background boxes (2,664 t). Stability and strength include them. No ship can sail on this plan alone.

# Note to the master

[Deterministic template fallback]

Checker: PASS for the submitted plan under the active model rules; this is synthetic demonstration cargo.

Half-ship section: 87 boxes planned in 6 of 12 bays. Other bays hold 168 fixed synthetic background boxes (2,664 t). Stability and strength include them. No ship can sail on this plan alone.

Departure GM 1.12 m; minimum 0.15 m; synthetic securing-model maximum 3.28 m.

Departure draught aft 6.02 m, forward 6.27 m; hull maximum 7.05 m, forward minimum 2.47 m; berth/channel limit unknown.

Departure trim 0.25 m by the head (aft minus forward -0.25 m); signed model limits -1.08 to 1.08 m.

Departure heel 0.001 degrees; absolute model limit 2.00 degrees.

Departure displacement 8,154.948 t; model limits 2,900.000 to 9,713.353 t. Weight account including fixed background cargo is in stability-summary.md.

4 DG boxes in the planned section; slots: 0110506, 0110682, 0220402, 0220486. Details are in manifest.csv.

2 reefer boxes in the planned section; slots: 0020104, 0220502. Details are in manifest.csv.

0 OOG boxes in the planned section; slots: none. Details are in manifest.csv.

Tight limit 1: approximate securing: max lashing ratio: 0.060 of limit spare; associated model limit 1.000 of limit (5.99% spare). Source: condition:straits:call-0:THLCH:departure:lashing.max_lashing_ratio.

Tight limit 2: stack height at stack 01101H: 0.676 m spare; associated model limit 8.449 m (8.00% spare). Source: rules.stack_height_m.

Tight limit 3: approximate securing: max fitting ratio: 0.085 of limit spare; associated model limit 1.000 of limit (8.46% spare). Source: condition:straits:call-0:THLCH:departure:lashing.max_fitting_ratio.

Tight limits are ranked by fractional spare against their own model limits across every reported margin, including sampled voyage conditions; they are not a continuous-voyage assessment.

Confirmed instruction: Nothing goes into bay nine on this call. Confirmed by: user-authorised batch regeneration, no planner present (demo fixture).

Ballast departure allocation: 921 t. Tank contents are listed in tank-plan.csv; no cargo-work transfer programme is checked. Tank quantities are in tank-plan.csv; no cargo-work adjustment programme is validated.

Cargo-work list estimates and flags are in crane-sequence.csv and cargo-work.json. HEEL-BALLAST-01: stop before any lift flagged over 1 degree until the officer in charge has established a safe, separately checked ballast and crane-work programme. This named hold is an instruction to obtain that programme; no ballast adjustment is claimed.

All 54 active rules ran. 8 are off, including stability during cargo work. Lashing is our approximation, with synthetic gear, because class rules aren't public. Stability during loading isn't checked. The in-port rule is off.
