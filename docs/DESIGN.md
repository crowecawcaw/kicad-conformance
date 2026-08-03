# Design — kicad-conformance architecture

This document defines the architecture. Companion docs:
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) (how to author a case),
[`VALIDATION.md`](VALIDATION.md) (what a case actually compares — the **standard answers**
per input type and their exact schemas, [DL-0025]–[DL-0028]),
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

> **Verbs are an adapter-internal vocabulary, not a manifest field.** Since [DL-0025] a
> `case.toml` names no verb: the runner derives the verbs to run from the input file's
> suffix (`.kicad_pcb` → the six board exports, and so on —
> [`VALIDATION.md`](VALIDATION.md) §9.1). The table below is the **adapter contract**,
> which a second implementation still answers. `parse-*` remains as the loader a
> `failure/` case invokes; `model` is renamed `summary` ([DL-0028]); `export-gerbers` and
> `export-drill` are no longer exit-only — they produce compared answers again
> ([DL-0026]).

Each adapter declares which verbs it supports; unsupported verbs cause the relevant
cases to be **skipped and counted**, never failed (openjd's capability-negotiation
idea, expressed in data). Core verbs:

| Verb | Input | Output the runner consumes | `kicad-cli` mapping (10.0.5) |
|---|---|---|---|
| `version` | — | version string on stdout | `version --format plain` (+ `--format about` for the identity record) |
| `summary` | `.kicad_pcb` / `.kicad_sch` | **one merged `summary.json`** — everything the tool understood ([DL-0028]; was `model`) | board: `pcb export stats` + `pcb export pos` + `pcb export ipcd356`; schematic: `sch export netlist` ([`VALIDATION.md`](VALIDATION.md) §4) |
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
| `export-gerbers` | `.kicad_pcb` | **a directory of gerbers**, compared byte-for-byte after normalization ([DL-0026]) | `pcb export gerbers -o <out>/` — **no `--layers`**, no `--no-protel-ext`: KiCad's own set, which is what a fab receives |
| `export-drill` | `.kicad_pcb` | **a directory holding one `.drl`**, compared byte-for-byte after normalization ([DL-0026]) | `pcb export drill -o <dir>/` — no map, no report, no `--excellon-separate-th` |
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
  case a matching `summary` already proves the file parsed, so a parse check beside it is
  redundant. `--force` is always passed so the result never depends on the input's
  pre-existing version stamp.
- **`summary` composes inside the adapter, not inside the runner.** The reference adapter
  runs the two or three `kicad-cli` exports into its scratch dir and writes
  `<out>/summary.json` itself. A non-KiCad implementation therefore emits its summary
  directly, without having to imitate three KiCad export formats — the summary schema
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
| `summary` (pcb) | `-o <scratch>/stats.json`, `<scratch>/pos.csv`, `<scratch>/board.d356`, merged | `<out>/summary.json` |
| `summary` (sch) | `-o <scratch>/netlist.net`, reduced | `<out>/summary.json` |
| `drc` | `-o <out>/drc.json` | `<out>/drc.json` |
| `erc` | `-o <out>/erc.json` | `<out>/erc.json` |
| `netlist` | `-o <out>/netlist.net` | `<out>/netlist.net` |
| `pos` | `-o <out>/pos.csv` | `<out>/pos.csv` |
| `ipcd356` | `-o <out>/board.d356` | `<out>/board.d356` |
| `stats` | `-o <out>/stats.json` | `<out>/stats.json` |
| `render` (pcb) | `-o <out>/render-F_Cu.svg` — a **file** path, and `--layers F.Cu` is mandatory (§2b) | `<out>/render-F_Cu.svg` |
| `render` (sch/sym/fp) | `-o <out>/` — a **directory**; kicad-cli derives the names (`<stem>.svg`, `<Symbol>_unit<N>.svg`, `<Footprint>.svg`) | `<out>/*.svg` |
| `export-gerbers` | `-o <out>/` — a directory, created if absent. **No `--layers`** | `<out>/*` (all of it, compared as a tree) |
| `export-drill` | `-o <out>/` — a directory, created if absent. No report, no map | `<out>/*.drl` |
| `parse-sch`/`parse-pcb` | (no `-o`; rewrites in place) | — (exit only) |
| `parse-sym`/`parse-fp` | `-o <out>` (path must **not** pre-exist — see gotcha above) | — (exit only) |

