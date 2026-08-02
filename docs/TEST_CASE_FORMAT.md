# Test-case format — authoring spec

This is the contract for what a test case *is* on disk. It is the most important doc for
goal #1 (documentation) and goal #3 (AI-agent readability): the format is designed so a
directory listing reads like a coverage map and each case is a self-contained worked
example. Architecture context is in [`DESIGN.md`](DESIGN.md); rationale in
[`DECISIONS.md`](DECISIONS.md).

---

## 1. The format decision: tiny per-case manifest, not filename-only

The OpenJobDescription prior art encodes the entire expectation in the filename
(`.invalid`) plus an optional inline `expected:` block, with no side-car manifest. That
works there because **every case is the same operation** — validate one template, or run
one job. It does not work here, and we deliberately diverge:

- KiCad operations are **diverse** (`parse-pcb`, `drc`, `export-gerbers`, `netlist`, …).
  A filename can't say *which verb* to run.
- Some outputs are **multi-file** (gerbers = one file per layer + a job file). A single
  `.golden` sibling can't hold them.
- Goldens are **per KiCad version**. Filename encoding can't express a version axis.
- The owner explicitly wants **one input fixture to drive multiple operation-checks**.

So a case is a **directory with a tiny `case.toml` manifest** ([DL-0003]). We keep the
manifest as small as openjd's implicit convention allows — the common single-check case
is ~4 lines — and we **keep the openjd virtues** by encoding suite, polarity, and
concept in the *directory path and slug*. TOML (not YAML) is chosen for comment support,
whitespace-insensitivity, and consistency with the surrounding ecosystem
(Cargo/pcb manifests are TOML). See [DL-0003].

Two case shapes, both first-class:

- **Single-concept case (the default, and the norm for every verb suite).** One fixture,
  one check, one behavior. The directory slug names the behavior; the manifest is a few
  lines. Verb suites (`drc/`, `gerber/`, …) contain **only** single-verb cases, so their
  directory listing stays a true coverage map for that verb.
- **Multi-operation case (the exception) lives in a dedicated `integration/` suite
  ([DL-0017]).** One fixture, several `[[check]]` entries across different verbs — used
  when the *point being documented* is that one real board yields consistent DRC + fab
  outputs. It does **not** live under any one verb's suite (that would hide, e.g., a DRC
  check inside `board-parse/…` from someone browsing `drc/`). Instead:
  1. multi-verb cases go under `suites/integration/`, keeping each verb suite pure; and
  2. the runner emits a **generated per-verb coverage index** (`--coverage-proxy`,
     [`DESIGN.md`](DESIGN.md) §7a) that lists **every** `[[check]]` by its `op` regardless
     of which directory it lives in — so a `drc` check inside an `integration/` case still
     shows up under `drc` in the coverage map.

  The directory tree remains "listing = coverage map" *per verb suite*, and the generated
  index restores full cross-suite coverage visibility for the multi-op exception.

---

## 2. Directory layout

```
suites/<suite>/<happy|failure>/<NNNN-slug>/
├── case.toml                 # required: the manifest
├── <fixture files>           # required: the input(s), smallest that shows the concept
└── golden/                   # only for cases with a golden-file/golden-dir/structured compare
    ├── 10.0.5/               # per KiCad version (regenerated, oracle-authored)
    │   └── <artifacts>
    └── 11.0.0/               # added when KiCad 11 ships; fixtures unchanged
        └── <artifacts>
```

