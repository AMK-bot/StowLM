# Development live run — 900-second deadline

40/40 medium rows finalized (10 preserved rows; 60 new rows across all cells). Three process-isolated worker slots. Eight public r0 development strata, baseline plus four confirmed rule types; this is a structured-rule diagnostic, not Reader accuracy, sealed evaluation, or whole-ship evidence.

New attempts have a 900s process-group deadline; requested live-v2 search/finish budgets are90s/30s. Preserved rows retain their original timing and provenance. Fleet admission is additional. Deadline outcomes distinguish an independently checked instruction-obeying incumbent from no checked plan inside the live budget. A deadline or unsuccessful search is not proof of impossibility. Only full checker passes obeying every instruction count as passes; relaxed plans are separate.

| Cell | Rule | Final | Preserved | Full pass | Relaxed | Deadline + checked | Deadline, no checked | Binding pass | Already obeyed pass | Unknown pass | First checked median s | Final median/max s | Paired lifts / baseline |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
|medium|baseline|8|4|5|2|0|0|—|—|—|114.2|221.9/1030.0|— / — (n=0)|
|medium|close_bay|8|2|3|3|0|0|3|0|0|90.5|212.6/717.0|84.0 / 76.0 (n=3)|
|medium|max_tiers|8|2|6|1|0|0|2|3|1|89.7|207.4/658.1|90.0 / 96.0 (n=5)|
|medium|dg_keep_away|8|2|7|1|0|0|4|0|3|84.5|263.6/841.1|79.0 / 101.0 (n=4)|
|medium|one_port_per_hatch|8|0|5|2|0|1|4|0|1|425.5|689.3/900.2|70.5 / 86.0 (n=4)|
|tiny|baseline|3|0|3|0|0|0|—|—|—|15.0|17.4/19.0|— / — (n=0)|
|tiny|close_bay|3|0|0|0|0|0|0|0|0|—|1.5/2.4|— / — (n=0)|
|tiny|max_tiers|3|0|3|0|0|0|3|0|0|10.5|12.0/15.8|8.0 / 8.0 (n=3)|
|tiny|dg_keep_away|3|0|3|0|0|0|1|2|0|26.2|32.3/32.4|8.0 / 8.0 (n=3)|
|tiny|one_port_per_hatch|3|0|0|0|0|0|0|0|0|—|4.9/5.1|— / — (n=0)|
|small|baseline|3|0|3|0|1|0|—|—|—|107.6|416.3/902.7|— / — (n=0)|
|small|close_bay|3|0|0|0|0|0|0|0|0|—|1.6/4.3|— / — (n=0)|
|small|max_tiers|3|0|3|0|0|0|1|2|0|95.6|570.2/887.7|9.0 / 9.0 (n=3)|
|small|dg_keep_away|3|0|2|0|1|0|2|0|0|250.7|847.9/900.1|10.0 / 10.0 (n=2)|
|small|one_port_per_hatch|3|0|3|0|0|0|2|1|0|202.4|510.0/560.0|10.0 / 9.0 (n=3)|

A binding pass requires the matched passed baseline to violate that exact rule. Without a passed baseline, binding is unknown. Paired lifts use the same mutually passing maps; lift spread is search variation, not causal instruction cost or saving. Best means best found at this work budget.

Remaining medium scheduling projection: 0×900s/3 = 0.00h, plus admission and baseline dependencies. At30 remaining rows this is2.5h of slot scheduling and may take about3h before additional admission waits; it is not a promised finish time.

Tiny/small jobs are opportunistic after the medium queue; a tiny single-hatch section with multiple discharge ports is not a useful hatch-allocation test. No pending outcome is counted as final.