This keeps every artifact location deterministic and avoids CWD pollution.

### 2b. Layer sets are fixed by the harness for SVG, and taken from KiCad for gerbers

Both are now decisions, not case parameters ([DL-0025], [DL-0026]) — `args` is gone.

**SVG: the harness must choose, because KiCad has no default.** `pcb export svg` refuses
to run without an explicit layer list, in either output mode:

```
$ kicad-cli pcb export svg --mode-multi -o out --page-size-mode 2 \
      --exclude-drawing-sheet --black-and-white board.kicad_pcb
At least one layer must be specified
```

The harness pins **`--layers F.Cu`**, one file, `render-F_Cu.svg`. Justified in
[`VALIDATION.md`](VALIDATION.md) §6.2; briefly, the gerbers now cover per-layer geometry
byte-exactly, so the render's remaining jobs are to be human-readable and to be
rasterizable later, and one layer does both. `sch|sym|fp export svg` need no choice.

Two `-o` gotchas, verified: for `pcb export svg` in its default single mode `-o` is a
**file path** (passing a directory fails with `Failed to create file '<dir>'`); for
`gerbers`, `drill`, and the `sch`/`sym`/`fp` SVG exports it is a **directory**, created if
absent. `pcb export svg` also prints a deprecation notice that its default will become
`--mode-multi`; re-verify at the KiCad 11 bump.

**Gerbers: KiCad chooses, and the choice is part of the answer.** `pcb export gerbers` is
run with **no `--layers`**. KiCad plots the set stored in the board, falling back to its
built-in default when the board has none — verified as **6 gerbers + a job file** for the
populated fixture (which carries a `(pcbplotparams (layerselection …))` block) and **20
gerbers + a job file** for the minimal fixture (which does not). The set varies per board
and is stable per board, which is exactly what a per-board recorded answer needs. Pinning
a list instead would compare an artifact nobody ships and would hide a future change to
KiCad's default selection.

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

**How something is compared follows from what it is** — there is no `compare` field
([DL-0023]) and, since [DL-0025], no `op` field either. The runner looks at the answer's
name and extension. Four kinds of comparison exist:

| Kind | Applied to | What it compares |
|---|---|---|
| **exit** (§3a) | every `failure/` case | did the tool accept (exit 0) or gracefully reject the input |
| **summary / projection** (§3b) | `summary.json`, and the JSON extras (`drc.json`, `pos.json`, …) | a normalized JSON document, compared for equality |
| **render** (§3c) | `render*.svg`, `render/*.svg` | the drawn SVG geometry, byte-exact after normalizing `<title>`/`<desc>` |
| **bytes** (§3d) | `gerbers/`, `drill/` | a directory of fabrication output: same filenames, every file byte-identical after five normalizers (§4) |

A previous revision numbered these L0–L3. The numbering is retired.
[`VALIDATION.md`](VALIDATION.md) is the full spec of what each kind compares, including
the summary schema.

### 3a. exit — success/failure polarity (+ error substring)

The baseline, and the *only* thing a `failure` case needs. Mirrors openjd's
filename-polarity trick — and since [DL-0025] it stays in the filename rather than being
restated in the manifest:

- A case in **`happy/`** → the adapter must exit `0`, and every standard answer must match.
- A case in **`failure/`** → the adapter must exit with a **bounded, graceful non-zero**
  exit — a clean rejection, *not* a crash (see the crash verdict below). No answers are
  recorded or compared.
