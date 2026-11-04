# Rule tiers and the peel ladder (user order, 1 Nov 2026; D241)

The user's instruction: sort rules into tiers, and when a plan can't be solved, peel enforcement back from the least important tier, T5, up to T1. The sorting is the leader's, open for veto. Machine-readable table: `experiments/mvp/frontier/lab/c5/leader/tiers.toml`.

**How rules were sorted:** by what a breach costs in the real world, before measuring which rules block our maps. A rule never moves tier to improve a result.

| Tier | Meaning | Rules |
|---|---|---:|
| T0, never peeled | Not a rule to relax: the plan couldn't be carried out, or the check never ran | 22 |
| T1, must follow | Can capsize, break or ground the ship, start a fire or explosion, or is a legal bar on sailing | 22 |
| T2, should follow | A real safety or statutory limit, with a recognised relief | 7 |
| T3 | Promises to customers and cargo care, including leaving a box ashore | 6 |
| T4 | The field expert's stowage practice | 2 |
| T5, extra | Preferences and tie-breaks | 3 |

- **T0:** one box per slot, size fit, no 20-foot box on a 40-foot box, height clearance, top-only access, hatch clear, bottom-up filling, access at later ports (2), out-of-gauge overlap (2), crane and yard reach and crossing (4), checker ran and had its data (4), loading software validation (3).
- **T1:** displacement, load-line draught, GM, free surface, the six GZ criteria, weather criterion, hull strength, stack weight, IMDG segregation (2) and stowage, port-refused DG, verified gross mass, open hatch immersion, stability during cargo work, port draught, port length.
- **T2:** container securing, containership second stability check (IS Code Part B, recommendatory), bridge visibility, trim, list, ballast pumping window, DG direct delivery.
- **T3:** every box loaded, under-deck, on-deck, away-from-heat and top-stowage bookings, reefer plug.
- **T4:** heavy at the bottom, no heavier box on a lighter one.
- **T5:** heavy under deck (the expert calls this a preference, Q11), stability margin and strength margin tie-breaks.

Judgement calls most worth a veto:
- **T0 is an addition to the user's five tiers.** Two boxes can't share a slot however far enforcement is peeled, so these aren't on the ladder.
- **Securing is T2.** It's a real safety limit, but extra lashing or a route limit are recognised reliefs, and our model is an approximation with synthetic gear. Moving it to T1 would change most results, since it's broken on every failing map.
- **Leaving a box ashore is T3.** That puts it after preferences and stowage practice, and before any safety limit, matching D11's order: legal first, then load every box.

## The ladder

| Level | May break | What the plan is called |
|---|---|---|
| L0 | nothing | Passes the full checker |
| L1 | T5 | Relaxed: gives up preferences |
| L2 | T5, T4 | Relaxed: gives up stowage practice |
| L3 | T5, T4, T3 | Relaxed: gives up bookings or leaves boxes ashore |
| L4 | T5, T4, T3, T2 | Breaks a class or statutory limit: needs a person's sign-off |
| L5 | T5, T4, T3, T2, T1 | Not safe to sail: shown only to say what would have to change |

- **Only when stuck.** A map moves down a level only when the engine has used its fixed work at the level above without a plan. That means "no plan found", never "impossible", unless proved. A level is never used to save lifts.
- **Peel as little as possible.** Inside a level, plans rank by: fewest boxes ashore, then TEU and mass ashore, fewest broken rules, smallest breach, then fewest lifts. L4 doesn't ignore T2: it looks for the smallest breach.
- **Keep peeling until every box is aboard (D242).** The ladder stops at the first completed plan. It also keeps the best partial plan at L3, which meets every T1 and T2 rule with the fewest boxes ashore, so a planner can choose between them.
- **The checker is unchanged.** It still reports every breach. The ladder only reads its report and decides what may be returned.
- **Pass rates mean L0 only.** Relaxed plans are counted separately, by level.

## Completion by tier (user order, 1 Nov 2026; D242)

The user's metric: how many maps completed at T5, at T4, at T3, and so on. A map is **completed at a tier** when every box is aboard and it obeys that tier and every tier above it.

| Column | Peeled | Level |
|---|---|---|
| T5 | nothing: an ordinary full-checker pass | L0 |
| T4 | T5 | L1 |
| T3 | T5, T4 | L2 |
| T2 | T5, T4, T3, bookings only, since every box must be aboard | L3 |
| T1 | T5 to T2 | L4 |
| T0 only | T5 to T1 | L5 |

- **A plan leaving any box ashore is partial, and is never counted as completed.** First results showed why: "fewest broken rules" alone rewarded a nearly empty ship, 8 of 121 boxes aboard. Safe partial plans are reported beside the table, with the % of boxes aboard.
- **Report it as a table by map set, with "not completed" shown.** Tool: `experiments/mvp/frontier/lab/c5/leader/completion.py`.
- **"Passes" still means the T5 column alone.** No other column is added to a pass rate.