Axes (mirroring openjd's three orthogonal axes, re-mapped to KiCad):

1. **`<suite>`** — the operation family = adapter verb group. One of:
   `schematic-parse`, `board-parse`, `erc`, `drc`, `gerber`, `drill`, `netlist`,
   `symbol-lib`, `footprint-lib` (and `step`/`bom` if ratified). This is what a reader
   looking for "how does DRC behave" browses to. Each verb suite holds **only single-verb
   cases**. Multi-verb cases live in the separate **`integration/`** suite ([DL-0017]);
   their per-verb coverage is recovered from the runner's generated coverage index, not
   from the directory tree.
2. **`<happy|failure>`** — polarity. `happy/` = must succeed / must match golden;
   `failure/` = the tool must reject the input. A directory listing self-partitions into
   "must accept" and "must reject" at a glance.
3. **KiCad version** — lives *inside* the case under `golden/<version>/`, not as a
   top-level directory, because fixtures are shared across versions; only goldens differ.

The large real-world **`corpus/`** (gitignored, pinned by `manifest.toml`) is a separate
tree used only for the scheduled coverage sweep and broad regression — it is *not* part
of `suites/` and is not hand-authored. See [`DESIGN.md`](DESIGN.md) §7 and [DL-0009].

---

## 3. Naming conventions (the directory listing IS the index)

```
<NNNN>-<slug>/
```

- **`<NNNN>`** — a 4-digit ordinal, unique within `<suite>/<polarity>/`, zero-padded so
  listings sort stably. It is a stable handle, not a spec-section number (KiCad formats
  have no stable section numbering the way OpenJD's schema does; the `doc =` field in the
  manifest carries the format-doc citation instead).
- **`<slug>`** — a hyphenated phrase that reads as a sentence fragment describing the one
  behavior. `failure/` slugs name the defect.

The slug should let a human read the behavior without opening the case:

```
suites/board-parse/happy/0002-minimal-two-layer-board/
suites/board-parse/failure/0001-unterminated-sexpr/
suites/board-parse/failure/0002-unknown-layer-count/
suites/schematic-parse/happy/0001-empty-root-sheet/
suites/schematic-parse/failure/0003-missing-uuid-on-symbol/
suites/drc/happy/0004-clearance-violation-reported/
suites/netlist/happy/0002-two-nets-one-shared-pin/
```

Reading `suites/board-parse/failure/` top to bottom is a checklist of the board parser's
rejection behavior. This is the docs-as-tests property, carried by the path instead of
the filename.

---

## 4. `case.toml` schema

### 4.1 Top-level (case) fields

| Field | Req | Type | Meaning |
|---|---|---|---|
| `concept` | **yes** | string | One sentence: the single behavior this case documents. Shown in reports and reads as the case's headline. |
| `doc` | recommended | string | Format-doc / behavior citation, e.g. `"sexpr-pcb#layers"` or `"cli:pcb-drc"`. Ties the case to documentation. |
| `input` | yes* | string | The fixture path, relative to the case dir. Use `inputs` for multi-file input (a `.pretty` dir, a multi-sheet schematic). Exactly one of `input`/`inputs`. |
| `inputs` | yes* | array\<string\> | Multi-file input (e.g. every `.kicad_sch` of a multi-sheet schematic, or the members of a `.pretty` dir). |
| `root` | cond | string | **Required when `inputs` is a multi-sheet schematic** for a `netlist` check: names which entry of `inputs` is the **root sheet** handed to `sch export netlist`. The remaining `inputs` are subsheets; the adapter reproduces their relative on-disk layout in scratch so child-sheet resolution works. With a single `input`, that file is the root and `root` is omitted. |
| `tags` | no | array\<string\> | Free-form labels for filtering (`["zones", "regression"]`). |
| `min_kicad` | no | string | Skip (counted) below this oracle version, for behavior that doesn't exist in older KiCad. |
| `skip_reason` | no | string | If present, the case is skipped-and-counted with this reason (e.g. an irreducibly nondeterministic fixture). |
| `known_divergence` | no | table | Declares a known, tracked non-conformance of the **reference oracle itself** (e.g. the KiCad 10.0.5 PCB-parse segfault) as a **strict xfail** ([DL-0018](DECISIONS.md), [`DESIGN.md`](DESIGN.md) §3a). Default for every check in the case; a `[[check]]` may override with its own `known_divergence`. See §4.3 below. |
| `[[check]]` | **yes** | table array | One or more checks (below). Order is preserved in reports. |

### 4.2 `[[check]]` fields

| Field | Req | Type | Meaning |
|---|---|---|---|
| `op` | **yes** | string | The adapter verb: `parse-sch`, `parse-pcb`, `parse-sym`, `parse-fp`, `upgrade`, `erc`, `drc`, `netlist`, `export-gerbers`, `export-drill`, `export-pos`, `bom`, `version`. |
| `expect` | **yes** | `"ok"` \| `"error"` | Exit-code polarity. `ok` = exit 0; `error` = non-zero. |
| `error_contains` | no | string | (only `expect="error"`) substring that must appear on **stderr**. |
| `error_contains_any` | no | array\<string\> | (only `expect="error"`) at least one substring must appear on stderr (wording escape hatch). |
| `compare` | no | `"exit"` \| `"structured"` \| `"golden-file"` \| `"golden-dir"` | Comparison mode ([`DESIGN.md`](DESIGN.md) §3). Defaults to `"exit"` (polarity only). |
| `golden` | cond | string | (required for `golden-file`/`golden-dir`/`structured`) artifact name resolved under `golden/<version>/`. A file for `golden-file`, a directory for `golden-dir`. For `structured` it names the stored **canonical reduction** (e.g. `drc.reduced.json`, or the net→node map) that `--regenerate` produced from the oracle — **not** the raw KiCad report; the runner reduces the adapter's output the same way and compares by membership ([`DESIGN.md`](DESIGN.md) §3b, [DL-0014]). |
| `control` | cond | string / table | (required for `failure` cases) the **positive control**: a defect-free sibling fixture (path) or an inline patch that removes the injected defect. The runner re-runs the same check against it and requires exit 0 — proving the case can actually fail ([`DESIGN.md`](DESIGN.md) §3a, [DL-0013]). |
| `format` | no | string | Verb-specific output format override (e.g. a non-default netlist format). |
| `args` | no | array\<string\> | Extra verb-specific flags passed through to the adapter, for cases exercising a specific option. Use sparingly; document why in `concept`. |
| `name` | no | string | Short label when a case has several checks, so reports name each. |
| `known_divergence` | no | table | Per-check override of the case-level `known_divergence` (same schema, §4.3). |

**Rules the runner enforces:**

- Exactly one of `input` / `inputs`.
- `golden` is required iff `compare` is `golden-file`, `golden-dir`, or `structured`.
- `error_contains*` is only valid with `expect = "error"`.
- A `failure` case must contain at least one `expect = "error"` check; a `happy` case
  must contain no `expect = "error"` check. (Polarity and directory must agree — this
  catches a miscategorized case.)
- If any `[[check]]` needs a golden, `golden/<version>/` must exist for the pinned
  version, or the case is reported as **needs-regenerate**, not passed.
- **A `CRASH` is never a pass.** The runner classifies each invocation as `OK` /
  `REJECT` / `CRASH` ([`DESIGN.md`](DESIGN.md) §3a, [DL-0013]). Termination by signal, or
  exit code `> 128` (128 + signal; on Windows, a fatal-exception status), is a `CRASH` —
  reported as its own verdict and counted as a **failure of the case**, whether the case
  is `happy` or `failure`. `expect = "error"` is satisfied **only** by a `REJECT` (a
  bounded, graceful non-zero exit), never by a crash. Detection is by signal / `>128`
  semantics, portable across Windows-native and Docker-Linux — the literal `139` is never
  hard-coded.
- **Every `failure` case must carry a positive control.** The runner runs the control
  (defect-free sibling / inline patch, via the `control` field) through the same check and
  requires exit 0. If the control does not flip to `OK`, the case is reported as
  **not-evidence**, never passed — "a test that can't fail is not evidence." This is how a
  schematic failure case (whose stderr is the undiscriminating `Failed to load schematic`)
  proves the *specific* defect is what triggered the rejection.
- **A declared `known_divergence` never changes the OK/REJECT/CRASH verdict itself** —
  see §4.3 — it only reinterprets an already-computed, already-bad verdict that matches
  the declaration, and only after any positive control has already passed.

### 4.3 `known_divergence` sub-schema (strict xfail, [DL-0018])

A `known_divergence` table — at case level (`[known_divergence]`, default for every
check) or check level (`known_divergence = { ... }`, overrides the case default for that
one check) — declares that the **reference oracle itself** is known to diverge from the
behavior the case otherwise asserts, and that this is tracked, not silently tolerated:

| Field | Req | Type | Meaning |
|---|---|---|---|
| `reason` | **yes** | string | One line: why the oracle diverges (what actually happens instead of the desired behavior). Cite `docs/DIVERGENCES.md` for the full writeup. |
| `kind` | **yes** | string | The category of divergence. Currently used: `"crash"` (the oracle segfaults/is signaled instead of a graceful rejection). Other kinds (e.g. `"reject-expected-accept"`) are reserved for future use as they come up. |
| `tracking` | no | string | An upstream issue URL/id, or a placeholder like `"TODO: file upstream"` until one exists. |

**Semantics (strict xfail) — a layer on top of the OK/REJECT/CRASH verdict, never a
replacement for it:**

- If the check's actual verdict matches the declared `kind` (e.g. the runner classifies
  the invocation as `CRASH` and `kind = "crash"`), the check is scored **`XFAIL`**
  ("known divergence") — not a failure; the build stays green.