- A `failure/` case may set `error_contains = "…"`, asserting a substring on **stderr**
  (per-stream, not merged, so a warning can't satisfy an error check). An
  `error_contains_any = ["…", "…"]` escape hatch tolerates legitimate wording variation
  between implementations (openjd's `anyOf`).

(The polarity was a manifest field twice: `expect`, then `outcome` after [DL-0023]. Both
are gone. It was only ever written in cases whose directory already said the same thing,
and a manifest that can contradict its own directory is a manifest with a failure mode
worth deleting.)

Substring matching is deliberately loose: it pins the *observable contract* (the tool
rejects a malformed board and says something about the offending token) without
over-fitting to KiCad's exact phrasing, so a second adapter with different error text
still conforms.

**Crash verdict — a crash is NEVER a pass ([DL-0013]).** A malformed input can make the
oracle *crash* rather than reject cleanly: on 10.0.5, a truncated board makes
`pcb upgrade` print a good `Expecting '('` message and then **segfault** (exit 139 on
native Windows; a `SIGSEGV` on Docker Linux). 139 is non-zero, so a naïve "non-zero =
rejected" rule would silently *pass* a `failure/` case on a **crash** — building
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
`Failed to load schematic`), a `failure/` case must ship a runner-enforceable
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
§3a above) reports green without either loosening its `failure/` polarity or `error_contains`
assertion or leaving the gating build permanently red over a bug filed upstream, not in
this repo. See [DL-0018] and [`DIVERGENCES.md`](DIVERGENCES.md) for the full rationale
and the ledger entry.

### 3b. model / projection — one normalized JSON document

For outputs where formatting, ordering and internal IDs are irrelevant, a byte compare is
meaningless. The runner turns both sides into a canonical JSON structure and compares them
for equality. The **default** projection is the **summary** — one document describing everything
the tool understood about the input, composed from several exports
([`VALIDATION.md`](VALIDATION.md) §4). The narrower projections behave identically, each
producing its own document:

- **`summary`** → board: `counts`, `drill_holes`, `has_outline`, `min_track_width`,
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
- **`pos` / `ipcd356` / `stats`** → the same reductions the summary composes, emitted
  standalone ([`VALIDATION.md`](VALIDATION.md) §5).

