# Family test cut (D220)

Stopped at 2026-10-31T21:20:27-04:00 on the leader's order, after about 52 minutes on 746 rows. `evaluate.py` writes only at the end and reports no progress, so there is no partial score and `results/family-test-tuned.json` doesn't exist. It isn't a gate. v1's R1, R2, R3 and both sealed arms were already scored and are unaffected. `GPU.DONE` here marks that the v1 GPU work is over, not that the family test finished.