- If the same check instead reaches its normally-desired outcome (a clean `OK`/graceful
  `REJECT` — the oracle got fixed), that is an **`XPASS`**, and XPASS **fails the build**
  with a message pointing at `docs/DIVERGENCES.md`: a strict xfail must not be allowed to
  rot, so this forces the ledger and the `known_divergence` marker to be updated by hand
  rather than silently going stale.
- A check whose verdict is bad but does **not** match the declared `kind` (some other,
  undeclared failure) is reported as an ordinary `FAIL`/`CRASH` — the marker only covers
  the specific divergence it names.

Worked snippet (the shape used by `board-parse/failure/0001-unterminated-sexpr`, where
`kicad-cli` 10.0.5 prints the correct `Expecting …` message and then segfaults instead of
rejecting cleanly):

```toml
concept = "A board whose first token is malformed is rejected with a parse-position error."
doc     = "sexpr-intro"
input   = "board.kicad_pcb"
control = "control.kicad_pcb"

[known_divergence]
kind     = "crash"
reason   = "kicad-cli 10.0.5 segfaults after printing the correct 'Expecting' message instead of exiting gracefully -- see docs/DIVERGENCES.md."
tracking = "TODO: file upstream"

[[check]]
op             = "parse-pcb"
expect         = "error"          # the DESIRED behavior -- unchanged by the marker above
error_contains = "Expecting"
```

