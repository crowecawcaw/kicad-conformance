# `runner/` -- how it works

Canonical entrypoint: `python3 -m runner [PATHS...]`, Python 3.11+, stdlib only (DL-0002).
Full architecture is in [`../docs/DESIGN.md`](../docs/DESIGN.md); the case.toml schema
this runner implements is in [`../docs/TEST_CASE_FORMAT.md`](../docs/TEST_CASE_FORMAT.md).
This file is implementation notes for the runner's own code, not the spec.

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | Argument parsing, orchestration, report printing. `python -m runner` entrypoint (`__main__.py`) lands here. |
| `manifest.py` | Parses/validates `case.toml` (`tomllib`) into `Case`/`Check` dataclasses; enforces the schema rules from TEST_CASE_FORMAT.md §4.2. |
| `adapter.py` | Host-side helper that invokes an adapter executable (default or `--adapter`), pinning `LC_ALL=C.UTF-8`/`TZ=UTC` in its environment (DESIGN §4). |
| `adapters/kicad.py` | The reference adapter: an executable subprocess wrapping `kicad-cli` per the verb protocol (DESIGN §2). Runnable standalone: `python3 runner/adapters/kicad.py <verb> --in ... --out ...`. |
| `verdict.py` | The OK/REJECT/CRASH classifier (DESIGN §3a, DL-0013). |
| `engine.py` | Runs one check and applies the comparison its `op` implies (JSON equality for `model`/`drc`/`erc`/`netlist`/`pos`/`ipcd356`/`stats`, normalized-SVG byte-exact for `render`, exit-only for everything else), and (with `--regenerate`) writes `expected/<version>/...`. |
| `model.py` | The composite `model` verb's schema (DL-0022, VALIDATION.md §4): `build_board_model`/`build_schematic_model` merge `reduce.py`'s raw parsers into the one normalized document per input. |
| `normalize.py` | The per-output-kind normalizers (DESIGN §4) — today just the SVG `<title>`/`<desc>` strip and CRLF→LF; the rest is retained as reference for the gerber/drill gap (VALIDATION §7). |
| `reduce.py` | The canonical reductions for the JSON-comparison ops: DRC/ERC, netlist, stats, pos, ipcd356 (DESIGN §3b, DL-0014). Shared by `model.py` and the standalone opt-in projections. |
| `sexpr.py` | A minimal S-expression reader (stdlib only) used by `reduce.py`/`model.py` (netlist) and `coverage.py` (top-level section scanning). Not a full KiCad format model. |
| `coverage.py` | The cheap coverage proxy (DESIGN §7a): CLI-surface + format-token bookkeeping, zero KiCad rebuild. |
| `determinism.py` | The run-twice determinism self-test (DESIGN §4a). |
| `verbs.py` | The capability-verb table, shared by the adapter's `capabilities` response and the coverage proxy so they can't drift apart. |

## The adapter boundary, concretely

The runner's direct subprocess child is the *adapter*, never `kicad-cli` itself
(DL-0007). That indirection matters for crash detection: when `kicad-cli` is killed by
a signal (the known 10.0.5 PCB-parse segfault), the adapter must not simply observe that
and exit with some ordinary code -- it re-raises the *identical* signal against itself
(`os.kill(os.getpid(), sig)` in `adapters/kicad.py`'s `run_and_relay`). That is what lets
`runner/verdict.py`'s classifier -- which only ever inspects its own direct child's
`returncode` -- see a genuinely negative (signaled) `returncode` and correctly report
`CRASH`, rather than laundering a real crash into a normal-looking adapter exit code.
This was verified empirically: invoking the adapter directly against a truncated board
fixture produces `returncode == -11` (SIGSEGV) at the runner's own subprocess call, not
just at kicad-cli's.

## A documented schema judgment call

`docs/TEST_CASE_FORMAT.md` §4.2 lists `control` as a **check**-level field, but its own
worked examples (§5.2, §5.2b) place `control = "..."` at the **case** top level next to
`input`. `manifest.py` accepts both: a case-level `control` is the default positive
control for every `outcome="error"` check in the case, and a `[[check]]` may set its own
`control` to override it. All of this repo's own example cases use the case-level form,
matching the worked examples.

## Two other things worth knowing before extending this

- **`.gbrjob`'s creation-date key.** DESIGN.md's normalization table (§4) names the path
  `GeneralSpecs/CreationDate`. Empirically (KiCad 10.0.5) it is actually
  `Header/CreationDate` (with `Header/GenerationSoftware/Version` alongside it). The
  normalizer in `normalize.py` strips what kicad-cli actually emits; this note flags the
  discrepancy against the design doc's table rather than silently diverging from it.
- **Determinism self-test's honest limit.** For `parse-sch`/`parse-pcb`, the *raw*
  upgrade output is already byte-stable run-to-run within one kicad-cli install --
  `generator_version` is the installed binary's own version string, which does not
  change between two runs of the same container. The `generator_version` normalizer is
  therefore justified by DESIGN's stated rationale (it changes across a kicad-cli point
  release, which this per-session test can't reproduce), not by a raw-differs/
  normalized-matches pair the way the timestamp-bearing formats (DRC JSON, netlist,
  gerbers, `.gbrjob`) are. `--determinism-check`'s report says exactly this for every
  check it runs, rather than overclaiming.

## Running it

```bash
# Inside the pinned Docker image (what CI uses) -- see scripts/run.sh / scripts/regen.sh
scripts/run.sh                      # run everything under suites/
scripts/run.sh suites/drc/          # scope to one suite or case
scripts/run.sh --determinism-check  # run-twice self-test
scripts/run.sh --coverage-proxy     # full CLI-surface + format-token report
scripts/regen.sh                    # regenerate expected files (inspect the diff before committing)
```
