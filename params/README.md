# Parameter files

Tracked, human-editable parameters for the synthetic world. Read with stdlib `tomllib` through
`stowage.params` (`list_param_files(kind)`, `load_param_file(kind, name)`, `quantity(node)`).

| Folder | Plan task | Consumer |
|---|---|---|
| `params/cargo/` | 1.4 cargo distributions | 2.5 load-list generator |
| `params/ports/` | 1.5 port archetypes | 2.6 port profiles, 3.4 port-state simulator |
| `params/fleet/` | 1.6 synthetic fleet particulars | 2.1 hull, 2.2 vessel instances |

## Conventions

- **Every number carries its provenance.** Write a numeric value as a table:
  `{ value = 5, unit = "tiers", source = "<URL> — what it says — date read" }` or
  `{ value = 5, unit = "tiers", assumption = "<why; expert question number if one will answer it>" }`.
  `stowage.params.unsourced_numbers(tree)` lists bare numbers and tables without provenance; each kind's tests use it.
- **SI units** (kg, m, s, N, N·m, rad, m³, kg/m³), named in `unit`. Counts use a plain noun (`"tiers"`, `"boxes"`).
- **No real company or customer data.** Ports and ships are synthetic archetypes ("Laem Chabang-like"); public
  statistics may be cited as sources, nothing behind a login.
- One TOML per archetype or ship; the file stem is its id (`laem_chabang_like.toml` → `"laem_chabang_like"`), a
  lower-case slug.
- Approximations are labelled in a comment starting `APPROXIMATION:` and proposed for `docs/decisions.md`.
- Each folder's owner keeps its own `README.md` with the source list and the assumptions waiting on the expert.
