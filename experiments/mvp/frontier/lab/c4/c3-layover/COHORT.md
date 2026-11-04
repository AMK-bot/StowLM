# Cycle 4 TRAIN specification

Fixed before generation or solver outcomes: 72 fresh maps, twelve in each
tiny/small/medium × 30%/50% arrival-occupancy cell. Seeds 331401000–331401071,
assigned in size order, then occupancy order, then replicate 0–11. The old
Cycle 3 six maps are excluded. No solver success controls inclusion.

| Size | 30% seeds | 50% seeds |
| --- | --- | --- |
| tiny | 331401000–331401011 | 331401012–331401023 |
| small | 331401024–331401035 | 331401036–331401047 |
| medium | 331401048–331401059 | 331401060–331401071 |

`cohort.py` copies the Cycle 3 wrapper, changing only seed enumeration and
recording, plus memoizing the seed-independent geometry capacity query. Every
seed still runs the existing native generator and background acceptance screens.
The existing generator creates dense non-DG dry20 exports at about
70% total physical-cell occupancy. Tiny retains native empty-box masses;
small/medium retain native loaded/empty masses. The lightest boxes form the
arrival cargo; sorting their masses descending assigns them to bottom-closed
prefix slots. One current-call discharge sits below retained later-port cargo
in a hold stack. The vessel's synthetic background is reconditioned for native
cargo mass. No all-empty medium transformation is attempted.

These are structural Stage B snapshots, not certified preceding voyages or
representative mixed-cargo benchmarks. `Instance` format and public checker
are unchanged. Cohort selection excludes sealed data. Manifest records native
generation reports, original and transformed hashes, actual occupancy and seeds.
Generation errors remain errors; no replacement seed may silently fill a cell.

Copied source: `lab/c3/c3-layover/cohort.py`. Only local files are written.
