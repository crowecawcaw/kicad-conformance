# Design — kicad-conformance architecture

This document defines the architecture. Companion docs:
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) (how to author a case),
[`VALIDATION.md`](VALIDATION.md) (the L0–L3 comparator ladder — the L2 semantic-extraction
and L3 SVG-render comparators that go beyond §3's exit/structured/golden modes, [DL-0019]–[DL-0021]),
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
| `parse-fp` | `.pretty` **dir** (never a lone `.kicad_mod`) | success/failure; canonical s-expr | `fp upgrade --force -o <dir> <in_dir>` |
| `upgrade` | any of the above | canonical re-save (golden compared) | same `… upgrade --force` subcommand as the matching `parse-*` |
| `erc` | `.kicad_sch` | structured violation set | `sch erc --format json --severity-all -o <out>/erc.json` |
| `drc` | `.kicad_pcb` | structured violation set | `pcb drc --format json --units mm --severity-all -o <out>/drc.json` |
| `netlist` | `.kicad_sch` (root) | structured net→node membership | `sch export netlist --format kicadsexpr -o <out>/netlist.net` |
| `export-gerbers` | `.kicad_pcb` | golden file set (RS-274X) | `pcb export gerbers --layers <pinned> --no-protel-ext -o <out>/` |
| `export-drill` | `.kicad_pcb` | golden file set (Excellon + report) | `pcb export drill --generate-report --report-path <r> -o <dir>` |
| `export-pos` | `.kicad_pcb` | golden file (CSV/ASCII) | `pcb export pos --format csv --side both --units mm -o <out>/pos.csv` |
| `export-step` | `.kicad_pcb` | geometry (bbox/tolerance) — **scope TBD** | `pcb export step` (heavy, least deterministic; see [DL-0012](DECISIONS.md)) |
| `bom` | `.kicad_sch` | golden file (CSV) | `sch export bom -o <out>/bom.csv` (fixed field/sort spec) |

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
| `drc` | `-o <out>/drc.json` | `<out>/drc.json` |
| `erc` | `-o <out>/erc.json` | `<out>/erc.json` |
| `netlist` | `-o <out>/netlist.net` | `<out>/netlist.net` |
| `bom` | `-o <out>/bom.csv` | `<out>/bom.csv` |
| `export-pos` | `-o <out>/pos.csv` | `<out>/pos.csv` |
| `export-gerbers` | `-o <out>/` (+ pinned `--layers`, `--no-protel-ext`) | every file under `<out>/` |
| `export-drill` | `-o <out>/` `--report-path <out>/drill-report.rpt` | every file under `<out>/` |
| `parse-sch`/`parse-pcb`/`upgrade` (pcb/sch) | (no `-o`; rewrites in place) | the scratch copy, read back |
| `parse-sym`/`parse-fp` | `-o <out>` (path must **not** pre-exist — see gotcha above) | `<out>/…` |

This keeps every artifact location deterministic and avoids CWD pollution.

### 2b. Gerber layer set is an explicit case parameter, not a fixed list

Default `pcb export gerbers` on a 2-layer board emits **seven** files, not four:
KiCad plots every *enabled/plottable* layer, so a bare export produced
`bb-F_Cu.gtl bb-B_Cu.gbl bb-Edge_Cuts.gm1 bb-F_Courtyard.gbr bb-B_Courtyard.gbr
bb-Margin.gbr bb-job.gbrjob` — Protel extensions (`.gtl/.gbl/.gm1`), plus Courtyard and
Margin layers the worked examples never listed. Because the file set is a function of
board state, `golden-dir` contents are otherwise unpredictable.

**The gerber verb therefore pins the layer set explicitly**: `--layers F.Cu,B.Cu,Edge.Cuts`
(the layer set is a per-case parameter, overridable via `args`) and `--no-protel-ext`
for stable `.gbr` extensions. A pinned three-layer export yields exactly
`<stem>-F_Cu.gbr`, `<stem>-B_Cu.gbr`, `<stem>-Edge_Cuts.gbr`, plus `<stem>-job.gbrjob`.
The `.gbrjob` is JSON carrying its own creation date and needs a normalizer (§4).

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

Every check declares a `compare` mode. Pass/fail is decided per check. The modes below
(`exit`, `structured`, `golden-file`/`golden-dir`) are the **L0/L1/L2** rungs of the
comparator ladder; [`VALIDATION.md`](VALIDATION.md) formalizes that ladder and adds the
richer **L2** interchange projections (stats / pos / ipcd356, extending `structured`) and an
**L3** SVG-render comparator (a new `image` mode) — [DL-0019]–[DL-0021].

### 3a. `exit` — success/failure polarity (+ error substring)

The baseline mode, and the *only* thing a `failure` case needs. Mirrors openjd's
filename-polarity trick, moved into the manifest:

- `expect = "ok"` → the adapter must exit `0`.
- `expect = "error"` → the adapter must exit with a **bounded, graceful non-zero** exit —
  i.e. a clean rejection, *not* a crash (see the crash verdict below).
- For `expect = "error"`, an optional `error_contains = "…"` asserts a substring on
  **stderr** (per-stream, not merged, so a warning can't satisfy an error check). An
  `error_contains_any = ["…", "…"]` escape hatch tolerates legitimate wording variation
  between implementations (openjd's `anyOf`).

Substring matching is deliberately loose: it pins the *observable contract* (the tool
rejects a malformed board and says something about the offending token) without
over-fitting to KiCad's exact phrasing, so a second adapter with different error text
still conforms.

**Crash verdict — a crash is NEVER a pass ([DL-0013]).** A malformed input can make the
oracle *crash* rather than reject cleanly: on 10.0.5, a truncated board makes
`pcb upgrade` print a good `Expecting '('` message and then **segfault** (exit 139 on
native Windows; a `SIGSEGV` on Docker Linux). 139 is non-zero, so a naïve "non-zero =
rejected" rule would silently *pass* an `expect="error"` case on a **crash** — building
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
`Failed to load schematic`), an `expect="error"` case must ship a runner-enforceable
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
§3a above) reports green without either loosening its `expect="error"`/`error_contains`
assertion or leaving the gating build permanently red over a bug filed upstream, not in
this repo. See [DL-0018] and [`DIVERGENCES.md`](DIVERGENCES.md) for the full rationale
and the ledger entry.

### 3b. `structured` — semantic reduction (DRC, ERC, netlist)

For outputs where formatting, ordering, and internal IDs are irrelevant, a byte compare
is meaningless. The runner parses both sides into a canonical structure and compares
membership:

- **netlist** → `{ net-name : sorted set of (refdes, pin) }`. A pin on the wrong node,
  a split net, a misnamed net fails; formatting/net-code/order never does. The netlist
  output also embeds the absolute source path, a `(date …)`, and `(tool "Eeschema …")`,
  so netlist is **always** `structured`, never `golden-file`. A multi-sheet schematic has
  one **root** sheet handed to `sch export netlist`; the case names it with the explicit
  `root` field (one entry of `inputs` — see [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md)
  §4.1), and the adapter reproduces the subsheets' relative on-disk layout in scratch so
  child-sheet resolution works.
- **DRC / ERC** → sorted list of `(rule-id, severity, sorted item locations)`. Sorted by
  content, **not by UUID** — some violation-item UUIDs are minted fresh each run.
- The structural reduction is defined per verb in the runner and documented so a second
  adapter knows what shape to emit.

**What is stored as the golden for a `structured` check ([DL-0014]).** The golden is a
**canonical reduction** committed under `golden/<version>/` (e.g. `drc.reduced.json`, or
the net→node map), *not* the raw KiCad JSON/s-expr. `--regenerate` runs the oracle,
applies the per-verb reduction, and writes the reduced form; at compare time the runner
reduces the adapter's output the same way and asserts **membership equality** against the
stored reduction. Storing the reduction (rather than the raw report the reduction is
"derived from") makes the golden self-describing, diffs review as semantic changes, and a
second adapter is judged on the same reduced shape.

Residue is **characterized, not hidden**: when a structured compare fails, the runner
reports *how* (names-only difference vs membership difference vs count difference), so a
mismatch is actionable rather than an opaque "differs."

### 3c. `golden-file` / `golden-dir` — normalized text compare (gerbers, drill, upgraded s-expr, pos, bom)

For rich text/interchange outputs, compare **byte-exact after normalization** (§4).
`golden-file` is a single output; `golden-dir` is a multi-file set (gerbers emit one
file per layer + a job file) — the whole tree is normalized and compared, missing/extra
files are failures.

**What a byte golden actually measures — regression, not cross-adapter conformance
([DL-0015]).** A `golden-file`/`golden-dir` compare pins KiCad's *exact formatting* —
token ordering, whitespace, aperture numbering, comment style. That makes it an excellent
**KiCad self-consistency / version-regression** signal (did an oracle bump change the
bytes?), but it **over-fits a second adapter**: a clean-room engine emits
valid-but-differently-formatted output and would "diverge" on essentially every
upgrade/gerber golden for reasons that are *not bugs*. Therefore:

- **Cross-adapter conformance is judged on the portable subset** — `structured`/semantic
  compares plus exit polarity (§3a) and error substrings. These port across
  implementations.
- **Byte goldens are a KiCad-version-regression tool**, primarily meaningful for the
  KiCad adapter. A second adapter runs the **semantic subset** of these verbs (parse both
  sides, compare the model), not the byte compare.
- The divergence ledger ([DL-0009]) must **not** fill with pure-formatting diffs: a
  second-adapter diff on a `golden-file`/`golden-dir` verb that reduces to an identical
  semantic model is **auto-classified as formatting-only** and kept out of the
  conformance findings. See [DL-0015] and MAJOR-8 in the review.

### 3d. Geometry tolerance (STEP / future 3D) — printed-quantum, no pre-authorized bands

Where a numeric export is compared, tolerance is **the precision the export prints, and
nothing wider** — for KiCad's integer-nanometre board unit that means exact-integer nm
for coordinate exports, and "round to the same printed string" for `stats`-style
figures. We explicitly **refuse pre-approved tolerance bands**: a pre-approved tolerance
band is the shape of thing that silently absorbs a real bug — the moment a band is wider
than the export's own printed precision, a genuine coordinate error can hide inside it.
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
test (§4a). Concrete sources to strip — the union of prior clean-room-engine
normalization findings and this project's own CLI research:

| Output kind | Strip / normalize |
|---|---|
| s-expr (upgrade) | `(generator_version "…")`, sometimes `(generator …)`. **Keep** `(version YYYYMMDD)` — it is the compatibility key; a bump means *re-baseline the golden*, not strip. Canonicalize fresh UUIDs only if the operation minted them. |
| Gerber (RS-274X) | `G04` header lines: `TF.CreationDate,<ISO>`, `TF.GenerationSoftware,KiCad,Pcbnew,<ver>`, and the "Created by KiCad … date" comment. |
| Gerber job file (`.gbrjob`) | JSON `CreationDate` under `Header/CreationDate` (and the KiCad version string alongside it, `Header/GenerationSoftware/Version`). The job file is JSON, so it diffs run-to-run on its own creation date — this is a *separate* normalizer from the `G04` gerber stripping above. |
| Excellon drill | header creation date + KiCad version. |
| Drill report | "Created on" wall-clock stamp. |
| DRC/ERC JSON | drop top-level `date`, `kicad_version`, absolute input path; sort `violations`, `unconnected_items`, `schematic_parity` and each violation's `items[]` by content-derived order (not by UUID). |
| pos / GenCAD / IPC-2581 / IPC-D-356 / ODB++ | generation timestamp + tool/version; IPC-D-356 trailing `S…` serial on `VIA` records only (keep meaningful `S0/S1/S2` on pads); zip/xml mtimes for ODB++/IPC-2581. |
| netlist / BOM | header line with tool name, version, date; BOM row order only deterministic with a fixed sort/group spec. |
| SVG | tool comment; canonicalize FP coordinate number formatting. |
| PDF | `/CreationDate`, `/ModDate`, random `/ID`, producer. **Least diffable — avoid PDF for conformance**; prefer SVG/plot text. |
| STEP / BREP (OCC) | ISO-10303 `FILE_NAME` timestamp/author/system; entity ordering + FP tessellation not byte-stable across OCC versions → compare geometrically, not textually. |

**Line endings & golden platform ([DL-0016]).** Text goldens are normalized to **LF**
before compare and stored **LF** in the repo. A contributor may develop on Windows, but
CI compares inside the `kicad/kicad:10.0.5` **Docker (Linux)** image, and the native
Windows binary writes CRLF (and can leak `\` path separators into messages) — so a
Windows-regenerated golden would mismatch a Linux-CI run on line endings alone. Two
measures keep goldens platform-canonical: (1) the normalizer converts CRLF↔LF and stores
LF for every text golden; (2) **committable goldens are regenerated inside the Docker
Linux image** — `--regenerate` should be run in the container so the bytes are Linux-
canonical, even when authoring on Windows. A `.gitattributes` entry marks `golden/**`
(and text fixtures) as LF so git does not re-mangle them on checkout. See [DL-0016] and
§5.

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

## 5. Goldens: per-version, oracle-authored, regenerable

Goldens live inside each case at `golden/<kicad-version>/…` (e.g. `golden/10.0.5/`).
Rationale and mechanics:

- **Keyed by reference-oracle version, not by adapter.** The golden is "the correct
  answer as defined by KiCad `<ver>`." A second adapter compares *its* output against
  the same golden; there is no per-adapter golden.
- **A single input fixture can drive multiple goldens** — one board's `case.toml` may
  declare `drc` (`structured`; the stored golden is the **canonical reduction**, e.g.
  `drc.reduced.json`, not the raw report — see §3b and [DL-0014]), `export-gerbers`
  (`golden-dir`), and `export-drill` (`golden-dir`), each producing its own artifact under
  `golden/10.0.5/`.
- **Regeneration story.** `python -m runner --regenerate` runs the reference adapter at
  the currently-installed/pinned `kicad-cli`, normalizes (including CRLF→LF), and writes
  `golden/<detected-version>/`. For committable goldens, run `--regenerate` **inside the
  `kicad/kicad:10.0.5` Docker Linux image** so the stored bytes are platform-canonical
  ([DL-0016]); a Windows-native regenerate is fine for local iteration but should not be
  the source of committed goldens. The contributor **inspects the diff** and commits.
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
  `(zone …)`) appear across the fixtures and goldens. **Unexercised top-level sections /
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
