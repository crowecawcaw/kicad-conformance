"""kicad-conformance reference runner.

Canonical entrypoint: `python3 -m runner [PATHS...]`, Python 3.11+, stdlib only (DL-0002).
Full architecture is in `docs/DESIGN.md`; the case.toml schema this runner implements is
in `docs/TEST_CASE_FORMAT.md`. This docstring is implementation notes for the runner's
own code, not the spec.

## The shape, in one paragraph

Since [DL-0025]/[DL-0027]/[DL-0028] a `case.toml` names no verb and no output file: it is
`concept` + `doc` + `input` (or `inputs` + `root`) and, occasionally, `extra`. The runner
derives what to record from the **input's file suffix** (`engine.py`'s `input_kind` +
`battery_for`) -- `.kicad_pcb` always yields `summary.json`, `render-F_Cu.svg`, `gerbers/`,
`drill/`; `.kicad_sch` yields `summary.json` + `render.svg`; a `.kicad_sym`/`.pretty`
library yields `render/`. A rejection case (one that sets `control`) runs the type's loader
(`parse-pcb`/`parse-sch`/`parse-sym`/`parse-fp`) and records no answers at all; a case with
no `control` is a happy case -- polarity comes from the manifest, not from a `happy/`/
`failure/` directory. `extra = ["drc"]` is the one opt-in knob (`engine.py`'s
`answer_for_extra`). There is no `[[check]]`, `op`, `expected`, `outcome`, `args`,
`compare` or `min_kicad` anywhere in this codebase -- `manifest.py` rejects a manifest
that still has one of those as a loud `CaseError`, not a silent no-op.

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | Argument parsing, orchestration, report printing. `python -m runner` entrypoint (`__main__.py`) lands here. |
| `manifest.py` | Parses/validates `case.toml` (`tomllib`) into the `Case` dataclass; enforces the schema in TEST_CASE_FORMAT.md §5 (including rejecting unknown/retired keys); derives polarity from `control`. |
| `adapter.py` | Host-side helper that invokes an adapter executable (default or `--adapter`), pinning `LC_ALL=C.UTF-8`/`TZ=UTC` in its environment (DESIGN §4). |
| `engine.py` | The standard-answer battery per input type (`battery_for`/`answer_for_extra`/`input_kind`/`LOADER_VERB`, the `Answer` dataclass), the OK/REJECT/CRASH verdict classifier, and the three comparators it dispatches to by answer `kind`: JSON equality, normalized-SVG byte-exact, and a directory-tree compare for `gerbers/`/`drill/`/library `render/`. Also runs the rejection-case exit+control+known-divergence path. `--regenerate` writes `expected/<version>/...` from here. |
| `summary.py` | The composite `summary` answer's schema (DL-0022/DL-0028, DESIGN.md §3b): `build_board_summary`/`build_schematic_summary` merge `reduce.py`'s raw parsers into the one normalized document per input. |
| `normalize.py` | The per-output-kind normalizers (DESIGN §4): SVG `<title>`/`<desc>`, CRLF→LF, and the five gerber/Excellon date-line normalizers (G1-G3, D1-D2, DL-0026) that back the `gerbers/`/`drill/` directory compare. |
| `reduce.py` | The canonical reductions for the JSON-comparison answers (DRC/ERC, netlist, stats, pos, ipcd356 -- DESIGN §3b, DL-0014), shared by `summary.py` and the standalone opt-in extras; also a minimal stdlib-only S-expression reader (`parse_all`/`find_one`/`find_all`) used to walk `kicadsexpr` netlists. |
| `determinism.py` | The run-twice determinism self-test (DESIGN §4a), over the same `Answer` battery `engine.py` runs for a real check. |

The reference adapter lives OUTSIDE this package, at `<repo-root>/adapters/kicad.py` --
an ordinary executable subprocess wrapping `kicad-cli` per the verb protocol (DESIGN §2),
runnable standalone: `python3 adapters/kicad.py <verb> --in ... --out ...`. It used to live
at `runner/adapters/kicad.py`, which collided with `runner/adapter.py` (the host-side
invoker just above); moving it out matches DESIGN §1's story that the adapter is an
executable, not a runner internal.

## The adapter boundary, concretely

The runner's direct subprocess child is the *adapter*, never `kicad-cli` itself
(DL-0007). That indirection matters for crash detection: when `kicad-cli` is killed by
a signal (the known 10.0.5 PCB-parse segfault), the adapter must not simply observe that
and exit with some ordinary code -- it re-raises the *identical* signal against itself
(`os.kill(os.getpid(), sig)` in `adapters/kicad.py`'s `run_and_relay`). That is what lets
`engine.py`'s `Verdict`/`classify` -- which only ever inspects its own direct child's
`returncode` -- see a genuinely negative (signaled) `returncode` and correctly report
`CRASH`, rather than laundering a real crash into a normal-looking adapter exit code.
This was verified empirically: invoking the adapter directly against a truncated board
fixture produces `returncode == -11` (SIGSEGV) at the runner's own subprocess call, not
just at kicad-cli's.

## Multi-file inputs (`inputs` + `root`)

A case may declare `inputs = [...]` instead of a single `input`, plus `root` naming which
entry is the netlist root (a multi-sheet schematic). `Case.input_paths` resolves every
entry to a full path, and `engine.py` passes the WHOLE list to `adapter.invoke` -- not
just the first -- so `adapters/kicad.py`'s `_scratch_copy_all` can copy every sub-sheet
into one scratch dir under its own filename before running `sch export netlist`/building
the summary. Copying only the first input (the pre-fix behavior) either fails to resolve
a missing sub-sheet reference or silently summarizes the root sheet alone -- see
`suites/schematic-parse/hierarchical-sheet/` for the case that proves this.

## Two things worth knowing before extending this

- **`.gbrjob`'s creation-date key.** DESIGN.md's normalization table (§4) names the path
  `GeneralSpecs/CreationDate`. Empirically (KiCad 10.0.5) it is actually
  `Header/CreationDate` (with `Header/GenerationSoftware/Version` alongside it). The
  normalizer in `normalize.py` strips what kicad-cli actually emits.
- **Determinism self-test's honest limit.** For `parse-sch`/`parse-pcb` (a rejection
  case's loader) the determinism self-test has nothing to compare twice at all -- a
  rejection case records no answers (TEST_CASE_FORMAT.md §7), so `determinism.py` skips
  it entirely rather than overclaiming coverage it doesn't have.

## Running it

```bash
# Inside the pinned Docker image (what CI uses) -- see scripts/run.sh
scripts/run.sh                        # run everything under suites/
scripts/run.sh suites/drc/            # scope to one suite or case
scripts/run.sh --determinism-check    # run-twice self-test
scripts/run.sh --regenerate suites/   # regenerate expected files (inspect the diff before committing)
```
"""

__version__ = "0.1.0"
