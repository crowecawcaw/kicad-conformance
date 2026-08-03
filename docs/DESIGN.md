# Design — kicad-conformance architecture and validation

This document defines the architecture **and** what a case actually compares (the two
used to be separate files, `DESIGN.md` and `VALIDATION.md`; they duplicated the same
four comparison kinds and disagreed on a normalizer count, so they are merged — the
counts and claims below are reconciled against the running code, which is ground truth).
Companion docs: [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) (how to author a case),
[`DECISIONS.md`](DECISIONS.md) (numbered rationale), [`ROADMAP.md`](ROADMAP.md),
[`DIVERGENCES.md`](DIVERGENCES.md) (the checked-in known-divergence ledger,
[DL-0009]/[DL-0018]).

Every empirical claim below was produced against **`kicad-cli` 10.0.5** in the
`kicad/kicad:10.0.5` Docker image, on the committed fixtures, run as:

```
docker run --rm -v "<dir>:/work" -w /work -e LC_ALL=C.UTF-8 -e TZ=UTC \
    kicad/kicad:10.0.5 bash -lc '<command>'
```

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

A test case is **one input file**, and the answers KiCad gave for it. The runner feeds
the input to the tool and records a fixed set of outputs chosen by the input's file
type — the **standard answers**. For a board that is four things: a **summary** (one
JSON document describing everything the tool understood), a **render** (the front-copper
drawing, as SVG), the **gerbers**, and the **drill file**. For a schematic it is two: a
summary and a render. Each is compared against the recorded copy in `expected/<version>/`.
A **rejection case** (one that sets `control`) records nothing and only checks that a bad
file is rejected. The case author picks none of this — they name the input file, and the
file's type does the rest ([DL-0025]).

- **Corpus of input fixtures + declarative expectations.** A *case* is a directory: a
  tiny `case.toml` manifest, one or more input fixtures, and (unless it is a rejection
  case) an `expected/<version>/` tree holding the recorded correct answer. Expectations
  are declarative data, never code.
- **Runner.** Walks `suites/`, reads each `case.toml`, invokes the adapter, applies the
  normalization layer, and decides pass/fail. It is a reference harness, not the source
  of truth — the *file format* is the contract (an SDK-based implementation may write its
  own runner against the same cases).
- **Adapter.** Abstracts the implementation-under-test behind a fixed set of
  **capability verbs** exchanged over a **subprocess protocol** (§2). The **reference
  adapter wraps `kicad-cli`**; others (a Rust engine, a viewer) implement the same
  verbs.
- **Expected files.** The recorded correct answer, **produced by the reference oracle
  (KiCad)** at a pinned version and stored per version. Never hand-written. Any
  adapter's output is compared against the KiCad-recorded answer — KiCad is
  authoritative. (Other test frameworks call this a snapshot, a baseline, or a golden
  file; earlier revisions of this repo said "golden" too, [DL-0004].)

Why the adapter is a *subprocess* boundary and not a Python plugin API: it keeps the
implementation-under-test **language-agnostic**. A subprocess contract (files in, exit
code + captured streams + written output files out) is the lowest common denominator
every tool can satisfy. See [DL-0007](DECISIONS.md).

---

## 2. The adapter contract

An adapter is an executable. The runner invokes it as:

```
<adapter> <verb> --in <path...> [--in <path...>] --out <dir> [--root <name>] [--format <fmt>]
```

and inspects three things: the **exit code** (0 = success, non-zero = the tool rejected
the input), the captured **stdout/stderr**, and any **files written under `--out`**. The
runner sets `LC_ALL=C.UTF-8` and `TZ=UTC` in the adapter's environment for every call.
`--in` may repeat (a multi-sheet schematic passes its root **and every sub-sheet**); when
it does, `--root` names which one is the netlist root.

The reference adapter (`adapters/kicad.py`, at the repo root — not inside the `runner/`
package, since it is an executable, not a runner internal) is a thin shim: it discovers
`kicad-cli` (env `KICAD_CLI` → `PATH` → per-OS install dirs, newest-numeric-version
first), then maps each verb onto one or more `kicad-cli` subcommands. Discovery verifies
the binary with `kicad-cli version --format plain` and records `version --format about`
in the run log as the oracle's identity.

### Capability verbs

