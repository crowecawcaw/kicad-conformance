# Line coverage of KiCad itself

Builds KiCad 10.0.5 from source with GCC/gcov instrumentation and runs the
conformance suite against it, to see which lines/branches of KiCad the suite
actually executes. `adapters/kicad.py` picks up the instrumented `kicad-cli`
via `KICAD_CLI`, so the adapter, runner and suites all run unchanged.

## Usage

```bash
tools/coverage/build.sh --jobs 4        # 1. build the instrumented image (long)
tools/coverage/run-suite.sh --fresh     # 2. run the suite -> out/report/{html,coverage.json}
tools/coverage/run-suite.sh -- suites/drc   # ...or a subset
# 3. analysis helpers, reading gcovr JSON from out/report/coverage.json:
python3 tools/coverage/compare.py OLD.json NEW.json --top 45        # before/after diff
python3 tools/coverage/gaps.py NEW.json corrected                    # corrected buckets / dead code
python3 tools/coverage/subdir.py pcbnew/pcb_io/kicad_sexpr NEW.json  # one subdir's coverage
tools/coverage/funcs.sh erc.cpp                                      # per-function coverage

# 4. engine-scope denominator (which lines a CLI run can even reach) -> out/engine/:
tools/coverage/engine-scope.sh              # all 4 stages -> engine-report.txt
tools/coverage/engine-scope.sh why SYMBOL   # audit one root->symbol path
python3 tools/coverage/engine_validate.py negative --coverage out/engine/engine-coverage.json \
    --denominator out/engine/engine-denominator.tsv.gz   # sanity-check the closure

tools/coverage/verify-builddeps.sh   # drift-check Dockerfile deps vs upstream
```

`tools/coverage/out/` is generated, not committed. Archive `coverage.json` before
re-running `run-suite.sh` — it overwrites `out/report/` (needed for `compare.py`).
