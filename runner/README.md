# `runner/` -- how it works

Canonical entrypoint: `python3 -m runner [PATHS...]`, Python 3.11+, stdlib only (DL-0002).
Full architecture is in [`../docs/DESIGN.md`](../docs/DESIGN.md); the case.toml schema
this runner implements is in [`../docs/TEST_CASE_FORMAT.md`](../docs/TEST_CASE_FORMAT.md);
what each answer actually compares is in [`../docs/VALIDATION.md`](../docs/VALIDATION.md).
This file is implementation notes for the runner's own code, not the spec.

## The M0.5 shape, in one paragraph

Since [DL-0025]/[DL-0027]/[DL-0028] a `case.toml` names no verb and no output file: it is
`concept` + `doc` + `input` and, occasionally, `extra`. The runner derives what to record
from the **input's file suffix** (`runner/engine.py`'s `input_kind` + `battery_for`) --
`.kicad_pcb` always yields `summary.json`, `render-F_Cu.svg`, `gerbers/`, `drill/`;
`.kicad_sch` yields `summary.json` + `render.svg`; a `.kicad_sym`/`.pretty` library yields
`render/`. A `failure/` case runs the type's loader (`parse-pcb`/`parse-sch`/`parse-sym`/
`parse-fp`) and records no answers at all. `extra = ["drc"]` is the one opt-in knob
(`runner/engine.py`'s `answer_for_extra`). There is no `[[check]]`, `op`, `expected`,
`outcome`, `args` or `compare` anywhere in this codebase any more -- `runner/manifest.py`
rejects a manifest that still has one of those as a loud `CaseError`, not a silent no-op.

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | Argument parsing, orchestration, report printing. `python -m runner` entrypoint (`__main__.py`) lands here. |
| `manifest.py` | Parses/validates `case.toml` (`tomllib`) into the `Case` dataclass; enforces the schema in TEST_CASE_FORMAT.md §5 (including rejecting unknown/retired keys). |
| `adapter.py` | Host-side helper that invokes an adapter executable (default or `--adapter`), pinning `LC_ALL=C.UTF-8`/`TZ=UTC` in its environment (DESIGN §4). |
| `adapters/kicad.py` | The reference adapter: an executable subprocess wrapping `kicad-cli` per the verb protocol (DESIGN §2). Runnable standalone: `python3 runner/adapters/kicad.py <verb> --in ... --out ...`. |
| `verdict.py` | The OK/REJECT/CRASH classifier (DESIGN §3a, DL-0013). |
| `engine.py` | The standard-answer battery per input type (`battery_for`/`answer_for_extra`/`input_kind`/`LOADER_VERB`, the `Answer` dataclass) and the three comparators it dispatches to by answer `kind`: JSON equality, normalized-SVG byte-exact, and a directory-tree compare for `gerbers/`/`drill/`/library `render/` (VALIDATION §9.2). Also runs the `failure/` exit+control+known-divergence path. `--regenerate` writes `expected/<version>/...` from here. |
| `summary.py` | The composite `summary` answer's schema (DL-0022/DL-0028, VALIDATION.md §4): `build_board_summary`/`build_schematic_summary` merge `reduce.py`'s raw parsers into the one normalized document per input. |
| `normalize.py` | The per-output-kind normalizers (DESIGN §4): SVG `<title>`/`<desc>`, CRLF→LF, and the five gerber/Excellon date-line normalizers (G1-G3, D1-D2, DL-0026) that back the `gerbers/`/`drill/` directory compare. |
| `reduce.py` | The canonical reductions for the JSON-comparison answers: DRC/ERC, netlist, stats, pos, ipcd356 (DESIGN §3b, DL-0014). Shared by `summary.py` and the standalone opt-in extras. |
| `sexpr.py` | A minimal S-expression reader (stdlib only) used by `reduce.py`/`summary.py` (netlist) and `coverage.py` (top-level section scanning). Not a full KiCad format model. |
| `coverage.py` | The cheap coverage proxy (DESIGN §7a): CLI-surface + format-token bookkeeping, zero KiCad rebuild. Derives exercised verbs from `engine.py`'s battery, the same way the engine itself does -- there is no manifest field to read them off any more. |
| `determinism.py` | The run-twice determinism self-test (DESIGN §4a), over the same `Answer` battery `engine.py` runs for a real check. |
| `verbs.py` | The capability-verb table (adapter-internal only, since DL-0025), shared by the adapter's `capabilities` response and the coverage proxy so they can't drift apart. |

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

## Two things worth knowing before extending this

- **`.gbrjob`'s creation-date key.** DESIGN.md's normalization table (§4) names the path
  `GeneralSpecs/CreationDate`. Empirically (KiCad 10.0.5) it is actually
  `Header/CreationDate` (with `Header/GenerationSoftware/Version` alongside it). The
  normalizer in `normalize.py` strips what kicad-cli actually emits; this note flags the
  discrepancy against the design doc's table rather than silently diverging from it.
- **Determinism self-test's honest limit.** For `parse-sch`/`parse-pcb` (the `failure/`
  loader) the determinism self-test has nothing to compare twice at all -- a `failure/`
  case records no answers (TEST_CASE_FORMAT.md §7), so `determinism.py` skips it
  entirely rather than overclaiming coverage it doesn't have.

## Running it

```bash
# Inside the pinned Docker image (what CI uses) -- see scripts/run.sh / scripts/regen.sh
scripts/run.sh                      # run everything under suites/
scripts/run.sh suites/drc/          # scope to one suite or case
scripts/run.sh --determinism-check  # run-twice self-test
scripts/run.sh --coverage-proxy     # full CLI-surface + format-token report
scripts/regen.sh                    # regenerate expected files (inspect the diff before committing)
```