**Verbs are an adapter-internal vocabulary, not a manifest field.** Since [DL-0025] a
`case.toml` names no verb: the runner derives the verbs to run from the input file's
suffix (§2's table below). A second implementation still answers this same table as its
contract.

Each adapter declares which verbs it supports (the `capabilities` meta-verb); unsupported
verbs cause the relevant cases to be **skipped and counted**, never failed. Core verbs:

| Verb | Input | Output the runner consumes | `kicad-cli` mapping (10.0.5) |
|---|---|---|---|
| `version` | — | version string on stdout | `version --format plain` (+ `--format about` for the identity record) |
| `summary` | `.kicad_pcb` / `.kicad_sch` | **one merged `summary.json`** — everything the tool understood (§3b) | board: `pcb export stats` + `pcb export pos` + `pcb export ipcd356`; schematic: `sch export netlist` |
| `parse-sch` | `.kicad_sch` | success/failure only | `sch upgrade --force` on a **scratch copy** (rewrites in place) |
| `parse-pcb` | `.kicad_pcb` | success/failure only | `pcb export stats --format json` on a scratch copy ([DL-0029]; NOT `pcb upgrade --force` — see notes below) |
| `parse-pcb-upgrade` | `.kicad_pcb` | success/failure only | `pcb upgrade --force` on a scratch copy — retained ONLY so one case (`rejects-unterminated-sexpr`, DIV-0001) can keep deliberately exercising its documented segfault via `known_divergence.probe` ([DL-0029]); no case reaches for this as its default probe |
| `parse-sym` | `.kicad_sym` | success/failure only | `sym upgrade --force -o <out> <in>` |
| `parse-fp` | `.pretty` **dir** (never a lone `.kicad_mod`) | success/failure only | `fp upgrade --force -o <dir> <in_dir>` |
| `erc` | `.kicad_sch` | normalized violation set | `sch erc --format json --severity-all -o <out>/erc.json` |
| `drc` | `.kicad_pcb` | normalized violation set | `pcb drc --format json --units mm --severity-all -o <out>/drc.json` |
| `netlist` | `.kicad_sch` (root + subsheets) | net→node membership | `sch export netlist --format kicadsexpr\|kicadxml -o <out>/netlist.net` |
| `pos` | `.kicad_pcb` | placement rows | `pcb export pos --format csv --side both --units mm -o <out>/pos.csv` |
| `ipcd356` | `.kicad_pcb` | board net graph + test-point geometry | `pcb export ipcd356 -o <out>/board.d356` |
| `stats` | `.kicad_pcb` | inventory report | `pcb export stats --format json -o <out>/stats.json` |
| `render` | any of the four | one SVG per invocation | `pcb\|sch\|sym\|fp export svg` (dispatches on the input suffix) |
| `export-gerbers` | `.kicad_pcb` | **a directory of gerbers**, compared byte-for-byte after normalization ([DL-0026]) | `pcb export gerbers -o <out>/` — **no `--layers`**, no `--no-protel-ext`: KiCad's own set, which is what a fab receives |
| `export-drill` | `.kicad_pcb` | **a directory holding one `.drl`**, compared byte-for-byte after normalization ([DL-0026]) | `pcb export drill -o <dir>/` — no map, no report, no `--excellon-separate-th` |
| `export-step` | `.kicad_pcb` | reserved, unused | `pcb export step` (heavy, least deterministic; see [DL-0012](DECISIONS.md)) |

Notes, load-bearing for correct mapping:

- **`parse-*` has no dedicated subcommand.** There is no pure "parse and stop" verb in
  `kicad-cli`; for `sch`/`sym`/`fp`, `… upgrade --force` loads the file (proving it
  parses) and re-emits it. The re-emitted bytes are not compared against anything —
  `parse-*` is an **exit-polarity check only**, exactly what a rejection case needs.
  `--force` is always passed so the result never depends on the input's pre-existing
  version stamp.
- **`parse-pcb` is `pcb export stats`, not `pcb upgrade --force` ([DL-0029]).** Verified
  (two independent sweeps, 8/8 and 10/10 malformed boards): `pcb upgrade --force`
  SIGSEGVs on every board it fails to load, always right after printing the correct
  `Failed to load board: …` message — a crash is never a pass ([DL-0013]), so every
  `rejects-*` board case scored a strict xfail instead of the genuine reject-and-PASS its
  concept describes. Every other board-consuming subcommand, including `pcb export
  stats`, rejects the identical bytes gracefully (exit `3`, same stderr message). The
  written `stats.json` is discarded, same exit-polarity-only contract as before. The
  old command survives as its own verb, `parse-pcb-upgrade`, used by exactly one case
  (`rejects-unterminated-sexpr`, DIV-0001) via `known_divergence.probe` to keep
  documenting the segfault on purpose now that it is off the default path.
- **`summary` composes inside the adapter, not inside the runner.** The reference adapter
  runs the exports into its scratch dir and writes `<out>/summary.json` itself. A
  non-KiCad implementation therefore emits its summary directly, without imitating
  KiCad's export formats.
- **A multi-sheet schematic's `summary`/`netlist` needs every sub-sheet on disk.** The
  adapter copies **every** declared `--in`, not just the first, into one scratch
  directory under each file's own name (`adapters/kicad.py`'s `_scratch_copy_all`), then
  runs `sch export netlist` against the scratch copy of the declared `--root` (or the
  first `--in` if `--root` is absent). A sub-sheet is referenced by name from its parent
  (`(sheet ... (property "Sheetfile" "sub.kicad_sch"))`), so it must physically be next
  to the root's scratch copy or kicad-cli cannot resolve it. Copying only the first input
  is not merely incomplete — verified empirically, kicad-cli does **not** error in that
  case; it silently exits 0 and produces a netlist covering only the reachable (root-only)
  portion of the hierarchy, which is the failure mode
  `suites/schematic-parse/hierarchical-sheet/` exists to catch (§9 below).
- **`pcb`/`sch upgrade` rewrite in place** (no `--output`), so the adapter copies the
  fixture to a scratch dir first and reads the result back. `fp`/`sym upgrade` are
  library/directory operations with `-o` (`fp upgrade -o` refuses a pre-existing output
  path; `sym upgrade -o` writes one merged file unless the output path already exists as
  a directory).
- **DRC flags are pinned:** `--severity-all` (otherwise the reported set depends on the
  project's stored settings); `--units mm` (`--units in` prints the same decimals at a
  25×-coarser quantum, destroying information). We do **not** pass
  `--exit-code-violations`: a DRC violation is *data to compare*, not a tool failure.
- **`step`/3D and `render`** need OpenCASCADE and sometimes a display (`xvfb-run`), and
  their output is the least deterministic. Deferred pending owner ratification
  ([DL-0012]).
- **`parse-fp` takes a `.pretty` directory, never a lone `.kicad_mod`.** Empirically,
  `fp upgrade --force -o out fp.kicad_mod` fails with `Unable to convert library`
  (exit 2); it must be pointed at a library *directory*.

### 2a. Output-artifact / `-o` handling per verb (the runner always passes an explicit path)

The empirical gotcha (KiCad 10.0.5): with **no** `-o`, several verbs write a **derived
filename in the current working directory**, not a name the bare `<verb> --out <dir>`
shape mentions, so the runner cannot reliably find the artifact (`pcb drc` writes
`<input-stem>-drc.json` in CWD; `sch export netlist` writes `<stem>.net`; etc.).