The case still asserts the behavior we actually want (a graceful `Expecting …` rejection);
`known_divergence` only records that *today's* oracle can't deliver it, so the suite stays
honest (the assertion is unchanged) and the build stays green (the known-bad verdict is
scored `XFAIL`, not `FAIL`/`CRASH`).

---

## 5. Fully-worked examples

These are illustrations written verbatim as they would appear on disk. **They are not
files in the repo yet** — they document the format.

### 5.1 Happy path — schematic parse

`suites/schematic-parse/happy/0001-empty-root-sheet/case.toml`:

```toml
concept = "An empty root schematic sheet (title block only, no symbols) parses and canonicalizes."
doc     = "sexpr-schematic"

[[check]]
op      = "parse-sch"
expect  = "ok"
compare = "golden-file"
golden  = "canonical.kicad_sch"   # KiCad's `sch upgrade --force` output, normalized
```

`suites/schematic-parse/happy/0001-empty-root-sheet/sheet.kicad_sch` (the fixture — a
minimal but real schematic):

```
(kicad_sch
  (version 20250114)
  (generator "eeschema")
  (generator_version "10.0.5")
  (uuid "6f3a1c2e-0000-4000-8000-000000000001")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
)
```

On disk, alongside those two files:
`suites/schematic-parse/happy/0001-empty-root-sheet/golden/10.0.5/canonical.kicad_sch`
— produced by `python -m runner --regenerate`, with `(generator_version …)` normalized
per [`DESIGN.md`](DESIGN.md) §4.

**What the runner does:** copies `sheet.kicad_sch` to a scratch dir, runs
`kicad-cli sch upgrade --force <scratch>`, reads the rewritten file back, normalizes it,
and asserts byte-equality against the golden. Exit must be 0.

### 5.2 Failure path — malformed s-expression

`suites/schematic-parse/failure/0001-unterminated-sexpr/case.toml`:

```toml
concept = "A schematic with an unterminated s-expression is rejected by the parser."
doc     = "sexpr-intro"
input   = "sheet.kicad_sch"
control = "control.kicad_sch"   # same sheet with the paren restored → must exit 0

[[check]]
op             = "parse-sch"
expect         = "error"
error_contains = "Failed to load schematic"   # the ONLY message KiCad's sch loader emits
```