**Numbers.** Tolerance is **the precision the export prints, and nothing wider.** KiCad
prints coordinates as fixed-precision decimal strings (`pos`: 6 decimals of a millimetre =
1 nm = KiCad's own integer board unit), and the reductions keep those strings verbatim, so
string equality *is* printed-quantum tolerance — no float parsing and no band. We
explicitly **refuse pre-authorized tolerance bands**: the moment a band is wider than the
export's own printed precision, a genuine coordinate error can hide inside it. Values that
cannot be compared exactly across implementations — computed float areas and densities —
are **excluded from the summary** rather than compared loosely
([`VALIDATION.md`](VALIDATION.md) §4.1).

**What is stored as the expected file ([DL-0014], [DL-0023]).** The recorded answer is the
**normalized document itself** (`summary.json`, `drc.json`, …) committed under
`expected/<version>/`, *not* the raw KiCad JSON/s-expr/CSV. `--regenerate` runs the
oracle, applies the reduction, and writes that document; at compare time the runner
reduces the adapter's output the same way and compares. Storing the reduction makes the
expected file self-describing, makes diffs read as semantic changes, and makes the shape a
second implementation must emit explicit.

Residue is **characterized, not hidden**: when a comparison fails, the runner reports
*how* (names-only difference vs membership difference vs count difference), so a mismatch
is actionable rather than an opaque "differs."

### 3c. render — normalized SVG compare

A render answer exports the drawing to SVG, normalizes the one nondeterministic line
(`<title>`, which carries the output filename and a wall-clock date) plus `<desc>`, and
compares **byte-exact**. Zero tolerance: KiCad's SVG path geometry is byte-stable
run-to-run (verified, [`VALIDATION.md`](VALIDATION.md) §6), and determinism is pinned at
the source with `--page-size-mode 2 --exclude-drawing-sheet --black-and-white` plus
`LC_ALL=C.UTF-8`/`TZ=UTC`.

The cross-implementation variant — rasterize both sides with a pinned `resvg` and
pixel/SSIM-diff under an explicit, per-case, load-bearing threshold — arrives with the
second adapter ([DL-0021]); no KiCad-vs-KiCad check ever rasterizes.

### 3d. Byte comparison exists for fabrication output only ([DL-0026])

There **is** a fourth comparison, and its scope is exactly two answers: `gerbers/` and
`drill/`, on every board case. Each is a directory compared as a whole — the same
filenames must be present, and every file must be byte-identical after the five
normalizers in §4.

Everything else the old `golden-file`/`golden-dir` mode covered stays **deleted**
([DL-0024]): the canonical `.kicad_pcb`/`.kicad_sch` re-serialize comparison and the
`upgrade` and `bom` verbs that fed it.

The distinction is worth stating precisely, because it is the same argument reaching
opposite conclusions on two inputs. A byte compare pins KiCad's exact *formatting* —
token order, whitespace, aperture numbering, comment style. That is a good
KiCad-version-regression signal and a bad cross-implementation one.

- For **re-serialized s-expressions**, a better comparison already exists: the summary
  compares the same file's *meaning*, exactly and fairly. The byte compare was pure
  duplication with a fairness penalty. Deleted, correctly.
- For **fab output**, no semantic comparator exists — a structural RS-274X reduction was
  ruled out as a second plotter's worth of engineering ([DL-0020]). Here the byte compare
  duplicates nothing; it is the only thing in the suite that looks at what a fab actually
  receives, and it covers track geometry and hole positions that the summary does not.

So it is kept, with [DL-0015]'s scoping made explicit: **in ecosystem mode `gerbers/` and
`drill/` report `INFO`, never `FAIL`.** The cross-implementation path remains
rasterize-and-compare ([DL-0021], [`ROADMAP.md`](ROADMAP.md) M4).

---

## 4. Normalization layer

`kicad-cli` output is deterministic in *geometry* but carries build/time/identity noise
in headers and IDs. Two halves:

**This layer is small, and every entry in it was re-verified against the 10.0.5 binary for
this revision.** The reductions in §3b *drop* the noisy fields by construction — the
summary never contains a date, a version, a path or a UUID, because those fields are not
part of the schema. So there is nothing to scrub for
`summary`/`drc`/`erc`/`netlist`/`pos`/`ipcd356`/`stats`. **Seven** normalizers exist in
total: the SVG `<title>`/`<desc>` strip, CRLF→LF for stored text, and the five
gerber/Excellon date lines that [DL-0026] brought back.

> **Four normalizers that earlier revisions of this table called for do not exist**, and
> the reason is evidence, not preference. `TF.GenerationSoftware` (gerber),
> `Header/GenerationSoftware` (`.gbrjob`) and the Excellon `TF.GenerationSoftware` line
> are all **byte-identical run to run** — they are version strings, not timestamps, and
> leaving them intact makes every fab answer assert for free that the pinned KiCad
> produced it. The drill report's "Created on" line has **no input at all**: the report is
> only written when `--generate-report` is passed, and the standard answers do not pass
> it. Full diffs in [`VALIDATION.md`](VALIDATION.md) §7.3. This is §4a applied to the
> table itself.

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
| summary / netlist / pos / ipcd356 / stats | *not needed* | The reduction drops `metadata`, the `(design …)` header, paths, dates, tool versions, UUIDs and net codes by construction — there is nothing left to strip ([`VALIDATION.md`](VALIDATION.md) §4.6). |
| DRC/ERC JSON | *live, inside the reduction* | drop top-level `date`, `kicad_version`, absolute input path; sort `violations`, `unconnected_items`, `schematic_parity` and each violation's `items[]` by content-derived order (not by UUID). |
| **Gerber** (RS-274X), every layer file | **live** (G1, G2) | Exactly two lines, verified: **G1** the value in `%TF.CreationDate,<ISO>*%`; **G2** the trailing ` date <YYYY-MM-DD HH:MM:SS>` in `G04 Created by KiCad (PCBNEW <ver>) date …*`. **Not** `TF.GenerationSoftware` — it is stable. |
| **Gerber job file** (`.gbrjob`) | **live** (G3) | Exactly one line, verified: the JSON key `Header.CreationDate`. **Not** `Header.GenerationSoftware` — it is stable. |
| **Excellon drill** (`.drl`) | **live** (D1, D2) | Exactly two lines, verified: **D1** the trailing timestamp in `; DRILL file KiCad <ver> date …`; **D2** the value in `; #@! TF.CreationDate,<ISO>`. **Not** the `TF.GenerationSoftware` line — it is stable. |
| Drill report | *never produced* | Requires `--generate-report`; the standard answers do not pass it, so its "Created on" stamp has no input. Normalizer deleted from this spec ([DL-0026]). |
| s-expr (`… upgrade`) | *not compared* | (was: `(generator_version …)`; the whole comparison is deleted, [DL-0024]) |
| BOM | *not compared* | header line with tool name, version, date; row order only deterministic with a fixed sort/group spec. |
| PDF | *not compared* | `/CreationDate`, `/ModDate`, random `/ID`, producer. **Least diffable — avoid PDF for conformance.** |
| STEP / BREP (OCC) | *deferred ([DL-0012])* | ISO-10303 `FILE_NAME` timestamp/author/system; entity ordering + tessellation not byte-stable across OCC versions → compare geometrically, not textually. |

The "not compared" rows are kept deliberately: they are the research a future comparison
would otherwise have to redo.

**One rule that is not a normalizer.** Gerber output embeds the input file's stem, in both
the output filenames and the `%TF.ProjectId` line, whose GUID is literally the filename's
bytes (verified: `board.kicad_pcb` → `board-F_Cu.gtl` /
`%TF.ProjectId,board,626f6172-642e-46b6-…`; the same board as `renamed.kicad_pcb` →
`renamed-F_Cu.gtl` / `%TF.ProjectId,renamed,72656e61-6d65-…`). So the runner copies each
input to its scratch directory **under the original filename**, and case authors name
board inputs `board.kicad_pcb`. Normalizing the project id instead was rejected: it would
discard a real assertion — that the tool identified the project correctly — to buy a
freedom nobody needs.

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
`expected/10.0.5/summary.json`). Rationale and mechanics:

