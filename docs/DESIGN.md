# Design — kicad-conformance architecture

This document defines the architecture. Companion docs:
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) (how to author a case),
[`DECISIONS.md`](DECISIONS.md) (numbered rationale), [`ROADMAP.md`](ROADMAP.md).

---

## 1. Core model

Four pieces, deliberately decoupled:

```
   ┌──────────┐   fixtures + case.toml   ┌────────┐   verb + files   ┌─────────┐
   │  CORPUS  │ ───────────────────────▶ │ RUNNER │ ───────────────▶ │ ADAPTER │
   │ (cases)  │                          │        │ ◀─────────────── │ (subproc)│
   └──────────┘                          └────┬───┘  exit + output   └─────────┘
        ▲                                     │                            │
        │ declares expectations               │ normalize + compare        │ kicad-cli
        │                                      ▼                            ▼ (reference)
   ┌──────────┐                          ┌──────────┐              ┌──────────────┐
   │ GOLDENS  │ ◀── regenerate ────────  │ verdict  │              │ pcb / others │
   │ per-ver  │    (reference oracle)    │ pass/fail│              │ (ecosystem)  │
   └──────────┘                          └──────────┘              └──────────────┘
```

- **Corpus of input fixtures + declarative expectations.** A *case* is a directory: a
  tiny `case.toml` manifest, one or more input fixtures, and (for rich-output checks) a
  `golden/<version>/` tree. Expectations are declarative data, never code.
- **Runner.** Walks `suites/`, reads each `case.toml`, invokes the adapter once per
  declared check, applies the normalization layer, and decides pass/fail. It is a
  reference harness, not the source of truth — the *file format* is the contract (an
  SDK-based implementation may write its own runner against the same cases). This is
  the single most important lesson from the OpenJobDescription prior art.
- **Adapter.** Abstracts the implementation-under-test behind a fixed set of
  **capability verbs** exchanged over a **subprocess protocol** (§2). The **reference
  adapter wraps `kicad-cli`**; others (a Rust engine, a viewer) implement the same
  verbs.
- **Goldens.** The "correct answer" for a rich-output check, **authored by the
  reference oracle (KiCad)** at a pinned version and stored per version. Never
  hand-written. Any adapter's output is compared against the KiCad golden — KiCad is
  authoritative.

Why the adapter is a *subprocess* boundary and not a Python plugin API: it keeps the
implementation-under-test **language-agnostic**. KiCad ships a C++ CLI; the pcb engine
is Rust; a future tool could be anything. A subprocess contract (files in, exit code +
captured streams + written output files out) is the lowest common denominator every
tool can satisfy. See [DL-0007](DECISIONS.md).

---

## 2. The adapter contract

An adapter is an executable. The runner invokes it once per check as:

```
<adapter> <verb> --in <path...> --out <dir> [--format <fmt>] [verb-specific flags]
```

and inspects three things: the **exit code** (0 = success, non-zero = the tool rejected
the input), the captured **stdout/stderr**, and any **files written under `--out`**. The
runner sets `LC_ALL=C.UTF-8` and `TZ=UTC` in the adapter's environment for every call.

The reference adapter (`runner/adapters/kicad.*`) is a thin shim: it discovers
`kicad-cli` (env `KICAD_CLI` → `PATH` → per-OS install dirs, newest-numeric-version
first, exactly as the pcb-oracle harness does), then maps each verb onto one or more
`kicad-cli` subcommands. Discovery verifies the binary with
`kicad-cli version --format plain` and records `version --format about` in the run log
as the oracle's identity.

### Capability verbs

Each adapter declares which verbs it supports; unsupported verbs cause the relevant
cases to be **skipped and counted**, never failed (openjd's capability-negotiation
idea, expressed in data). Core verbs:

