# Deferred final verification

`finalize.py` is prepared, not launched by the snapshot subtask. Start it only after the frozen controller has registered. Read the PID and exact `started` value from `results/main/controller-process.json` and invoke:

```
.venv/bin/python experiments/mvp/frontier/lab/c6/c3-multiport/finalize.py --controller-pid PID --controller-started STARTED --out results/main
```

The watcher imports only standard-library modules while waiting, probes only the exact supplied registered PID, and sends it no signals. It does not follow a replacement controller. The successful64-job state/completion records and PID absence must agree before verification begins; all64 unique expected final rows then pass source/config/run-key gates. Checkpoints cannot satisfy this gate. A global controller lock prevents another controller starting during verification. Live source attestations in the snapshot manifest are provenance only; no original c5 experimental file is consulted.

After this gate, the watcher runs sequentially at requested nice5 and one numerical thread: strict fresh native checker/physical-projection audit, pytest over root `HERE.glob('test_*.py')` files only, and frozen report generation. The actual passed-test count is parsed and retained. Each Popen command, PID, parent, launch/registration/finish timestamps, controlled environment, log and return code is saved before/after waiting. `FINALIZE.json` stores source, FREEZE, cohort, active runtime bindings and all64 row hashes before and after verification.

NOTES is updated only after every check succeeds and hashes/bindings remain identical. A marked results block contains the exact frozen completion tables, separate development/nondevelopment port/fill pass tables, links to per-cell paired lift/restow/time evidence, and fresh audit counts. Existing prose is preserved apart from known pending-status sentences. The parent writes the separate under200-word verdict. Any failure leaves NOTES untouched by this watcher and records failure state/logs.

Six standard-library mock tests in `test_finalize.py` cover the active-controller gate, exact PID/start identity and cleanup requirements, exact-PID absence probes, command/PID/environment ledgers, refusal to edit NOTES after audit failure, and bounded marked-block replacement. These tests launch no workers or native checker. They become part of the final dynamically selected root test suite.
