Half-ship section: 17 boxes planned in 6 of 12 bays. Other bays hold 168 fixed synthetic background boxes (2,677 t). Stability and strength include them. No ship can sail on this plan alone. Synthetic ships and cargo, simulated port. Full checker passed; local demo receipt. Pipeline: not frozen yet. Checker coverage: 54 active, 8 inactive.

Half-ship section: 17 boxes planned in 6 of 12 bays. Other bays hold 168 fixed synthetic background boxes (2,677 t). Stability and strength include them. No ship can sail on this plan alone.

# Note to the master

[Deterministic template fallback]

Checker: PASS for the submitted plan under the active model rules; this is synthetic demonstration cargo.

Half-ship section: 17 boxes planned in 6 of 12 bays. Other bays hold 168 fixed synthetic background boxes (2,677 t). Stability and strength include them. No ship can sail on this plan alone.

Departure GM 2.54 m; minimum 0.15 m; synthetic securing-model maximum 3.28 m.

Departure draught aft 6.22 m, forward 6.20 m; hull maximum 7.05 m, forward minimum 2.47 m; berth/channel limit unknown.

Departure trim 0.02 m by the stern (aft minus forward 0.02 m); signed model limits -1.08 to 1.08 m.

Departure heel -0.105 degrees; absolute model limit 2.00 degrees.

Departure displacement 8,286.643 t; model limits 2,900.000 to 9,713.353 t. Weight account including fixed background cargo is in stability-summary.md.

0 DG boxes in the planned section; slots: none. Details are in manifest.csv.

0 reefer boxes in the planned section; slots: none. Details are in manifest.csv.

0 OOG boxes in the planned section; slots: none. Details are in manifest.csv.

Tight limit 1: aft draught: 0.828 m spare; associated model limit 7.050 m (11.75% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_aft_m.

Tight limit 2: forward draught: 0.848 m spare; associated model limit 7.050 m (12.03% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_fwd_m.

Tight limit 3: maximum displacement: 1,426.710 t spare; associated model limit 9,713.353 t (14.69% spare). Source: condition:straits:call-0:THLCH:departure:stability.displacement_maximum_kg.

Tight limits are ranked by fractional spare against their own model limits across every reported margin, including sampled voyage conditions; they are not a continuous-voyage assessment.

Confirmed instruction: Keep bay three empty on this call. Confirmed by: source recorded as 'functional_app_planner_click'.

Ballast departure allocation: 1,727 t. Tank contents are listed in tank-plan.csv; no cargo-work transfer programme is checked. Tank quantities are in tank-plan.csv; no cargo-work adjustment programme is validated.

Cargo-work list estimates and flags are in crane-sequence.csv and cargo-work.json. HEEL-BALLAST-01: stop before any lift flagged over 1 degree until the officer in charge has established a safe, separately checked ballast and crane-work programme. This named hold is an instruction to obtain that programme; no ballast adjustment is claimed.

All 54 active rules ran. 8 are off, including stability during cargo work. Lashing is our approximation, with synthetic gear, because class rules aren't public. Stability during loading isn't checked. The in-port rule is off.