**Rule: the runner never relies on a derived name.** It creates a per-check scratch
`--out` directory and passes the adapter an **explicit** output path/dir; the adapter
forwards it to the corresponding `kicad-cli -o/--output`. The runner then reads back the
exact path it dictated:

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
| `render` (pcb) | `-o <out>/render.svg` — a **file** path, `--layers F.Cu` mandatory (§2b) | `<out>/render.svg`, recorded as `expected/<version>/render-F_Cu.svg` |
| `render` (sch/sym/fp) | `-o <out>/` — a **directory**; kicad-cli derives the names (`<stem>.svg`, `<Symbol>_unit<N>.svg`, `<Footprint>.svg`; a hierarchical sheet's non-root pages get `<stem>-<sheetname>.svg`, §6.2) | `<out>/*.svg` |
| `export-gerbers` | `-o <out>/` — a directory, created if absent. **No `--layers`** | `<out>/*` (all of it, compared as a tree) |
| `export-drill` | `-o <out>/` — a directory, created if absent. No report, no map | `<out>/*.drl` |
| `parse-sch`/`parse-pcb` | (no `-o`; rewrites in place) | — (exit only) |
| `parse-sym`/`parse-fp` | `-o <out>` (path must **not** pre-exist) | — (exit only) |

**The adapter also copies every input to an isolated scratch dir, not only for the
in-place `upgrade` verbs.** `kicad-cli` writes a `.kicad_prl` project-local-settings cache
next to a board it merely **reads** (`pcb drc`, `pcb export gerbers`, …) as a side effect.
Left uncontrolled, that would land inside `suites/` next to the committed fixture. So
every verb's input is copied into a fresh scratch directory before invoking `kicad-cli`,
under its **original filename** (load-bearing: see the gerber `%TF.ProjectId` note in §4).

### 2b. Layer sets are fixed by the harness for SVG, and taken from KiCad for gerbers

Both are decisions, not case parameters ([DL-0025], [DL-0026]).

**SVG: the harness must choose, because KiCad has no default.** `pcb export svg` refuses
to run without an explicit layer list:

```
$ kicad-cli pcb export svg --mode-multi -o out --page-size-mode 2 \
      --exclude-drawing-sheet --black-and-white board.kicad_pcb
At least one layer must be specified
```

The harness pins **`--layers F.Cu`**, one file, recorded as `render-F_Cu.svg`. The
gerbers already cover per-layer geometry byte-exactly (including silkscreen, mask,
paste, adhesive, courtyard, fab and edge cuts — §4.1 below), so rendering every layer as
SVG too would record the same geometry twice in a second format. `F.Cu` is the one layer
every KiCad board has (copper layer 1 is mandatory) and is where routing and SMD pads
live — the layer most likely to move when someone edits the board, and a fixed choice
that keeps every board case comparable. `sch|sym|fp export svg` need no such choice.

Two `-o` gotchas, verified: for `pcb export svg` in its default single mode `-o` is a
**file path**; for `gerbers`, `drill`, and the `sch`/`sym`/`fp` SVG exports it is a
**directory**, created if absent.

**Gerbers: KiCad chooses, and the choice is part of the answer.** `pcb export gerbers`
is run with **no `--layers`**. KiCad plots the set stored in the board, falling back to
its built-in default when the board has none — verified as **6 gerbers + a job file** for
the populated fixture (which carries a `(pcbplotparams (layerselection …))` block) and
**20 gerbers + a job file** for the minimal fixture (which does not). Pinning a list
instead would compare an artifact nobody ships and would hide a future change to KiCad's
default selection.

### 2c. Parser error-verbosity is asymmetric between PCB and schematic

- **PCB** (`pcb upgrade`) on malformed input surfaces parse position:
  `Failed to load board: Expecting '(' in '…', line 2, offset 1.` A PCB rejection case
  **may** assert the specific `Expecting` substring. (Caveat: on 10.0.5 this path also
  *crashes* after printing — see §3a and [DL-0013].)
- **Schematic** (`sch upgrade`) collapses *every* defect to the same generic
  `Failed to load schematic` (exit 3), with no position. A schematic rejection case
  **cannot** discriminate the defect via stderr; it pins the coarse message and relies on
  the positive control (§3a) to prove which defect fired.

---

## 3. Comparison model

**How something is compared follows from what it is** — there is no `compare` or `op`
field. The runner looks at the answer's name and extension. Four kinds of comparison
exist:

| Kind | Applied to | What it compares |
|---|---|---|
| **exit** (§3a) | every rejection case | did the tool accept (exit 0) or gracefully reject the input |
| **summary** (§3b) | `summary.json`, and the JSON extras (`drc.json`, `pos.json`, …) | a normalized JSON document, compared for equality |
| **render** (§3c) | `render*.svg`, `render/*.svg` | the drawn SVG geometry, byte-exact after normalizing `<title>`/`<desc>` |
| **bytes** (§3d) | `gerbers/`, `drill/` | a directory of fabrication output: same filenames, every file byte-identical after normalization |

(An earlier revision of this doc numbered these L0–L3; that numbering is retired in
favor of the four names above.)

### 3a. exit — success/failure polarity (+ error substring)

The baseline, and the *only* thing a rejection case needs:

- A **happy** case (no `control` set) → the adapter must exit `0`, and every standard
  answer must match.
- A **rejection** case (sets `control`) → the adapter must exit with a **bounded,
  graceful non-zero** exit — a clean rejection, *not* a crash. No answers are recorded or
  compared.
- A rejection case may set `error_contains = "…"`, asserting a substring on **stderr**
  (per-stream, not merged). `error_contains_any = ["…", "…"]` tolerates legitimate
  wording variation between implementations.

Substring matching is deliberately loose: it pins the *observable contract* without
over-fitting to KiCad's exact phrasing, so a second adapter with different error text
still conforms.

**Crash verdict — a crash is NEVER a pass ([DL-0013]).** A malformed input can make the
oracle *crash* rather than reject cleanly: on 10.0.5, a truncated board makes
`pcb upgrade` print a good `Expecting '('` message and then **segfault** (exit 139 on
native Windows; a `SIGSEGV` on Docker Linux). 139 is non-zero, so a naïve "non-zero =
rejected" rule would silently *pass* a rejection case on a **crash**. The runner
therefore classifies termination into three outcomes, not two:

| Outcome | Detection (portable — do **not** hard-code 139) | Counts as |
|---|---|---|
| `OK` | exit `0` | pass for happy |
| `REJECT` | bounded non-zero exit (roughly `1..128`), process exited normally | pass for a rejection case only |
| `CRASH` | killed by a signal, or exit code `> 128` (128 + signal); on POSIX inspect `WIFSIGNALED`/`WTERMSIG`; on Windows treat a fatal exception status or code `> 128` as a signal-equivalent | **never a pass** — not for happy, not for a rejection case |

Known oracle crashes (the 10.0.5 PCB parse-failure segfault) are filed upstream and
recorded in the divergence ledger ([`DIVERGENCES.md`](DIVERGENCES.md)); the paired PCB
rejection case asserts the real `Expecting` substring so a future KiCad that rejects
*cleanly* still conforms.

**Adapter requirement: relay a child's crash, don't launder it.** The runner's direct
subprocess child is the *adapter*, never `kicad-cli` itself ([DL-0007]) — so this
classifier only ever sees the adapter's own `returncode`. For a signaled `kicad-cli` to
be visible as a `CRASH` through that indirection, **any** adapter satisfying this
contract must re-signal itself the same way the reference adapter does: when its
`kicad-cli` child is killed by a signal, the adapter re-raises the identical signal
against itself (`os.kill(os.getpid(), sig)` in `adapters/kicad.py`'s `run_and_relay`)
rather than exiting with some ordinary nonzero code.

**Positive control — every rejection case must be falsifiable ([DL-0013]).** Because
stderr on the schematic side cannot discriminate *which* defect fired, a rejection case
must ship a runner-enforceable positive control: **removing the injected defect must
make the same check exit 0.** The runner re-runs the check against the sibling `control`
fixture and requires it to reach `OK`. A rejection case whose control does not flip to
`OK` is reported **not-evidence**, never passed.

**Known-oracle-divergence — strict xfail ([DL-0018]).** The OK/REJECT/CRASH verdict is
never edited per-case — but a case may declare that the reference oracle itself is known,
and tracked, to diverge, via a `known_divergence` table (`reason`, `kind`, optional
`tracking` — schema in [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §8). This is a
*presentation layer* on top of the verdict, applied only after any positive control has
already passed:

- If the actual verdict matches the declared `kind` (e.g. `CRASH` for `kind = "crash"`),
  the check is scored **`XFAIL`** — the build stays green.
- If the same check instead reaches its normally-desired outcome (the oracle got fixed),
  that is an **`XPASS`**, which **fails the build** with a message pointing at
  [`DIVERGENCES.md`](DIVERGENCES.md): an XPASS here is never silently tolerated, so the
  ledger and the marker cannot quietly rot once the underlying bug is fixed.
- `XFAIL`/`XPASS` are separately-counted verdicts in the summary alongside
  `PASS`/`FAIL`/`CRASH`/`SKIP`/`NOT-EVIDENCE`/`NEEDS-REGEN`.

This is how `board-parse/rejects-unterminated-sexpr` (the 10.0.5 PCB-parse segfault, §3a
above) reports green without loosening its assertions or leaving the gating build
permanently red over a bug filed upstream. See [DL-0018] and
[`DIVERGENCES.md`](DIVERGENCES.md).

### 3b. summary — one normalized JSON document

`summary.json` is the default answer for a happy board or schematic case. The runner
invokes several `kicad-cli` exports, merges them into one JSON document, and compares it
to `expected/<version>/summary.json`. The case author never sees the intermediate
exports. It is called the **summary** because that is what it is: a summary of
everything the tool understood about the file, with the parts that cannot be compared
fairly left out. (It was called `model.json` until [DL-0028]; the two are the same
thing.)

| Input | Composed from | Result |
|---|---|---|
| `.kicad_pcb` | `pcb export stats` + `pcb export pos` + `pcb export ipcd356` | board summary (§3b.1) |
| `.kicad_sch` | `sch export netlist` (root + every sub-sheet present in scratch) | schematic summary (§3b.2) |
| `.kicad_sym`, `.pretty` | — | **none**: there is nothing to build one from (§3b.4) |

**Canonical form (both summaries).** JSON, UTF-8, LF line endings, two-space indent, keys
sorted, trailing newline (`json.dumps(summary, indent=2, sort_keys=True) + "\n"`). Every
list is sorted by its own printed content — nothing depends on emission order. Numbers
KiCad prints as fixed-precision strings stay strings verbatim (`"0.2500 mm"`); string
equality then *is* printed-quantum tolerance — no float parsing, no tolerance band,
nothing for a real error to hide inside. No timestamps, versions, paths, UUIDs or net
codes — every such field is dropped by construction, so the summary needs no normalizer
step at all.

#### 3b.1 Board summary

Six top-level keys (shown in explanation order; on disk they are sorted):

| Key | Type | Source | Meaning |
|---|---|---|---|
| `kind` | string | — | Always `"board"`. |
| `has_outline` | bool | `stats` | Did the board yield a closed `Edge.Cuts` outline. |
| `min_track_width` | string | `stats` | Narrowest track actually on the board, e.g. `"0.2500 mm"`. |
| `min_drill_diameter` | string | `stats` | Smallest drilled hole, e.g. `"0.4000 mm"`. |
| `counts` | object | `stats` | Integer inventory: `footprints{tht,smd,unspecified,total}`, `pads{through_hole,smd,connector,npth,castellated,press_fit}`, `vias{through,blind,buried,micro}`. `components`→`footprints`; front/back split dropped (`placement` already records each footprint's side). |
| `drill_holes` | array | `stats` | The hole table: `count`, `shape`, `x_size`, `y_size`, `plated`, `source`, `start_layer`, `stop_layer`. Content-sorted. |
| `placement` | object | `pos` | `refdes → {value, package, x, y, rotation, side}`. Strings, verbatim from the CSV. |
| `nets` | object | `ipcd356` | `net-name → sorted array of "REFDES.PAD"` strings. |

Sources, exact commands:

```
$ kicad-cli pcb export stats  --format json                             -o stats.json  board.kicad_pcb
$ kicad-cli pcb export pos    --format csv --side both --units mm       -o pos.csv     board.kicad_pcb
$ kicad-cli pcb export ipcd356                                          -o board.d356  board.kicad_pcb
```

`pos` uses the **CSV** form deliberately: the ASCII form carries a `created on
<timestamp>` header that CSV does not. `ipcd356` carries no timestamp either. `stats`
has exactly one nondeterministic field, `metadata.date` — and the whole `metadata`
object is dropped anyway.

**`nets` — member format and the uppercase gotcha.** A net member is the string
`"<refdes>.<pad>"` (`"R1.1"`), sorted lexicographically. Split on the **first** `.`; a
refdes never contains one. Vias contribute their **net name** but no member.

> **IPC-D-356 is an uppercase-only format.** The board's net is literally `Net-1`, but
> `pcb export ipcd356` emits `NET-1`, and the summary records what the export prints.
> Verified: the fixture contains `(net "Net-1")`, the export line is
> `327NET-1            R1    -1          A01X+007874Y…`. Consequence for a second
> implementation: **upper-case net names in `summary.nets`** — two nets differing only in
> case would collide here. Schematic-side net names (§3b.2) are *not* uppercased.

**Board Y is flipped.** `pos` reports fab-convention coordinates: a footprint at board
`y = 20 mm` prints as `-20.000000`. Both sides of a comparison see the same convention,
so it is invisible to the check.

**What is deliberately excluded from `stats`, and why.** `pcb export stats` also reports
`area`, `front_copper_area`, `back_copper_area`, `front_footprint_area`,
`back_footprint_area`, `front_component_density`, `back_component_density`,
`min_track_clearance`, `width`, `height`. **None of them are in the summary.** The rule:
*keep counts and echoed input values; drop computed geometry.*

- **Areas and densities are computed float geometry** — polygon unions, clipping, and
  rounding to 3–4 significant digits. Two conformant implementations can legitimately
  differ in the last printed digit for reasons that are not bugs, so these fields are a
  **false-failure generator with very little conformance signal**: every defect they
  could catch is already caught exactly and integer-precisely by `counts`, `placement`
  and `has_outline`.
- **`min_track_clearance` is excluded** for the same reason *and* because it is usually
  the sentinel `"2147.4836 mm"` (INT_MAX nanometres — "only one segment on this net").
- **`width`/`height` are excluded** as bounding-box arithmetic; `has_outline` carries the
  fact that matters without the arithmetic.
- **`min_track_width` and `min_drill_diameter` are kept** because they are *echoed input
  values*, not computed geometry.

`min_track_width` is also how the summary notices a lost track — verified by deleting the
board's only `(segment …)` and re-running: `min_track_width` flips from `"0.2500 mm"` to
the sentinel `"2147.4836 mm"`. That is *coarse* — the summary counts no tracks and
records no track geometry — but since [DL-0026] the **gerbers** cover the actual plotted
copper geometry byte-for-byte (§4), so a moved or dropped track shows up there too.

#### 3b.2 Schematic summary

For a schematic the netlist export is already very nearly the complete semantic
projection, so the schematic summary is a thin, de-noised rewrite of it. Three top-level
keys:

| Key | Type | Meaning |
|---|---|---|
| `kind` | string | Always `"schematic"`. |
| `components` | object | `refdes → {value, part, footprint, sheet, pins[]}`. `part` is the library part name, `footprint` the `Footprint` field (`""` when unset), `sheet` the sheet path (`"/"` for a single-sheet design, `"/sub/"` for a component on a sub-sheet named `sub` — §9), `pins` the sorted list of pin numbers the symbol declares. |
| `nets` | object | `net-name → sorted array of "REFDES.PIN"` strings — identical shape to the board summary's `nets`. |

```
$ kicad-cli sch export netlist --format kicadsexpr -o net.net sheet.kicad_sch
$ kicad-cli sch export netlist --format kicadxml   -o net.xml sheet.kicad_sch
```

**Dropped:** the `(design …)` header (absolute source path, wall-clock date, tool
version), every `tstamps` UUID, the net `code` and `class`, `pinfunction`/`pintype`, and
the whole `title_block`.

**`pins` is what makes the summary catch a dropped pin.** `nets` only lists pins that are
*connected*; a symbol whose unconnected pin was silently lost would not change `nets` at
all, but it changes `components.<ref>.pins`.

**Either interchange format produces the identical summary.** `kicadsexpr` and `kicadxml`
carry the same content, so the reducer reads both and a case may assert the *same*
`summary.json` twice — once per format — via `extra = ["summary-kicadxml"]`. That is the
cheapest available proof that the summary measures meaning rather than one serialization.

The real, committed file is `suites/schematic-parse/two-nets-one-shared-pin/expected/10.0.5/summary.json`
(and `suites/board-parse/populated-board/expected/10.0.5/summary.json` for a board) — not
reproduced here, since it is three directories away and a doc copy would just be a second
place for it to go stale.

**Falsifiable, verified.** Three single-token perturbations of the board fixture, each
producing a minimal, legible diff:

```
# A -- delete the board's only track
<   "min_track_width": "0.2500 mm",
>   "min_track_width": "2147.4836 mm",

# B -- rotate R1 from 90 to 45 degrees
<       "rotation": "90.000000",
>       "rotation": "45.000000",

# C -- move R1 pad 1 from Net-1 to GND
>       "R1.1",
<       "C1.2",
<       "R1.1"
>       "C1.2"
```

"A test that cannot fail is not evidence": a new case is not trusted until its author has
broken the fixture and watched it go red like this.

#### 3b.3 Determinism, verified

Generated twice in the same container, one second apart: `diff summary-1.json
summary-2.json` is empty for both the board and schematic summaries. No normalizer is
involved: the nondeterministic fields (`metadata.date`, the netlist header's date/path/
tool) are *dropped by the reduction itself*, not scrubbed after the fact.

#### 3b.4 Symbol and footprint libraries: renders only

`kicad-cli` 10.0.5 offers exactly two things for a library — `upgrade` and `export svg`:

```
$ kicad-cli sym export --help
Usage: sym export [--help] {svg}
$ kicad-cli fp export --help
Usage: fp export [--help] {svg}
```

There is **no structured export** to build a summary from (no pin table, no pad table).
A library case's standard answer is its **drawings and nothing else** — one SVG per
symbol-unit or per footprint, in a `render/` directory:

```
$ kicad-cli sym export svg -o out --black-and-white test.kicad_sym
Plotting symbol 'T1' unit 1 to 'out/T1_unit1.svg'
$ kicad-cli fp export svg -o out --black-and-white ./test.pretty
Plotting footprint 'PadOnly' to 'out/PadOnly.svg'
```

Both are deterministic apart from the `<title>` line (§4). If a future KiCad grows a
structured symbol/footprint export, a library summary is the obvious extension.

### 3c. render — normalized SVG compare

A render answer exports the drawing to SVG, normalizes the one nondeterministic line
(`<title>`, which carries the output filename and a wall-clock date) plus `<desc>`, and
compares **byte-exact**. Zero tolerance: KiCad's SVG path geometry is byte-stable
run-to-run, verified again for this revision:

```
$ kicad-cli pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet \
      --black-and-white -o r1.svg board.kicad_pcb      # and again as r2.svg, 1 s later
$ diff r1.svg r2.svg
11c11
< <title>SVG Image created as r1.svg date 2026-08-03T04:13:09 </title>
---
> <title>SVG Image created as r2.svg date 2026-08-03T04:13:11 </title>
```

determinism pinned at the source with `--page-size-mode 2` (board area only),
`--exclude-drawing-sheet`, `--black-and-white`, plus `LC_ALL=C.UTF-8`/`TZ=UTC`. No
rasterizer is needed for this — none ships in the reference image.

The cross-implementation variant (arrives with the second adapter, [DL-0021]) rasterizes
both sides with a pinned `resvg` and pixel/SSIM-diffs under an explicit, per-case,
load-bearing threshold; no KiCad-vs-KiCad check ever rasterizes.

**Multi-page schematics — an open item.** `sch export svg` writes one SVG **per page**:
the root page as `<stem>.svg`, and each sub-sheet page as `<stem>-<sheetname>.svg`
(verified: rendering `root.kicad_sch` with one sub-sheet named `sub` writes `root.svg`
and `root-sub.svg`). The runner's single-file `render` answer only names and compares the
root page's file (`render.svg`, from `<stem>.svg`) — a hierarchical case's sub-sheet
pages are produced but not yet part of the recorded answer. This is a real, named gap,
not a silent one: extending `render` to a multi-page directory answer (mirroring
`gerbers/`/`drill/`'s directory-tree comparator) is future work, not part of the
`inputs`/`root` fix in §9.

### 3d. bytes — fabrication output, KiCad-version-regression signal only ([DL-0026])

Every board case records the gerbers and the drill file KiCad produces, **file for file
and byte for byte** after the normalizers in §4. Each is a directory compared as a
whole: the same filenames must be present, and every file must be byte-identical.

**The layer set is KiCad's, not a flag.** `pcb export gerbers` is run with **no
`--layers`**. Verified on both committed board fixtures:

```
$ kicad-cli pcb export gerbers -o out populated-board/board.kicad_pcb
                       -> 6 gerbers + board-job.gbrjob  (7 files, 5 573 bytes)
$ kicad-cli pcb export gerbers -o out minimal-two-layer-board/board.kicad_pcb
                       -> 20 gerbers + board-job.gbrjob (21 files, 12 317 bytes)
```

The populated board carries a stored `(pcbplotparams (layerselection …))` block; the
minimal one does not, so KiCad's built-in default set applies. Both sets are stable
run-to-run. The drill export likewise takes no options beyond `-o`: exactly one file,
`<input-stem>.drl`, with or without holes.

**What byte answers do and do not prove.** They catch, exactly: a plotter change between
KiCad patch releases; a board edit that changes the plot (a moved track, a resized pad, a
deleted via) — including track *geometry* the summary only sees through
`min_track_width`; a hole's *position*, which the summary's hole table does not record.
They **do not** prove a second implementation is correct — a clean-room tool emitting
valid RS-274X with different-but-equivalent apertures would fail every one of these
files while being perfectly conformant. So, per [DL-0015]/[DL-0026]: **a second
implementation is not judged on `gerbers/` or `drill/`** — in ecosystem mode the runner
reports these as `INFO`, never `FAIL`. There is no semantic (structural) comparator for
RS-274X — building one was ruled out as a second plotter's worth of engineering
([DL-0020]) — so board copper *meaning* is instead covered by the composition of
`stats`+`pos`+`ipcd356` (folded into the summary) and the render.

---

## 4. Normalization layer

`kicad-cli` output is deterministic in *geometry* but carries build/time/identity noise
in headers and IDs. Two halves:

**Environment pinning (removes noise at the source).** Every adapter call runs with
`LC_ALL=C.UTF-8` (decimal separator / thousands grouping leak into numbers) and
`TZ=UTC` (timestamps), from a fixed working directory. This is not post-hoc scrubbing;
it removes whole classes of drift before they are ever written to a file.

**Post-hoc normalizers (per output kind).** The summary/drc/erc/netlist/pos/ipcd356/
stats reductions *drop* the noisy fields by construction (§3b) — there is nothing left
to scrub for those. What remains needs an explicit normalizer, implemented in five
functions in `runner/normalize.py`:

| Function | Strips |
|---|---|
| `normalize_svg` | `<title>` (output filename + wall-clock date) and `<desc>` → a constant. The only run-to-run difference KiCad's SVG has (§3c). |
| `normalize_crlf` | CRLF → LF, for every text file (the fallback for any suffix with no more specific normalizer). |
| `normalize_gerber` | **G1** `%TF.CreationDate,<ts>*%` → a constant; **G2** the trailing ` date <ts>` in `G04 Created by KiCad (PCBNEW <ver>) date …*` → a constant. |
| `normalize_gbrjob` | **G3** the JSON key `Header.CreationDate` → a constant (re-serializes the JSON deterministically; both sides of every compare go through the same function, so this stays a content-only comparison). |
| `normalize_drill` | **D1** the trailing timestamp in `; DRILL file KiCad <ver> date …`; **D2** the value in `; #@! TF.CreationDate,<ts>`. |

Counted one way, that is **seven normalizing rules** (SVG's title+desc pair as one item,
CRLF as one item, and the five gerber/Excellon date-line rules G1–G3/D1–D2); counted
another way (fab output only) it is **five** (G1–G3, D1–D2). Both figures appeared in
earlier revisions of this repo's docs as if in disagreement — they are not; they are
counting different scopes, and this table is now the single place either count is
computed from. Every rule was re-verified against the 10.0.5 binary for this revision by
exporting twice, two seconds apart, in the same container, and diffing — **everything
that differed is listed above; nothing else differed.**

```
$ diff -u run1/board-F_Cu.gtl run2/board-F_Cu.gtl
 %TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%
-%TF.CreationDate,2026-08-03T04:52:25+00:00*%
+%TF.CreationDate,2026-08-03T04:52:27+00:00*%
 %TF.ProjectId,board,626f6172-642e-46b6-9963-61645f706362,rev?*%
 ...
-G04 Created by KiCad (PCBNEW 10.0.5) date 2026-08-03 04:52:25*
+G04 Created by KiCad (PCBNEW 10.0.5) date 2026-08-03 04:52:27*

$ diff -u run1/board.drl run2/board.drl
 M48
-; DRILL file KiCad 10.0.5 date 2026-08-03T04:52:57
+; DRILL file KiCad 10.0.5 date 2026-08-03T04:53:00
 ; FORMAT={-:-/ absolute / metric / decimal}
-; #@! TF.CreationDate,2026-08-03T04:52:57+00:00
+; #@! TF.CreationDate,2026-08-03T04:53:00+00:00
 ; #@! TF.GenerationSoftware,Kicad,Pcbnew,10.0.5
```

**Four things this layer does NOT normalize, and why — each verified, not assumed:**

| Candidate | Evidence | Call |
|---|---|---|
| `TF.GenerationSoftware` (gerber) | Identical across runs — a version string, not a timestamp | **Do not normalize.** Leaving it intact makes every gerber assert, for free, that it was produced by the pinned KiCad. |
| `.gbrjob`'s `Header/GenerationSoftware` | Same — stable | **Do not normalize.** Only `Header.CreationDate` moves. |
| Excellon's `TF.GenerationSoftware` header line | Stable; only the two date lines move | **Do not normalize.** |
| Drill report's `Created on` line | **Never produced** — requires `--generate-report`, which the standard answers do not pass | **No normalizer needed.** It has no input. |

**One rule that is not a normalizer.** Gerber output embeds the input file's stem, in
both the output filenames and the `%TF.ProjectId` line, whose GUID is literally the
filename's own bytes (verified: `board.kicad_pcb` → `board-F_Cu.gtl` /
`%TF.ProjectId,board,626f6172-642e-46b6-…`; the same board as `renamed.kicad_pcb` →
`renamed-F_Cu.gtl` / `%TF.ProjectId,renamed,72656e61-6d65-…`). So the runner copies each
input to its scratch directory **under the original filename**, and case authors name
board inputs `board.kicad_pcb`. Normalizing the project id instead was rejected: it
would discard a real assertion (that the tool identified the project correctly) to buy a
freedom nobody needs.

**Line endings & the canonical platform ([DL-0016]).** Text written to `expected/` is
normalized to **LF** and stored **LF**. CI compares inside the `kicad/kicad:10.0.5`
**Docker (Linux)** image, and the native Windows binary writes CRLF — so a
Windows-regenerated answer would mismatch a Linux-CI run on line endings alone. Two
measures: (1) CRLF↔LF conversion before writing and before comparing; (2) committable
expected files are regenerated inside the Docker Linux image, even when authoring on
Windows. `.gitattributes` marks `suites/**` as LF so git does not re-mangle them on
checkout.

**Honesty rule:** where an output is provably byte-identical run-to-run, add **no**
normalizer — "an identity normalizer would imply a nondeterminism that does not exist."
Some outputs are *irreducibly* nondeterministic (a board whose DRC violation *set itself*
wobbles run-to-run): those fixtures are named and excluded, not papered over.

### 4a. Proving a normalizer is load-bearing

A determinism test (`runner/determinism.py`, `python -m runner --determinism-check`)
runs each answer **twice on the same fixture** and asserts the normalized/reduced
outputs are byte-/value-identical. Every normalizer must be watched to make that test go
**red when disabled** — a normalizer that never changes anything is either dead or
masking something. "A test that cannot fail is not evidence." For each qualifying answer
the self-test also reports whether the RAW (pre-normalization) output already differed
between the two runs — informational, not a failure condition, but when raw output
*does* differ while normalized output does not, that is the concrete, printed proof the
normalizer is doing real work.

---

## 5. Expected files: per-version, oracle-authored, regenerable

The recorded correct answers live inside each case at `expected/<kicad-version>/…` (e.g.
`expected/10.0.5/summary.json`).

- **Keyed by reference-oracle version, not by adapter.** An expected file is "the correct
  answer as defined by KiCad `<ver>`." A second adapter compares *its* output against the
  same file; there is no per-adapter answer.
- **Usually exactly one JSON expected file per case.** The summary collapses what used to
  be several per-projection answers into one `summary.json` ([DL-0022]). A case has a
  second expected file only when it documents a second, genuinely different concept about
  the same input — in practice a `render`.
- **Regeneration story.** `python -m runner --regenerate` (or `scripts/run.sh
  --regenerate`) runs the reference adapter at the pinned `kicad-cli`, applies the
  reduction (and CRLF→LF), and writes `expected/<detected-version>/`. Run **inside the
  `kicad/kicad:10.0.5` Docker Linux image** so the stored bytes are platform-canonical
  ([DL-0016]); a Windows-native regenerate is fine for local iteration only. The
  contributor **inspects the diff** and commits. Answers are regenerated when the pinned
  `kicad-cli` changes, or when an input's format `(version YYYYMMDD)` token bumps.
- **Never hand-authored.** A hand-written answer encodes a human's belief about KiCad; a
  generated one encodes KiCad's behaviour. Only the latter is a conformance reference.

**Runtime, for scale.** Measured per invocation on the populated board fixture, inside
the container: `stats 448 ms, pos 385 ms, ipcd356 362 ms, svg 373 ms, gerbers 384 ms,
drill 353 ms` — a board case is six invocations ≈ 2.3 s; a schematic case is two ≈ 0.8 s.
The 8-case suite runs in well under a minute. If the suite grows to where this matters,
the fix is parallelizing across cases (each gets its own scratch dir already), not a
cache — caching a ~2-second operation keyed on file content is more machinery than it
saves, and a stale cache in a conformance suite is a false green.

---

## 6. Versioning strategy

- **Primary target: KiCad 10.0.5** — newest stable; KiCad 11 is unreleased ([DL-0001]).
- **Version-parametric matrix.** The runner detects the oracle version and looks for
  `expected/<version>/`. CI pins `kicad/kicad:10.0.5` (exact patch, ideally by digest) as
  the gating job, plus a **non-gating** `kicad/kicad:nightly` (10.99) job that tracks the
  moving KiCad-11 target and reports drift without failing the build.
- **How 11 slots in.** When `kicad/kicad:11.0`/`:11.0.0` tags publish (~early 2027), add
  a matrix entry and run `--regenerate` to populate `expected/11.0.0/`. Inputs are
  unchanged; only the recorded answers are version-specific. No case is gated on 11.
- The KiCad **format `(version YYYYMMDD)`** token — not the app version — is the true
  compatibility key. Fixtures record which format version they were authored at.

---

## 7. Remaining gaps — stated plainly

- **Pad geometry within a footprint.** The summary records where each *footprint* sits
  (1 nm precision) and which net each *pad* is on, but not where each pad sits. A bug
  that applies footprint rotation to the origin but not to the pad offsets would leave
  the summary unchanged. This is a deliberate trade — `ipcd356` prints pad positions in
  0.0001-inch integers, exactly the false-failure risk that got the float areas excluded
  (§3b.1) — and it is well covered elsewhere: the render *and* the gerbers both move when
  a pad moves. A case specifically about access-point geometry can use `extra =
  ["ipcd356"]`.
- **Graphic geometry on unplotted layers.** `stats` counts no graphics, and the gerbers
  only cover layers KiCad plots. A graphic on a layer disabled in the board's plot
  settings is recorded nowhere — narrow, and arguably correct: an unplotted layer does
  not reach the fab.
- **Cross-implementation fab comparison.** Byte answers are KiCad-vs-KiCad only (§3d/
  [DL-0015]). Rasterize-and-compare remains the fair-across-implementations answer and
  remains on the roadmap ([`ROADMAP.md`](ROADMAP.md) M6).
- **Multi-page schematic renders.** See §3c's open item — a hierarchical sheet's
  sub-sheet pages are produced by kicad-cli but not yet part of the recorded `render`
  answer.

---

## 8. What stays honest (hard parts, stated plainly)

- **Nondeterminism is real and partly irreducible.** Most is normalizable; some fixtures
  (wobbling DRC sets) must be named and excluded. Never add an identity normalizer.
- **Expected files are per-version and will churn** on every pinned-`kicad-cli` bump.
  That is the cost of KiCad-as-oracle; the regenerate flow + version subdirs manage it.
- **Coverage is expensive infra**, not a check. Budgeted as a weekly self-hosted job
  ([DL-0006], [`ROADMAP.md`](ROADMAP.md) M5) — separate from the gating suite entirely.
- **The runner is a reference, not the spec.** The contract is the case file format
  (`TEST_CASE_FORMAT.md`) and the verb protocol (§2). Keep them documented so alternate
  runners don't drift.
- **A second adapter *will* diverge from KiCad** somewhere. Those divergences are
  triaged in a checked-in ledger (verdict per entry: "KiCad's answer is right, fix the
  tool" vs "the suite is wrong"), so the suite can be stricter than any one tool without
  hiding regressions. See [DL-0009].
- **Gerber and drill coverage is byte-recorded, and byte-recorded means KiCad-only.**
  These answers are `INFO`, never `FAIL`, in ecosystem mode. The suite's fab coverage is
  real against KiCad and absent against anyone else; both halves of that sentence matter.
- **Cases record more than they need to, on purpose.** Since [DL-0025] there is no
  per-case opt-out from the standard answers, so a DRC case also carries gerbers it is
  not about. This trades a little redundancy for a manifest with no knobs to get wrong.

---

## 9. Multi-file inputs: the `inputs`/`root` fix, verified

`case.toml` may declare `inputs = [...]` (a multi-sheet schematic, or the member files of
a `.pretty` directory) instead of a single `input`, plus `root` naming which entry is the
netlist root. `suites/schematic-parse/hierarchical-sheet/` is the proof case: a root
sheet places one component and instantiates a sub-sheet; the sub-sheet places a second
component; both are tied to the same net via a GLOBAL label (a genuine cross-sheet net,
not two same-named-but-disconnected local labels). Its `expected/10.0.5/summary.json`
lists **both** `U1` (`sheet: "/"`) and `U2` (`sheet: "/sub/"`), and a `SIG` net spanning
`U1.1`/`U2.1`.

This only works because `engine.py` passes **every** declared input to the adapter (not
just the first), and `adapters/kicad.py`'s `_scratch_copy_all` copies every one into a
single scratch directory under its own filename before running `sch export netlist`
against the scratch copy of `root`. The bug this replaces was verified directly: running
the adapter's old single-file copy against this same fixture (copying only `root.kicad_sch`
and never writing `sub.kicad_sch` to scratch) does not error — `sch export netlist` exits
0 and silently produces a netlist covering only the root sheet, i.e. a `summary.json`
missing `U2` entirely and a `SIG` net with only one member instead of two. A case that
can't fail is not evidence — this is the case that makes it fail, and shows exactly how.
