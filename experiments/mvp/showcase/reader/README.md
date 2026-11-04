# Showcase readings (task 3.2)

`recordings.json`: 30 sentences, each read once by the adopted reader **swa-last-5** (adapter `eff712e1…`) on the laptop,
through the frozen masked path in pipeline manifest `a11b4202…` (`reader_final.AdoptedReader`: sealed-scoring prompt, mask
and greedy decoding). Label: "Recorded: adopted reader swa-last-5 on the laptop, pipeline manifest a11b4202…". Synthetic
ships and sentences; no API call.

Each row holds the sentence, its source file, the ship context hash, the raw reading, the refusal code, a status and the
evidence identity (adapter, decoder, domain-words and prompt hashes, mask on, seconds).

| Kind | Source | Rows |
|---|---|---:|
| Refusal cards | `reader/runs/refusals` | 4 |
| Impossible requests, hero ship | `pipeline/lattice/hero-impossible.json` | 4 |
| Copilot bay request and attack line | `pipeline/copilot/fixtures` | 3 |
| Voice clips the film shows | `voice/runs/v5-1.3`, `app/v6_hero.json` | 6 |
| Prepared brief cases | `app/brief_prepared.json` | 5 |
| Hero wordings and hero radio transcript | `pipeline/lattice/hero-wordings.json`, `pipeline/radio/hero-selection.json` | 8 |

**Misreads, reported, not fixed (5):**
- Brief `dg_keep_away-04`, "Class 3 — sorry, class 8 — one bay from bay 1 at minimum.": refused as ambiguous. The stored rule is class 8.
- Brief `max_tiers-05`, "No containers on deck in bay 2.": read as close bay 2 (hold too). The stored rule is deck tiers 0.
  This is a looser rule read as a stricter one, not a loosening.
- Hero wordings "Keep bay 09 empty: that space is held for the next port.", "Keep bay 09 empty this call, hold and deck."
  and "Skip bay 09 entirely.": refused as unsupported. The stored rule is close bay 9.

Don't show these five as readings the reader made. The other 4 hero wordings, the hero radio transcript, all 4 refusal
cards, the 4 impossible requests and the 6 voice clips read as their stored rules. Copilot's two bay sentences have no
stored rule to compare with, and the attack line was refused (loosening).

Statuses were re-scored once from the stored raw readings (`--rescore`), after fixing how two-rule impossible
requests were paired; no reading changed. Run: `python -m experiments.mvp.showcase.reader.record` (GPU, refuses to
overwrite).