- **Keyed by reference-oracle version, not by adapter.** An expected file is "the correct
  answer as defined by KiCad `<ver>`." A second adapter compares *its* output against the
  same file; there is no per-adapter answer.
- **Usually exactly one per case.** The summary collapses what used to be several
  per-projection answers into one `summary.json` ([DL-0022]). A case has a second expected
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
  input type's verb mapping, [`VALIDATION.md`](VALIDATION.md) §9.1). **Unexercised
  subcommands/flags are the gap list.**
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
- **Gerber and drill coverage is byte-recorded, and byte-recorded means KiCad-only.**
  [DL-0026] restored it on every board case, which closes the hole [DL-0024] opened — but
  a clean-room tool emitting valid RS-274X with different apertures or a different
  coordinate format would fail every one of those files while being perfectly conformant.
  So these answers are `INFO`, never `FAIL`, in ecosystem mode, and the fair
  cross-implementation comparison (rasterize both sides) is still only on the roadmap. The
  suite's fab coverage is real against KiCad and absent against anyone else; both halves of
  that sentence matter.
- **Cases record more than they need to, on purpose.** Since [DL-0025] there is no per-case
  opt-out from the standard answers, so a DRC case also carries gerbers it is not about.
  This trades a little redundancy for a manifest with no knobs to get wrong. If the suite
  ever grows to where the redundancy costs real time, the fix is parallelism
  ([`VALIDATION.md`](VALIDATION.md) §9.4), not a skip field.