| Verb | Input | Output the runner consumes | `kicad-cli` mapping (10.0.5) |
|---|---|---|---|
| `version` | — | version string on stdout | `version --format plain` (+ `--format about` for the identity record) |
| `parse-sch` | `.kicad_sch` | success/failure; canonical s-expr | `sch upgrade --force` on a **scratch copy** (rewrites in place) |
| `parse-pcb` | `.kicad_pcb` | success/failure; canonical s-expr | `pcb upgrade --force` on a scratch copy (rewrites in place) |
| `parse-sym` | `.kicad_sym` | success/failure; canonical s-expr | `sym upgrade --force -o <out> <in>` |
| `parse-fp` | `.pretty` dir / `.kicad_mod` | success/failure; canonical s-expr | `fp upgrade --force -o <dir> <in_dir>` |
| `upgrade` | any of the above | canonical re-save (golden compared) | same `… upgrade --force` subcommand as the matching `parse-*` |
| `erc` | `.kicad_sch` | structured violation set | `sch erc --format json --severity-all` |
| `drc` | `.kicad_pcb` | structured violation set | `pcb drc --format json --units mm --severity-all` |
| `netlist` | `.kicad_sch` (root) | structured net→node membership | `sch export netlist --format kicadsexpr` |
| `export-gerbers` | `.kicad_pcb` | golden file set (RS-274X) | `pcb export gerbers` |
| `export-drill` | `.kicad_pcb` | golden file set (Excellon + report) | `pcb export drill --generate-report --report-path <r> -o <dir>` |
| `export-pos` | `.kicad_pcb` | golden file (CSV/ASCII) | `pcb export pos --format csv --side both --units mm` |
| `export-step` | `.kicad_pcb` | geometry (bbox/tolerance) — **scope TBD** | `pcb export step` (heavy, least deterministic; see [DL-0012](DECISIONS.md)) |
| `bom` | `.kicad_sch` | golden file (CSV) | `sch export bom` (fixed field/sort spec) |

Notes drawn from the research, load-bearing for correct mapping:

- **`parse-*` vs `upgrade` share a subcommand.** There is no pure "parse and stop" verb
  in `kicad-cli`; `… upgrade --force` loads the file (proving it parses) and re-emits it
  in canonical form. `parse-*` cares only about *did it load* (exit polarity); `upgrade`
  additionally compares the canonical re-save against a golden. `--force` is always
  passed so the result never depends on the input file's pre-existing version stamp.
- **`pcb`/`sch upgrade` rewrite in place** (no `--output`), so the adapter must copy the
  fixture to a scratch dir first and read the result back. `fp`/`sym upgrade` are
  library/directory operations with `-o` (and have their own gotchas: `fp upgrade -o`
  refuses a pre-existing output path; `sym upgrade -o` writes one merged file unless the
  output path already exists as a directory).
- **DRC flags are pinned:** `--severity-all` (otherwise the reported set depends on the
  project's stored settings); `--units mm` (`--units in` prints the same decimals but at
  a 25×-coarser quantum, destroying information). We do **not** pass
  `--exit-code-violations`: a DRC violation is *data to compare*, not a tool failure.
- **`step`/3D and `render`** need OpenCASCADE and sometimes a display (`xvfb-run`), and
  their output is the least deterministic (OCC ISO-10303 timestamp + tessellation
  ordering). Their inclusion is deferred pending owner ratification ([DL-0012]).

---

## 3. Comparison model

Every check declares a `compare` mode. Pass/fail is decided per check:

### 3a. `exit` — success/failure polarity (+ error substring)

The baseline mode, and the *only* thing a `failure` case needs. Mirrors openjd's
filename-polarity trick, moved into the manifest:

