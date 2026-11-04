# Dense smoke failure: HiGHS global scheduler mismatch

The first f80 dense smoke returned no constructed candidate: all four
construction attempts raised `master LP status 4: (HiGHS Status 0: Not Set)`
after thousands of warm columns. This was a solver setup error, not evidence
that the roof family, page LP or map is infeasible. The old smoke row must
remain recorded as an implementation failure and be superseded by a distinct
fixed-method run, not counted as a physical infeasibility result.

A minimal two-LP reproduction confirmed the cause. The old plain fallback
called SciPy HiGHS without an explicit `threads` option, initializing its
process-global scheduler to nine threads on this machine despite numerical
environment variables being one. The subsequent moment master explicitly
requested one thread. HiGHS printed:

> Option 'threads' is set to 1 but global scheduler has already been initialized
> to use 9 threads.

The first LP returned status 0; the next returned status 4 / Not Set. This also
means environment variables alone did not enforce the requested numerical
thread count for that old fallback path. Accepted plans still passed the full
checker, but affected constructor failures and timings cannot be interpreted
as solver performance or map infeasibility.

The fixed dense arm imports **`pages_fast_dense.py`**, a private copy of frozen
`pages_fast.py`. Its sole semantic change wraps every `linprog` call with
`options['threads']=1`, preserving other options and suppressing SciPy's
forwarded-option warning. Thus the first LP initializes the same one-thread
scheduler used by subsequent moment pricing. No global scheduler reset, checker
change, shared library patch, process kill or frozen A source edit was used.
`method_dense.py` imports this copy. Existing result rows and hashes are kept.

Code audit of the dense call chain found explicit one-thread options on the
moment master (`pages.py`), fixed-charge LP (`lns.py`) and native ballast-search
LP (`ballast_opt.py`). CP-SAT uses one search worker in the private fallback,
constructor and LNS. The copied moment base has its LP overridden by the moment
Model. Native hydrostatic/equilibrium calculations are not HiGHS LP calls.

Verification: two regression tests pass in `test_highs_threads.py`, exit 0,
with numerical environment threads one, a scoped vessel cache and pytest cache
provider disabled. One checks preservation of other options and forced
one-thread forwarding; the other runs the fixed fallback LP first and then the
actual moment-master implementation in the same process, reproducing the
formerly failing order without status 4. No numerical process remains.
