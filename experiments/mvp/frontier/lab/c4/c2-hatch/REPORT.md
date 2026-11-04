1. **Model:** footprint packing, fixed cover/port charges, restow closure and first-aboard ranks, strengthened by necessary-rule cuts.

2. **Papers:** Roberti–Bartolini–Mingozzi (2015); Hooker–Ottosson (2003); Land–Doig (1960).

3. **Results:** validation **39/45 proved (+7)**; tiny **8/8**, quick-small **10/10**. New bound work: **31.2 minutes**. New checked warm plans: `41100001=8`, `40100038=6`. Cold constructor: **8/10 replay, 2/10 full**, mean 33 s/map including failures; raw first witnesses **0/10** with 20 s finishing. Warm: **4/10**. Fresh 0.9.0+E9 controls: 2/2; sole paired lift mean 22/22, 247.8 s/control. Limited controls and varying load: no speed claim. Final quick portfolio: **20/21 replay, 20/21 full**.

4. **Keep bounds; construction cannot replace search.** Proofs mean **fewest lifts among plans the checker passes, counting later ports by our D15 projection (fixed stow, no later loads)**. Global proofs allow current restows; the main constructor uses load-once. Guards: empty decision arrival, no discharge/forecast, immutable background. On medium maps preselected as passable, bounds **65/51/70** versus uppers 71/55/70: **1/3 proved**, no improvement after 240 s joint search. **Six validation cases remain open.**

5. All files in C4. [NOTES.md](experiments/mvp/frontier/lab/c4/c2-hatch/NOTES.md). Entries: `certified.py:lower_bound`, `constructor.py:construct`. **86 tests passed**; DG widening and bound API unchanged. Next: coupled physics cuts.
