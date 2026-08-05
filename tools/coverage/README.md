# Line coverage of KiCad itself

Builds KiCad 10.0.5 from source with GCC/gcov instrumentation and runs the
conformance suite against it, to see which lines and branches of KiCad the
suite actually executes. `adapters/kicad.py` picks up the instrumented
`kicad-cli` via the `KICAD_CLI` environment variable, so the adapter, runner
and suites all run unchanged.

## Usage

```bash
# 1. Build the instrumented image (long).
tools/coverage/build.sh --jobs 4

# 2. Run the suite; raw counters land in the kicad-coverage-raw Docker volume.
tools/coverage/run-suite.sh --fresh
tools/coverage/run-suite.sh -- suites/drc   # ...or a subset

# 3. Report lands in tools/coverage/out/report/ (html/, focus.json, summary.txt).
# 4. Analysis helpers, reading gcovr JSON from out/report/:
python3 tools/coverage/compare.py OLD.json NEW.json --top 45
python3 tools/coverage/gaps.py NEW.json corrected
tools/coverage/funcs.sh erc.cpp
```

`tools/coverage/out/` is generated, not committed (see `.gitignore`).
Archive `coverage.json` before re-running `run-suite.sh` — it overwrites
`out/report/` and `compare.py` needs a *before* file to diff against.
