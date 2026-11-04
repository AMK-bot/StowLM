# Development maps, declared 1 Nov 2026 before any cycle 6 tuning

Old six (dev6.txt): 800/f70-high/101, 500/f70-low/001, 500/f80-high/000, 800/f80-low/102, random 185100100, random 185100200.
New, from the 20 failures, one per failure type (dev-new.txt):
- 500/f80-high/002: securing-only near miss
- sim 185100111: securing-only near miss, 229 boxes
- random 185100000: fallback rounds, 121 boxes
- 800/f60-high/100: E6 only
- 800/f80-high/101: E9 only
- sim 185100210: no candidate, 428 boxes
Everything else stays untouched until the freeze.
