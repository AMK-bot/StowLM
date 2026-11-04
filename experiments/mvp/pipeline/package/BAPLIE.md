Synthetic demonstration — BAPLIE export cut; no carrier interchange validation.

# Exchange-format decision

Task 1.11 researched BAPLIE on 1 November 2026 and does **not** emit
`plan.baplie.edi`. The package's `plan.json` and `crane-sequence.csv` remain
application exports; neither claims BAPLIE conformance.

[SMDG's ship-planning guidance](https://smdg.org/documents/edifact-migs-message-implementation-guidelines/ship-planning/)
lists BAPLIE 3.1.1 on UN/EDIFACT D.13B and recommends version 3. Its published
implementation guides and code lists, rather than a plausible-looking example,
are the target for a future exporter. The
[UNECE message definition](https://service.unece.org/trade/untdid/latest/trmd/baplie_c.htm)
describes vessel and voyage identities, occupied positions, equipment, operator,
weights and ports, with additional information for hazardous and special cargo.

The current input supplies synthetic container IDs, ISO size/type, weights,
loading/discharge ports, dangerous-goods attributes and a replayable placement.
It does not supply an interchange partner agreement, responsible equipment
operators, vessel IMO/call-sign identity or exchange voyage identifiers. Adding
synthetic values would be possible, but their meaning and every conditional
segment still need an explicit supported profile. A seven-digit internal cell
ID alone is insufficient evidence that a bay/row/tier export has the intended
ISO 9711 semantics, especially for long boxes spanning cells.

In this task the SMDG guide download and UNECE D.13B page fetches did not
complete through the research tool. Search returned the authoritative SMDG
version table and UNECE scope, but that is insufficient to implement all
required/conditional segments and dangerous-goods handling faithfully. There is
also no independent offline BAPLIE parser/profile validator or carrier test
system in the package. Within the allotted implementation window, a correctly
validated exporter was not established. Emitting a small sample would therefore
overstate what was checked. The permitted exchange-format cut is taken.

Revisit with the complete SMDG 3.1.1 guide and code lists, an explicit synthetic
interchange profile, documented internal-slot conversion, handling for every
cargo attribute present (including DG, reefer and out-of-gauge), independent
parse/validation and round-trip placement comparisons. Any future file must
identify itself as generated from synthetic data and unvalidated against a
carrier system; a signature on this package does not supply that validation.
