# Design — kicad-conformance architecture

This document defines the architecture. Companion docs:
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) (how to author a case),
[`VALIDATION.md`](VALIDATION.md) (what a check actually compares — the composite `model`
projection and its exact schema, [DL-0022]–[DL-0024]),
[`DECISIONS.md`](DECISIONS.md) (numbered rationale), [`ROADMAP.md`](ROADMAP.md),
[`DIVERGENCES.md`](DIVERGENCES.md) (the checked-in known-divergence ledger, [DL-0009]/[DL-0018]).

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
   │ EXPECTED │ ◀── regenerate ────────  │ verdict  │              │ pcb / others │
   │ per-ver  │    (reference oracle)    │ pass/fail│              │ (ecosystem)  │
   └──────────┘                          └──────────┘              └──────────────┘
```

- **Corpus of input fixtures + declarative expectations.** A *case* is a directory: a
  tiny `case.toml` manifest, one input fixture, and (unless it is a failure case) an
  `expected/<version>/` tree holding the recorded correct answer. Expectations are
  declarative data, never code.
- **Runner.** Walks `suites/`, reads each `case.toml`, invokes the adapter once per
  declared check, applies the normalization layer, and decides pass/fail. It is a
  reference harness, not the source of truth — the *file format* is the contract (an
  SDK-based implementation may write its own runner against the same cases). This is
  the single most important lesson from the OpenJobDescription prior art.
- **Adapter.** Abstracts the implementation-under-test behind a fixed set of
  **capability verbs** exchanged over a **subprocess protocol** (§2). The **reference
  adapter wraps `kicad-cli`**; others (a Rust engine, a viewer) implement the same
  verbs.
- **Expected files.** The recorded correct answer for a check, **produced by the
  reference oracle (KiCad)** at a pinned version and stored per version. Never
  hand-written. Any adapter's output is compared against the KiCad-recorded answer —
  KiCad is authoritative. (Other test frameworks call this a snapshot, a baseline, or a
  golden file; this repo used to say "golden" too — see [DL-0023].)

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
| `model` | `.kicad_pcb` / `.kicad_sch` | **one merged `model.json`** — the composite semantic projection | board: `pcb export stats` + `pcb export pos` + `pcb export ipcd356`; schematic: `sch export netlist` ([`VALIDATION.md`](VALIDATION.md) §4) |
| `parse-sch` | `.kicad_sch` | success/failure only | `sch upgrade --force` on a **scratch copy** (rewrites in place) |
| `parse-pcb` | `.kicad_pcb` | success/failure only | `pcb upgrade --force` on a scratch copy (rewrites in place) |
| `parse-sym` | `.kicad_sym` | success/failure only | `sym upgrade --force -o <out> <in>` |
| `parse-fp` | `.pretty` **dir** (never a lone `.kicad_mod`) | success/failure only | `fp upgrade --force -o <dir> <in_dir>` |
| `erc` | `.kicad_sch` | normalized violation set | `sch erc --format json --severity-all -o <out>/erc.json` |
| `drc` | `.kicad_pcb` | normalized violation set | `pcb drc --format json --units mm --severity-all -o <out>/drc.json` |
| `netlist` | `.kicad_sch` (root) | net→node membership | `sch export netlist --format kicadsexpr\|kicadxml -o <out>/netlist.net` |
| `pos` | `.kicad_pcb` | placement rows | `pcb export pos --format csv --side both --units mm -o <out>/pos.csv` |
| `ipcd356` | `.kicad_pcb` | board net graph + test-point geometry | `pcb export ipcd356 -o <out>/board.d356` |
| `stats` | `.kicad_pcb` | inventory report | `pcb export stats --format json -o <out>/stats.json` |
| `render` | any of the four | one SVG per invocation | `pcb\|sch\|sym\|fp export svg` (dispatches on the input suffix) |
| `export-gerbers` | `.kicad_pcb` | **exit code only** — no comparator exists ([DL-0024]) | `pcb export gerbers --layers <pinned> --no-protel-ext -o <out>/` |
| `export-drill` | `.kicad_pcb` | **exit code only** — no comparator exists ([DL-0024]) | `pcb export drill --generate-report --report-path <r> -o <dir>` |
| `export-step` | `.kicad_pcb` | reserved, unused | `pcb export step` (heavy, least deterministic; see [DL-0012](DECISIONS.md)) |

Retired verbs ([DL-0024]): **`upgrade`** (it existed only to byte-compare KiCad's
re-serialized s-expr) and **`bom`** (a BOM is the schematic model's `components` section
by another name). The four `export-svg-{pcb,sch,sym,fp}` verbs collapsed into one
`render`; `export-pos`/`export-stats`/`export-ipcd356` lost their `export-` prefix.

Notes drawn from the research, load-bearing for correct mapping:

- **`parse-*` has no dedicated subcommand.** There is no pure "parse and stop" verb in
  `kicad-cli`; `… upgrade --force` loads the file (proving it parses) and re-emits it. The
  re-emitted bytes are no longer compared against anything — `parse-*` is an
  **exit-polarity check only**, which is exactly what a `failure/` case needs. On a happy
  case a passing `model` already proves the file parsed, so a `parse-*` check beside it is
  redundant. `--force` is always passed so the result never depends on the input's
  pre-existing version stamp.
- **`model` composes inside the adapter, not inside the runner.** The reference adapter
  runs the two or three `kicad-cli` exports into its scratch dir and writes
  `<out>/model.json` itself. A non-KiCad implementation therefore emits its model
  directly, without having to imitate three KiCad export formats — the model schema
  ([`VALIDATION.md`](VALIDATION.md) §4), not the exports, is the contract.
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
- **`parse-fp` takes a `.pretty` directory, never a lone `.kicad_mod`.** Empirically,
  `fp upgrade --force -o out fp.kicad_mod` fails with `Unable to convert library`
  (exit 2); it must be pointed at a library *directory* (`fp upgrade --force -o out
  mylib.pretty` → exit 0, writing `out/fp.kicad_mod`). Footprint fixtures are therefore
  authored as a `.pretty` dir, or the adapter wraps a lone `.kicad_mod` into a temp
  `.pretty` before invoking `fp upgrade`.

### 2a. Output-artifact / `-o` handling per verb (the runner always passes an explicit path)

The empirical gotcha (KiCad 10.0.5): with **no** `-o`, several verbs do *not* write to
stdout — they write a **derived filename in the current working directory**, which is
not a name the bare `<verb> --out <dir>` shape mentions, so the runner cannot reliably
find the artifact. Observed default (no-`-o`) behavior:

- `pcb drc … board.kicad_pcb` → writes `board-drc.json` (`<input-stem>-drc.json`) in CWD.
- `sch erc … good.kicad_sch` → writes `<stem>-erc.json` in CWD.
- `sch export netlist … good.kicad_sch` → silently writes `good.net` (`<stem>.net`) in CWD.
- `pcb export pos` / `sch export bom` → write a derived-name file in CWD.
- `pcb export gerbers` / `pcb export drill` → write a *set* of files into the `-o`/`--out`
  directory; membership is a function of board state (see §2b).

**Rule: the runner never relies on a derived name.** It creates a per-check scratch
`--out` directory and passes the adapter an **explicit** output path/dir; the adapter
forwards it to the corresponding `kicad-cli -o/--output`. The runner then reads back the
exact path it dictated. Per-verb the runner tells the adapter to write:

**The adapter also copies the input itself to an isolated scratch dir for *every* verb,
not only the in-place `upgrade` ones.** The obvious reason is `pcb`/`sch upgrade`
rewriting in place (§2 above); the less obvious, empirically-found reason is that
`kicad-cli` writes a side-effect file next to a board it merely **reads** — `pcb drc` and
`pcb export gerbers` both leave a `.kicad_prl` (project-local-settings cache) alongside
the input, even though neither operation is conceptually a write. Left uncontrolled, that
side effect would land inside `suites/` next to the committed fixture. So every verb's
input is copied into a fresh scratch directory before invoking `kicad-cli`, and
`kicad-cli` only ever sees the scratch copy — the committed fixture path is never passed
to it directly, whether the verb is a read-only report (`drc`, `erc`, exports) or an
in-place rewrite (`upgrade`).

| Verb | Adapter passes to `kicad-cli` | Artifact the runner reads |
|---|---|---|
| `model` (pcb) | `-o <scratch>/stats.json`, `<scratch>/pos.csv`, `<scratch>/board.d356`, merged | `<out>/model.json` |
| `model` (sch) | `-o <scratch>/netlist.net`, reduced | `<out>/model.json` |
| `drc` | `-o <out>/drc.json` | `<out>/drc.json` |
| `erc` | `-o <out>/erc.json` | `<out>/erc.json` |
| `netlist` | `-o <out>/netlist.net` | `<out>/netlist.net` |
| `pos` | `-o <out>/pos.csv` | `<out>/pos.csv` |
| `ipcd356` | `-o <out>/board.d356` | `<out>/board.d356` |
| `stats` | `-o <out>/stats.json` | `<out>/stats.json` |
| `render` (pcb) | `-o <out>/render.svg` (+ `--layers` from `args`) | `<out>/render.svg` |
| `render` (sch/sym/fp) | `-o <out>/` — kicad-cli derives `<stem>.svg` | `<out>/<stem>.svg` |
| `export-gerbers` | `-o <out>/` (+ pinned `--layers`, `--no-protel-ext`) | — (exit only) |
| `export-drill` | `-o <out>/` `--report-path <out>/drill-report.rpt` | — (exit only) |
| `parse-sch`/`parse-pcb` | (no `-o`; rewrites in place) | — (exit only) |
| `parse-sym`/`parse-fp` | `-o <out>` (path must **not** pre-exist — see gotcha above) | — (exit only) |

This keeps every artifact location deterministic and avoids CWD pollution.

### 2b. Layer sets are explicit case parameters, not fixed lists

For `render`, the layer set is a per-case `args` parameter (`args = ["--layers", "F.Cu"]`)
— a render case names the layer it is about.

The same used to matter, much more sharply, for gerbers: a default
`pcb export gerbers` on a 2-layer board emits **seven** files, not four (KiCad plots every
*enabled/plottable* layer, adding `F_Courtyard`/`B_Courtyard`/`Margin`, with Protel
`.gtl/.gbl/.gm1` extensions), so the output file set was a function of board state. That
mattered when the gerber output was compared as a directory of files; with the byte layer
gone ([DL-0024]) the gerber verb is exit-only, and the pinned
`--layers F.Cu,B.Cu,Edge.Cuts --no-protel-ext` invocation is retained purely so that any
future gerber comparison starts from a deterministic file set.

### 2c. Parser error-verbosity is asymmetric between PCB and schematic (observed oracle behavior)

Failure-case authors must expect *different* stderr per format, because KiCad's two
loaders differ:

- **PCB** (`pcb upgrade`) on malformed input surfaces parse position:
  `Failed to load board: Expecting '(' in '…', line 2, offset 1.` A PCB failure case
  **may** assert the specific `Expecting` substring. (Caveat: on 10.0.5 this path also
  *crashes* after printing — see §3a and [DL-0013].)
- **Schematic** (`sch upgrade`) collapses *every* defect — unterminated, truncated,
  unknown token, missing `(version)` — to the same generic `Failed to load schematic`
  (exit 3), with no position. A schematic failure case **cannot** discriminate the defect
  via stderr; it pins the coarse message and relies on the positive control (§3a) to prove
  which defect fired.

---

## 3. Comparison model

Pass/fail is decided per check, and **how** a check is compared follows from its `op` —
there is no `compare` field to set ([DL-0023]). Three kinds of comparison exist:

| Kind | Ops | What it compares |
|---|---|---|
| **exit** (§3a) | `parse-*`, `export-gerbers`, `export-drill` | did the tool accept (exit 0) or gracefully reject the input |
| **model / projection** (§3b) | `model`, `drc`, `erc`, `netlist`, `pos`, `ipcd356`, `stats` | a normalized JSON document, compared for equality |
| **render** (§3c) | `render` | the drawn SVG geometry, byte-exact after normalizing `<title>`/`<desc>` |

A previous revision numbered these L0–L3 and included a fourth kind, **L1**, which
compared KiCad's re-serialized bytes (canonical s-expr, gerber files, drill files). L1 and
the `golden-file`/`golden-dir` modes that implemented it are **deleted** ([DL-0024]); the
L-numbering is retired with them. [`VALIDATION.md`](VALIDATION.md) is the full spec of
what each kind compares, including the `model` schema.

### 3a. exit — success/failure polarity (+ error substring)

The baseline, and the *only* thing a `failure` case needs. Mirrors openjd's
filename-polarity trick, moved into the manifest:

- `outcome = "ok"` (the default in a `happy/` directory) → the adapter must exit `0`.
- `outcome = "error"` (the default in a `failure/` directory) → the adapter must exit with
  a **bounded, graceful non-zero** exit — a clean rejection, *not* a crash (see the crash
  verdict below).
- For `outcome = "error"`, an optional `error_contains = "…"` asserts a substring on
  **stderr** (per-stream, not merged, so a warning can't satisfy an error check). An
  `error_contains_any = ["…", "…"]` escape hatch tolerates legitimate wording variation
  between implementations (openjd's `anyOf`).

(The field was called `expect` before [DL-0023]; it was renamed because `expect = "ok"`
sitting next to `expected = "model.json"` read as two spellings of one thing.)

Substring matching is deliberately loose: it pins the *observable contract* (the tool
rejects a malformed board and says something about the offending token) without
over-fitting to KiCad's exact phrasing, so a second adapter with different error text
still conforms.

**Crash verdict — a crash is NEVER a pass ([DL-0013]).** A malformed input can make the
oracle *crash* rather than reject cleanly: on 10.0.5, a truncated board makes
`pcb upgrade` print a good `Expecting '('` message and then **segfault** (exit 139 on
native Windows; a `SIGSEGV` on Docker Linux). 139 is non-zero, so a naïve "non-zero =
rejected" rule would silently *pass* an `outcome="error"` case on a **crash** — building
the entire PCB `failure/` corpus on a KiCad bug. The runner therefore classifies
termination into three outcomes, not two:

| Outcome | Detection (portable — do **not** hard-code 139) | Counts as |
|---|---|---|
| `OK` | exit `0` | pass for `happy` |
| `REJECT` | bounded non-zero exit (roughly `1..128`), process exited normally | pass for `failure` only |
| `CRASH` | killed by a signal, or exit code `> 128` (128 + signal); on POSIX inspect `WIFSIGNALED`/`WTERMSIG`; on Windows treat a fatal exception status (`0xC0000005` etc.) or code `> 128` as a signal-equivalent | **never a pass** — not for `happy`, not for `failure` |

A `CRASH` is reported as its own verdict and, for `happy` cases, is distinct from a
normal failure so it is never mistaken for a clean mismatch. Because the exit *code*
differs across platforms (Windows-native vs Docker-Linux), detection is by
signal / `>128` semantics, never the literal 139. Known oracle crashes (the 10.0.5 PCB
parse-failure segfault) are filed upstream and recorded in the divergence
ledger ([`DIVERGENCES.md`](DIVERGENCES.md)); the paired PCB failure case asserts the real
`Expecting` substring so a future KiCad that rejects *cleanly* (no crash) still conforms.

**Adapter requirement: relay a child's crash, don't launder it.** The runner's direct
subprocess child is the *adapter*, never `kicad-cli` itself (§1, [DL-0007]) — so this
classifier only ever sees the adapter's own `returncode`. For a signaled `kicad-cli` to
be visible as a `CRASH` through that indirection, **any** adapter satisfying this
contract must re-signal itself the same way the reference adapter does: when its
`kicad-cli` child is killed by a signal, the adapter re-raises the identical signal
against itself (`os.kill(os.getpid(), sig)` in `runner/adapters/kicad.py`'s
`run_and_relay`) rather than exiting with some ordinary nonzero code. Skipping this would
silently launder a genuine crash into what looks like an adapter-level `REJECT`.

**Positive control — every `failure` case must be falsifiable ([DL-0013]).** Because
stderr on the schematic side cannot discriminate *which* defect fired (all defects →
`Failed to load schematic`), an `outcome="error"` case must ship a runner-enforceable
positive control: **removing the injected defect must make the same check exit 0.** The
runner supports this by re-running the check against a defect-free variant (a sibling
"control" fixture, or an inline patch declared in the manifest) and requiring the control
to reach `OK`. A `failure` case whose control does not flip to `OK` is reported as
**not-evidence**, never as passed — "a test that can't fail is not evidence."

**Known-oracle-divergence — strict xfail ([DL-0018]).** The OK/REJECT/CRASH verdict
above is never edited per-case — but a case may declare that the reference oracle itself
is known, and tracked, to diverge from the behavior it otherwise asserts, via a
`known_divergence` table (`reason`, `kind`, optional `tracking` — schema in
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §4.3). This is a *presentation layer* on top
of the verdict, applied only after any positive control has already passed:

- If the actual verdict matches the declared `kind` (e.g. `CRASH` for `kind = "crash"`),
  the check is scored **`XFAIL`** ("known divergence") instead of `FAIL`/`CRASH` — the
  build stays green.
- If the same check instead reaches its normally-desired outcome (the oracle got fixed —
  a clean `OK`/graceful `REJECT`), that is an **`XPASS`**, which **fails the build** with
  a message pointing at [`DIVERGENCES.md`](DIVERGENCES.md): unlike a conventional
  test-framework xfail, an XPASS here is never silently tolerated, so the ledger and the
  `known_divergence` marker cannot quietly rot once the underlying bug is fixed.
- `XFAIL`/`XPASS` are new, separately-counted verdicts in the summary alongside
  `PASS`/`FAIL`/`CRASH`/`SKIP`/`NOT-EVIDENCE`/`NEEDS-REGEN`. A case with no
  `known_divergence` is entirely unaffected.

This is how `board-parse/failure/0001-unterminated-sexpr` (the 10.0.5 PCB-parse segfault,
§3a above) reports green without either loosening its `outcome="error"`/`error_contains`
assertion or leaving the gating build permanently red over a bug filed upstream, not in
this repo. See [DL-0018] and [`DIVERGENCES.md`](DIVERGENCES.md) for the full rationale
and the ledger entry.

### 3b. model / projection — one normalized JSON document

For outputs where formatting, ordering and internal IDs are irrelevant, a byte compare is
meaningless. The runner turns both sides into a canonical JSON structure and compares them
for equality. The **default** projection is `model` — one document describing everything
the tool understood about the input, composed from several exports
([`VALIDATION.md`](VALIDATION.md) §4). The narrower projections behave identically, each
producing its own document:

- **`model`** → board: `counts`, `drill_holes`, `has_outline`, `min_track_width`,
  `min_drill_diameter`, `placement`, `nets`; schematic: `components`, `nets`. Full schema
  and worked examples in [`VALIDATION.md`](VALIDATION.md) §4.
- **`netlist`** → `{ net-name : sorted ["REFDES.PIN", …] }`. A pin on the wrong node, a
  split net, a misnamed net fails; formatting, net codes and emission order never do. The
  raw netlist embeds the absolute source path, a `(date …)` and `(tool "Eeschema …")`,
  which is why it is never compared as text. A multi-sheet schematic has one **root**
  sheet handed to `sch export netlist`; the case names it with the explicit `root` field,
  and the adapter reproduces the subsheets' relative on-disk layout in scratch so
  child-sheet resolution works.
- **`drc` / `erc`** → sorted list of `(rule-id, severity, sorted item locations)`. Sorted
  by content, **not by UUID** — some violation-item UUIDs are minted fresh each run.
- **`pos` / `ipcd356` / `stats`** → the same reductions `model` composes, emitted
  standalone ([`VALIDATION.md`](VALIDATION.md) §5).

**Numbers.** Tolerance is **the precision the export prints, and nothing wider.** KiCad
prints coordinates as fixed-precision decimal strings (`pos`: 6 decimals of a millimetre =
1 nm = KiCad's own integer board unit), and the reductions keep those strings verbatim, so
string equality *is* printed-quantum tolerance — no float parsing and no band. We
explicitly **refuse pre-authorized tolerance bands**: the moment a band is wider than the
export's own printed precision, a genuine coordinate error can hide inside it. Values that
cannot be compared exactly across implementations — computed float areas and densities —
are **excluded from the model** rather than compared loosely
([`VALIDATION.md`](VALIDATION.md) §4.1).

**What is stored as the expected file ([DL-0014], [DL-0023]).** The recorded answer is the
**normalized document itself** (`model.json`, `drc.json`, …) committed under
`expected/<version>/`, *not* the raw KiCad JSON/s-expr/CSV. `--regenerate` runs the
oracle, applies the reduction, and writes that document; at compare time the runner
reduces the adapter's output the same way and compares. Storing the reduction makes the
expected file self-describing, makes diffs read as semantic changes, and makes the shape a
second implementation must emit explicit.

Residue is **characterized, not hidden**: when a comparison fails, the runner reports
*how* (names-only difference vs membership difference vs count difference), so a mismatch
is actionable rather than an opaque "differs."

### 3c. render — normalized SVG compare

`op = "render"` exports the drawing to SVG, normalizes the one nondeterministic line
(`<title>`, which carries the output filename and a wall-clock date) plus `<desc>`, and
compares **byte-exact**. Zero tolerance: KiCad's SVG path geometry is byte-stable
run-to-run (verified, [`VALIDATION.md`](VALIDATION.md) §6), and determinism is pinned at
the source with `--page-size-mode 2 --exclude-drawing-sheet --black-and-white` plus
`LC_ALL=C.UTF-8`/`TZ=UTC`.

The cross-implementation variant — rasterize both sides with a pinned `resvg` and
pixel/SSIM-diff under an explicit, per-case, load-bearing threshold — arrives with the
second adapter ([DL-0021]); no KiCad-vs-KiCad check ever rasterizes.

### 3d. There is no byte-compare mode ([DL-0024])

Earlier revisions had a fourth mode, `golden-file`/`golden-dir`, which compared KiCad's
re-serialized bytes: the canonical `.kicad_pcb`/`.kicad_sch` from `… upgrade`, the gerber
file set, the drill file set. It is **deleted**, along with the `upgrade` and `bom` verbs
that only existed to feed it.

The reason is that a byte compare pins KiCad's exact *formatting* — token order,
whitespace, aperture numbering, comment style. That is a decent KiCad-version-regression
signal and a bad conformance signal: a clean-room implementation emits
valid-but-differently-formatted output and would "diverge" on essentially every such
comparison for reasons that are not bugs. Rather than maintain a whole comparison layer
whose findings must then be filtered back out as formatting-only, the layer is gone. What
it cost us — all gerber and drill coverage — is documented as an explicit gap in
[`VALIDATION.md`](VALIDATION.md) §7 and [`ROADMAP.md`](ROADMAP.md), with the two concrete
ways to get it back.

---

## 4. Normalization layer

`kicad-cli` output is deterministic in *geometry* but carries build/time/identity noise
in headers and IDs. Two halves:

**Most of this layer is now unnecessary.** The reductions in §3b *drop* the noisy fields
by construction — the model never contains a date, a version, a path or a UUID, because
those fields are simply not part of the schema. So there is nothing left to scrub for
`model`/`drc`/`erc`/`netlist`/`pos`/`ipcd356`/`stats`. Only two normalizers survive: the
SVG `<title>`/`<desc>` strip for `render`, and CRLF→LF for stored text. The rest of the
table below is retained as **reference for output kinds this suite does not currently
compare** — chiefly gerber and drill, which a future revision may re-introduce
([`VALIDATION.md`](VALIDATION.md) §7).

**Environment pinning (prevents noise at the source):** every adapter call runs with
`LC_ALL=C.UTF-8` (decimal separator / thousands grouping leak into numbers) and
`TZ=UTC` (timestamps), from a fixed working directory (so absolute/temp paths don't
leak). This is not post-hoc scrubbing; it removes whole classes of drift up front.

**Post-hoc normalizers (per output kind).** Each normalizer documents the *observed*
run-to-run difference that motivates it, and is proven load-bearing by a determinism
test (§4a). Concrete sources to strip — the union of prior clean-room-engine
normalization findings and this project's own CLI research:

| Output kind | Status | Strip / normalize |
|---|---|---|
| **SVG** (`render`) | **live** | `<title>` (output filename + wall-clock date) and `<desc>` → a constant. The only run-to-run difference KiCad's SVG has (verified, [`VALIDATION.md`](VALIDATION.md) §6). |
| **All text written to `expected/`** | **live** | CRLF → LF (see below). |
| model / netlist / pos / ipcd356 / stats | *not needed* | The reduction drops `metadata`, the `(design …)` header, paths, dates, tool versions, UUIDs and net codes by construction — there is nothing left to strip ([`VALIDATION.md`](VALIDATION.md) §4.6). |
| DRC/ERC JSON | *live, inside the reduction* | drop top-level `date`, `kicad_version`, absolute input path; sort `violations`, `unconnected_items`, `schematic_parity` and each violation's `items[]` by content-derived order (not by UUID). |
| s-expr (`… upgrade`) | *not compared* | (was: `(generator_version …)`; the whole comparison is deleted, [DL-0024]) |
| Gerber (RS-274X) | *not compared* | `G04` header lines: `TF.CreationDate,<ISO>`, `TF.GenerationSoftware,KiCad,Pcbnew,<ver>`, and the "Created by KiCad … date" comment. |
| Gerber job file (`.gbrjob`) | *not compared* | JSON `CreationDate` under `Header/CreationDate` and `Header/GenerationSoftware/Version`. |
| Excellon drill / drill report | *not compared* | header creation date + KiCad version; the report's "Created on" stamp. |
| BOM | *not compared* | header line with tool name, version, date; row order only deterministic with a fixed sort/group spec. |
| PDF | *not compared* | `/CreationDate`, `/ModDate`, random `/ID`, producer. **Least diffable — avoid PDF for conformance.** |
| STEP / BREP (OCC) | *deferred ([DL-0012])* | ISO-10303 `FILE_NAME` timestamp/author/system; entity ordering + tessellation not byte-stable across OCC versions → compare geometrically, not textually. |

The "not compared" rows are kept deliberately: they are the research that a future gerber
or drill comparison would otherwise have to redo ([`VALIDATION.md`](VALIDATION.md) §7).

**Line endings & the canonical platform ([DL-0016]).** Text written to `expected/` is
normalized to **LF** and stored **LF**. A contributor may develop on Windows, but CI
compares inside the `kicad/kicad:10.0.5` **Docker (Linux)** image, and the native Windows
binary writes CRLF (and can leak `\` path separators into messages) — so a
Windows-regenerated answer would mismatch a Linux-CI run on line endings alone. Two
measures: (1) CRLF↔LF conversion before writing and before comparing; (2) **committable
expected files are regenerated inside the Docker Linux image**, even when authoring on
Windows. A `.gitattributes` entry marks `expected/**` (and text fixtures) as LF so git
does not re-mangle them on checkout.

**Honesty rule:** where an output is provably byte-identical run-to-run (prior empirical
work found `pos` and `dxf` needed *no* normalizer across every board tried), add **no**
normalizer — "an identity normalizer would imply a nondeterminism that does not exist." And some outputs are *irreducibly* nondeterministic (a board
whose DRC violation *set itself* wobbles run-to-run): those fixtures are named and
excluded, not papered over.

### 4a. Proving a normalizer is load-bearing

A determinism test runs each verb **twice on the same fixture** and asserts the
normalized outputs are byte-identical. Every normalizer must be watched to make that
test go **red when disabled** — a normalizer that never changes anything is either dead
or masking something, and either way must be justified. "A test that cannot fail is not
evidence."

---

## 5. Expected files: per-version, oracle-authored, regenerable

The recorded correct answers live inside each case at `expected/<kicad-version>/…` (e.g.
`expected/10.0.5/model.json`). Rationale and mechanics:

- **Keyed by reference-oracle version, not by adapter.** An expected file is "the correct
  answer as defined by KiCad `<ver>`." A second adapter compares *its* output against the
  same file; there is no per-adapter answer.
- **Usually exactly one per case.** The `model` verb collapses what used to be several
  per-projection answers into one `model.json` ([DL-0022]). A case has a second expected
  file only when it documents a second, genuinely different concept about the same input —
  in practice a `render`.
- **Regeneration story.** `python -m runner --regenerate` runs the reference adapter at
  the pinned `kicad-cli`, applies the reduction (and CRLF→LF), and writes
  `expected/<detected-version>/`. For committable answers, run `--regenerate` **inside the
  `kicad/kicad:10.0.5` Docker Linux image** so the stored bytes are platform-canonical
  ([DL-0016]); a Windows-native regenerate is fine for local iteration only. The
  contributor **inspects the diff** and commits. Answers are regenerated when the pinned
  `kicad-cli` changes, or when an input's format `(version YYYYMMDD)` token bumps.
  Multiple version subdirs coexist; old ones are retained until a version is dropped from
  the support matrix.
- **Never hand-authored.** A hand-written answer encodes a human's belief about KiCad; a
  generated one encodes KiCad's behaviour. Only the latter is a conformance reference.

---

## 6. Versioning strategy

- **Primary target: KiCad 10.0.5** — newest stable; KiCad 11 is unreleased ([DL-0001]).
- **Version-parametric matrix.** The runner detects the oracle version and looks for
  `expected/<version>/`. CI pins `kicad/kicad:10.0.5` (exact patch, ideally by digest) as
  the gating job, plus a **non-gating** `kicad/kicad:nightly` (10.99) job that tracks the
  moving KiCad-11 target and reports drift without failing the build.
- **How 11 slots in.** When `kicad/kicad:11.0` / `:11.0.0` tags publish (~early 2027),
  add a matrix entry and run `--regenerate` to populate `expected/11.0.0/`. Inputs are
  unchanged; only the recorded answers are version-specific. No case is gated on 11.
- The KiCad **format `(version YYYYMMDD)`** token — not the app version — is the true
  compatibility key; the app major only loosely predicts it. Fixtures record which
  format version they were authored at.

---

## 7. Coverage strategy — a cheap proxy now, heavy line-coverage later

Two tiers, deliberately separated so M0 owes nothing to the expensive one.

### 7a. Cheap coverage proxy (runner-emitted, zero KiCad rebuild) — the M0-era gap signal

The early gap-finding signal is **not** source line-coverage — it is a **CLI-surface +
format-token report the runner emits for free**, with no instrumented build:

- **CLI-surface coverage.** Enumerate the `kicad-cli … --help` surface (every subcommand
  × flag) and record which subcommands/flags the suite actually exercises (from each
  check's verb mapping and `args`). **Unexercised subcommands/flags are the gap list.**
- **Format-token coverage.** Track which format `(version YYYYMMDD)` epochs and which
  **top-level s-expr sections** (e.g. `(lib_symbols …)`, `(net …)`, `(footprint …)`,
  `(zone …)`) appear across the fixtures and expected files. **Unexercised top-level sections /
  format tokens are the gap list.**

Both are pure bookkeeping over files the suite already has, emitted as a `--coverage-proxy`
report. This is what M0 relies on for gap-finding. It is honest about being a proxy: it
shows *which surfaces are touched*, not *which KiCad source lines run*.

### 7b. Line-coverage strategy (scheduled infra, not per-PR, not in M0)

**Goal:** run the whole suite against an instrumented KiCad and see which KiCad *source*
lines are exercised, to find deeper suite gaps. This is a later scheduled-infra milestone
([DL-0006], M6) — it gates nothing and is **out of M0's mental model** entirely.

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
- **Expected files are per-version and will churn** on every pinned-`kicad-cli` bump. That is
  the cost of KiCad-as-oracle; the regenerate flow + version subdirs manage it.
- **Coverage is expensive infra**, not a check. Budget it as a weekly self-hosted job.
- **The runner is a reference, not the spec.** The contract is the case file format
  (`TEST_CASE_FORMAT.md`) and the verb protocol (§2). Keep them documented so alternate
  runners don't drift.
- **A second adapter *will* diverge from KiCad** somewhere. Those divergences are
  triaged in a checked-in ledger (verdict per entry: "KiCad's answer is right, fix the
  tool" vs "the suite is wrong"), so the suite can be stricter than any one tool without
  hiding regressions. See [DL-0009] and the openjd `OPENJD_TEST_RESULTS.md` precedent.
- **Gerber and drill output are not covered at all** since the byte layer was deleted
  ([DL-0024]). That is a real hole in a fabrication-facing suite, named in
  [`VALIDATION.md`](VALIDATION.md) §7 and on the roadmap, not quietly absorbed.