**Why `"Failed to load schematic"` and not `"Expecting"`:** empirically (KiCad 10.0.5)
`sch upgrade` emits the *same* generic `Failed to load schematic` (exit 3) for **every**
malformed schematic — unterminated, truncated, unknown token, missing `(version)`. The
schematic loader **cannot discriminate which defect fired** via stderr, so a schematic
failure case pins this coarse message and leans on the **positive control** (`control`
above) to prove that *this* defect — not something incidental — is what triggers the
rejection. (A PCB parse-failure case is different: the PCB loader surfaces position, so it
*may* assert the real `Expecting '('` substring — see §5.2b and [`DESIGN.md`](DESIGN.md)
§2c.)

`suites/schematic-parse/failure/0001-unterminated-sexpr/sheet.kicad_sch` (the fixture —
note the missing closing paren on `lib_symbols`, which is the entire defect):

```
(kicad_sch
  (version 20250114)
  (generator "eeschema")
  (uuid "6f3a1c2e-0000-4000-8000-000000000002")
  (paper "A4")
  (lib_symbols
  (sheet_instances
    (path "/" (page "1"))
  )
)
```

No `golden/` directory — a failure case asserts only that the tool rejects the input.
For the schematic side stderr cannot say *which* defect fired, so the positive control
(`control.kicad_sch`, the same sheet with the paren restored → exit 0) is what proves the
case fails for the right reason. `error_contains` is a loose substring so a second adapter
with different wording still conforms; use `error_contains_any` if even that is too tight.

### 5.2b Failure path — PCB parse error (the loader *does* surface position)

Unlike schematic, the PCB loader reports the parse position, so a PCB failure case may
assert the specific substring. `suites/board-parse/failure/0001-unterminated-sexpr/case.toml`:

```toml
concept = "A board whose first token is malformed is rejected with a parse-position error."
doc     = "sexpr-intro"
input   = "board.kicad_pcb"
control = "control.kicad_pcb"          # well-formed board → must exit 0

[[check]]
op             = "parse-pcb"
expect         = "error"
error_contains = "Expecting"           # PCB surfaces e.g. "Expecting '(' … line 2, offset 1."
```

**Crash caveat (observed, 10.0.5).** On this oracle version, `pcb upgrade` on a truncated
board prints the good `Expecting '('` message and then **segfaults** (exit 139 native
Windows / `SIGSEGV` Docker Linux). That is a `CRASH`, not a clean `REJECT` — and a
`CRASH` never satisfies `expect = "error"` ([`DESIGN.md`](DESIGN.md) §3a, [DL-0013]). This
case therefore documents a **known oracle bug** filed in the divergence/known-issues
ledger; asserting the real `Expecting` substring means that when a future KiCad rejects
*cleanly* (a `REJECT`, no crash), the case starts passing without any edit. Until then the
runner reports it as a crash against KiCad, not a green conformance pass.

### 5.3 One board fixture driving DRC + gerber-export (multi-operation, `integration/` suite)

This is the "single input, multiple outputs" shape the owner asked for. Because it spans
several verbs it lives in the dedicated **`integration/`** suite ([DL-0017]) — *not* under
`board-parse/` — so the per-verb suites stay pure; its `drc`/`gerber` checks are recovered
in the runner's generated per-verb coverage index ([`DESIGN.md`](DESIGN.md) §7a).

`suites/integration/happy/0007-board-with-clearance-and-fab-output/case.toml`:

```toml
concept = "A small routed two-layer board: DRC is clean and gerber output is stable."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
tags    = ["integration", "fab"]

# 1) The board must load and re-save canonically.
[[check]]
name    = "parse"
op      = "parse-pcb"
expect  = "ok"
compare = "golden-file"
golden  = "canonical.kicad_pcb"

# 2) DRC must report the exact same violation set as KiCad (here: none).
[[check]]
name    = "drc"
op      = "drc"
expect  = "ok"
compare = "structured"                 # semantic reduction of the DRC JSON
golden  = "drc.reduced.json"           # the stored CANONICAL REDUCTION, not raw KiCad JSON

# 3) Gerber export must match the normalized golden file set (per-layer + job file).
#    The layer set is PINNED so the golden-dir membership is deterministic (see DESIGN §2b).
[[check]]
name    = "gerbers"
op      = "export-gerbers"
expect  = "ok"
compare = "golden-dir"
golden  = "gerbers/"                    # RS-274X files, G04 headers + .gbrjob date normalized
args    = ["--layers", "F.Cu,B.Cu,Edge.Cuts", "--no-protel-ext"]
```