- `expect = "ok"` → the adapter must exit `0`.
- `expect = "error"` → the adapter must exit non-zero.
- For `expect = "error"`, an optional `error_contains = "…"` asserts a substring on
  **stderr** (per-stream, not merged, so a warning can't satisfy an error check). An
  `error_contains_any = ["…", "…"]` escape hatch tolerates legitimate wording variation
  between implementations (openjd's `anyOf`).

Substring matching is deliberately loose: it pins the *observable contract* (the tool
rejects a malformed board and says something about the offending token) without
over-fitting to KiCad's exact phrasing, so a second adapter with different error text
still conforms.

### 3b. `structured` — semantic reduction (DRC, ERC, netlist)

For outputs where formatting, ordering, and internal IDs are irrelevant, a byte compare
is meaningless. The runner parses both sides into a canonical structure and compares
membership:

- **netlist** → `{ net-name : sorted set of (refdes, pin) }`. A pin on the wrong node,
  a split net, a misnamed net fails; formatting/net-code/order never does. (This is
  exactly the pcb harness's netlist oracle.)
- **DRC / ERC** → sorted list of `(rule-id, severity, sorted item locations)`. Sorted by
  content, **not by UUID** — some violation-item UUIDs are minted fresh each run.
- The structural reduction is defined per verb in the runner and documented so a second
  adapter knows what shape to emit.

Residue is **characterized, not hidden**: when a structured compare fails, the runner
reports *how* (names-only difference vs membership difference vs count difference), the
same bucketing the pcb netlist oracle uses.

### 3c. `golden-file` / `golden-dir` — normalized text compare (gerbers, drill, upgraded s-expr, pos, bom)

For rich text/interchange outputs, compare **byte-exact after normalization** (§4).
`golden-file` is a single output; `golden-dir` is a multi-file set (gerbers emit one
file per layer + a job file) — the whole tree is normalized and compared, missing/extra
files are failures.

### 3d. Geometry tolerance (STEP / future 3D) — printed-quantum, no pre-authorized bands

Where a numeric export is compared, tolerance is **the precision the export prints, and
nothing wider** — for KiCad's integer-nanometre board unit that means exact-integer nm
for coordinate exports, and "round to the same printed string" for `stats`-style
figures. We explicitly **refuse pre-approved tolerance bands** — "a pre-approved
tolerance band is the shape of thing that silently absorbs a real bug" (pcb DL-0010).
This only applies if STEP/geometry conformance is ratified ([DL-0012]).

---

## 4. Normalization layer

`kicad-cli` output is deterministic in *geometry* but carries build/time/identity noise
in headers and IDs. The runner strips these **before** any `structured` or `golden-*`
compare. Two halves:

**Environment pinning (prevents noise at the source):** every adapter call runs with
`LC_ALL=C.UTF-8` (decimal separator / thousands grouping leak into numbers) and
`TZ=UTC` (timestamps), from a fixed working directory (so absolute/temp paths don't
leak). This is not post-hoc scrubbing; it removes whole classes of drift up front.

**Post-hoc normalizers (per output kind).** Each normalizer documents the *observed*
run-to-run difference that motivates it, and is proven load-bearing by a determinism
test (§4a). Concrete sources to strip — the union of the pcb `normalize.rs` findings and
the CLI research:

| Output kind | Strip / normalize |
|---|---|
| s-expr (upgrade) | `(generator_version "…")`, sometimes `(generator …)`. **Keep** `(version YYYYMMDD)` — it is the compatibility key; a bump means *re-baseline the golden*, not strip. Canonicalize fresh UUIDs only if the operation minted them. |
| Gerber (RS-274X) | `G04` header lines: `TF.CreationDate,<ISO>`, `TF.GenerationSoftware,KiCad,Pcbnew,<ver>`, and the "Created by KiCad … date" comment. |
| Excellon drill | header creation date + KiCad version. |
| Drill report | "Created on" wall-clock stamp. |
| DRC/ERC JSON | drop top-level `date`, `kicad_version`, absolute input path; sort `violations`, `unconnected_items`, `schematic_parity` and each violation's `items[]` by content-derived order (not by UUID). |
| pos / GenCAD / IPC-2581 / IPC-D-356 / ODB++ | generation timestamp + tool/version; IPC-D-356 trailing `S…` serial on `VIA` records only (keep meaningful `S0/S1/S2` on pads); zip/xml mtimes for ODB++/IPC-2581. |
| netlist / BOM | header line with tool name, version, date; BOM row order only deterministic with a fixed sort/group spec. |
| SVG | tool comment; canonicalize FP coordinate number formatting. |
| PDF | `/CreationDate`, `/ModDate`, random `/ID`, producer. **Least diffable — avoid PDF for conformance**; prefer SVG/plot text. |
| STEP / BREP (OCC) | ISO-10303 `FILE_NAME` timestamp/author/system; entity ordering + FP tessellation not byte-stable across OCC versions → compare geometrically, not textually. |

**Honesty rule (from pcb):** where an output is provably byte-identical run-to-run
(the pcb harness found `pos` and `dxf` needed *no* normalizer across every board
tried), add **no** normalizer — "an identity normalizer would imply a nondeterminism
that does not exist." And some outputs are *irreducibly* nondeterministic (a board
whose DRC violation *set itself* wobbles run-to-run): those fixtures are named and
excluded, not papered over.

### 4a. Proving a normalizer is load-bearing

A determinism test runs each verb **twice on the same fixture** and asserts the
normalized outputs are byte-identical. Every normalizer must be watched to make that
test go **red when disabled** — a normalizer that never changes anything is either dead
or masking something, and either way must be justified. "A test that cannot fail is not
evidence."

---

## 5. Goldens: per-version, oracle-authored, regenerable

Goldens live inside each case at `golden/<kicad-version>/…` (e.g. `golden/10.0.5/`).
Rationale and mechanics:

- **Keyed by reference-oracle version, not by adapter.** The golden is "the correct
  answer as defined by KiCad `<ver>`." A second adapter compares *its* output against
  the same golden; there is no per-adapter golden.
- **A single input fixture can drive multiple goldens** — one board's `case.toml` may
  declare `drc` (structured, no stored golden — the expected reduction is derived from
  the golden JSON), `export-gerbers` (`golden-dir`), and `export-drill` (`golden-dir`),
  each producing its own artifact under `golden/10.0.5/`.
- **Regeneration story.** `python -m runner --regenerate` runs the reference adapter at
  the currently-installed/pinned `kicad-cli`, normalizes, and writes
  `golden/<detected-version>/`. The contributor **inspects the diff** and commits.
  Goldens are regenerated when the pinned `kicad-cli` changes, or when a fixture's
  format `(version YYYYMMDD)` token bumps. Multiple version subdirs coexist; old ones
  are retained until a version is dropped from the support matrix.
- **Never hand-authored.** A hand-written golden encodes a human's belief about KiCad;
  a generated one encodes KiCad's behavior. Only the latter is a conformance reference.

---

## 6. Versioning strategy

- **Primary target: KiCad 10.0.5** — newest stable; KiCad 11 is unreleased ([DL-0001]).
- **Version-parametric matrix.** The runner detects the oracle version and looks for
  `golden/<version>/`. CI pins `kicad/kicad:10.0.5` (exact patch, ideally by digest) as
  the gating job, plus a **non-gating** `kicad/kicad:nightly` (10.99) job that tracks the
  moving KiCad-11 target and reports drift without failing the build.
- **How 11 slots in.** When `kicad/kicad:11.0` / `:11.0.0` tags publish (~early 2027),
  add a matrix entry and run `--regenerate` to populate `golden/11.0.0/`. Fixtures are
  unchanged; only goldens are version-specific. No case is gated on 11.
- The KiCad **format `(version YYYYMMDD)`** token — not the app version — is the true
  compatibility key; the app major only loosely predicts it. Fixtures record which
  format version they were authored at.

---

## 7. Line-coverage strategy (scheduled infra, not per-PR)

**Goal:** run the whole suite against an instrumented KiCad and see which KiCad *source*
lines are exercised, to find suite gaps. This is the development loop that finds
uncovered branches the format docs don't mention.

**Honest cost.** There is **no turnkey coverage mode** — KiCad's CMake has no coverage
option. It requires a **from-source, Debug `-O0`, `--coverage`-instrumented build**. The
build is dominated by **OpenCASCADE** (a multi-hundred-MB dependency); a full
instrumented build is **~30-90 min on a strong machine and plausibly 2-4+ hours on a
stock 2-vCPU hosted runner**, near disk/time limits. `-O0 --coverage` also inflates the
binary and slows every `kicad-cli` invocation several-fold.

**Approach:**

1. Build **once per pinned KiCad revision** on a **self-hosted / beefy runner** (not
   `ubuntu-latest`); cache the instrumented tree.
2. Run the **whole suite once** against the instrumented `kicad-cli`; each invocation
   flushes `.gcda` cleanly on exit.
3. Merge with `lcov`/`gcovr` (version-matched to the build's GCC), filter `/usr`,
   `thirdparty/`, `qa/`, emit HTML + an lcov/cobertura summary.
4. **Treat uncovered KiCad modules as suite gaps** and file new cases for them — this is
   how the suite grows beyond what the (lagging) format docs describe.

**Cadence: scheduled (weekly / per-KiCad-bump), never on the PR hot path.** The
`tools/coverage/` scripts and the `.github/workflows/coverage.yml` schedule land in M6.
See [DL-0006].

---

## 8. Runner implementation choice

**Decision: a small Python 3.11+ runner using only the standard library** (`tomllib`
for manifests, `subprocess`, `json`, `pathlib`). `pytest` may wrap it for local dev
ergonomics, but the canonical entrypoint is `python -m runner`, mirroring openjd's
plain `run_*_tests.py`. See [DL-0002].

Weighing the options against the priorities (portability, CI-friendliness, low
contributor friction, must shell out to `kicad-cli`/Docker, adapter boundary must stay
language-agnostic):

| | Python 3.11 + stdlib runner | Rust/Go single-binary CLI |
|---|---|---|
| Contributor friction | **Lowest** — EDA/hardware contributors and AI agents read/write Python readily; openjd precedent is Python | Higher — compile toolchain, fewer EDA contributors fluent |
| CI friendliness | Python ships in the `kicad/kicad` Docker image and every runner | Must build/ship a binary per platform; adds a build step |
| Runtime deps | **Zero** third-party (TOML via stdlib `tomllib`, no PyYAML) | Zero at runtime, but a build toolchain at dev time |
| Shelling out | `subprocess` is trivial and portable | Also easy |
| Distribution as a static binary | N/A | **Advantage** — one artifact, no interpreter |
| Speed on huge sweeps | Adequate; runner time is dwarfed by `kicad-cli` | Faster, but the bottleneck is always `kicad-cli` |

Python wins on the two priorities that matter most here (contributor + agent friction,
and being already-present in the KiCad Docker image). Rust/Go's single-binary edge is
real but the runner is I/O-bound on `kicad-cli`, so raw speed is not the constraint.
Critically, **the adapter boundary is a subprocess contract (§2)**, so choosing Python
for the runner does *not* impose Python on any implementation-under-test — the pcb Rust
engine or a Go tool is driven identically.

---

## 9. What stays honest (hard parts, stated plainly)

- **Nondeterminism is real and partly irreducible.** Most is normalizable; some fixtures
  (wobbling DRC sets) must be named and excluded. Never add an identity normalizer.
- **Goldens are per-version and will churn** on every pinned-`kicad-cli` bump. That is
  the cost of KiCad-as-oracle; the regenerate flow + version subdirs manage it.
- **Coverage is expensive infra**, not a check. Budget it as a weekly self-hosted job.
- **The runner is a reference, not the spec.** The contract is the case file format
  (`TEST_CASE_FORMAT.md`) and the verb protocol (§2). Keep them documented so alternate
  runners don't drift.
- **A second adapter *will* diverge from KiCad** somewhere. Those divergences are
  triaged in a checked-in ledger (verdict per entry: "KiCad/golden right, fix the tool"
  vs "suite is wrong"), so the suite can be stricter than any one tool without hiding
  regressions. See [DL-0009] and the openjd `OPENJD_TEST_RESULTS.md` precedent.