On-disk tree for this one case:

```
suites/integration/happy/0007-board-with-clearance-and-fab-output/
├── case.toml
├── board.kicad_pcb
└── golden/
    └── 10.0.5/
        ├── canonical.kicad_pcb
        ├── drc.reduced.json           # canonical reduction (the structured golden)
        └── gerbers/                   # membership fixed by the pinned --layers set:
            ├── board-F_Cu.gbr         #   F.Cu    (KiCad ext via --no-protel-ext)
            ├── board-B_Cu.gbr         #   B.Cu
            ├── board-Edge_Cuts.gbr    #   Edge.Cuts
            └── board-job.gbrjob       #   JSON job file (CreationDate normalized, DESIGN §4)
```

Without the pinned `--layers`, a default export of this 2-layer board would emit **seven**
files (adding `F_Courtyard`/`B_Courtyard`/`Margin`, and Protel `.gtl/.gbl/.gm1`
extensions) — membership would depend on board state and the golden would churn. Pinning
the layer set makes the golden-dir contents an explicit, stable case parameter.

One fixture, three goldens, three verbs — no fixture duplication. If KiCad 11 changes
the canonical form or gerber header, `--regenerate` adds a sibling `golden/11.0.0/`
without touching `board.kicad_pcb`.

---

## 6. Where each behavior fires (parse-time vs run-time)

Decide early where a rule triggers, so the case lands in the right suite and asserts the
right stage (openjd's `.invalid` vs `.invalid.test` distinction):

- **Parse/load-time** failures (malformed s-expr, unknown token, bad layer count) →
  `schematic-parse` / `board-parse` `failure/`, verb `parse-*`, `expect = "error"`.
- **Rule-time** findings (a clearance violation, an unconnected net) are *not* failures —
  the tool exits 0 and *reports* them. These are `drc`/`erc` `happy/` cases with
  `compare = "structured"`; the violation is data in the golden, asserted by membership.
  Do **not** pass `--exit-code-violations`.

---

## 7. Contributor checklist

- [ ] Chosen the right **suite** (operation family) and **polarity** (`happy`/`failure`).
      **Single-verb → its verb suite; multi-verb → `integration/`** ([DL-0017]).
- [ ] Created `suites/<suite>/<polarity>/<NNNN>-<slug>/` with the next free ordinal.
- [ ] Fixture is the **smallest** artifact that demonstrates **exactly one** concept.
- [ ] `case.toml` has a one-sentence `concept` and a `doc` citation.
- [ ] Each `[[check]]` names the correct `op`, `expect`, and `compare`.
- [ ] Multi-sheet netlist input: set `root =` to the root sheet inside `inputs`.
- [ ] Gerber/golden-dir check: **pinned the layer set** (`args = ["--layers", …,
      "--no-protel-ext"]`) so golden-dir membership is deterministic.
- [ ] Polarity agrees with the directory (`failure/` ⇒ at least one `expect="error"`;
      `happy/` ⇒ none).
- [ ] For rich-output checks: ran `python -m runner --regenerate <case>` **inside the
      `kicad/kicad:10.0.5` Docker image** (LF/platform-canonical goldens, [DL-0016]),
      **inspected the diff**, and committed `golden/<version>/…`.
- [ ] `structured` check: committed the **canonical reduction** (`*.reduced.json` / net
      map), not the raw KiCad report.
- [ ] Ran `python -m runner <case>` → passes.
- [ ] For a failure case: added a **positive control** (`control =`) and confirmed the
      case fails for the **right reason** — `error_contains` present *and* removing the
      defect makes the same check exit 0. A test that can't fail is not evidence.
- [ ] Confirmed the failure is a **graceful rejection, not a `CRASH`** — the runner did
      not report a signal / exit `>128`; if KiCad crashes on your fixture, that is a known
      oracle bug for the ledger, not a passing conformance case ([DL-0013]).
- [ ] For a new normalizer: proved it **load-bearing** (determinism test goes red when
      the normalizer is disabled).
