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

- **Single-concept case (the default, ~90%).** One fixture, one check, one behavior. The
  directory slug names the behavior; the manifest is a few lines.
- **Multi-operation case (the exception).** One fixture, several `[[check]]` entries.
  Used when the *point being documented* is that one real board yields consistent DRC +
  fab outputs. Lives under its input's primary suite.

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
   looking for "how does DRC behave" browses to.
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
| `inputs` | yes* | array\<string\> | Multi-file input. |
| `tags` | no | array\<string\> | Free-form labels for filtering (`["zones", "regression"]`). |
| `min_kicad` | no | string | Skip (counted) below this oracle version, for behavior that doesn't exist in older KiCad. |
| `skip_reason` | no | string | If present, the case is skipped-and-counted with this reason (e.g. an irreducibly nondeterministic fixture). |
| `[[check]]` | **yes** | table array | One or more checks (below). Order is preserved in reports. |

### 4.2 `[[check]]` fields

| Field | Req | Type | Meaning |
|---|---|---|---|
| `op` | **yes** | string | The adapter verb: `parse-sch`, `parse-pcb`, `parse-sym`, `parse-fp`, `upgrade`, `erc`, `drc`, `netlist`, `export-gerbers`, `export-drill`, `export-pos`, `bom`, `version`. |
| `expect` | **yes** | `"ok"` \| `"error"` | Exit-code polarity. `ok` = exit 0; `error` = non-zero. |
| `error_contains` | no | string | (only `expect="error"`) substring that must appear on **stderr**. |
| `error_contains_any` | no | array\<string\> | (only `expect="error"`) at least one substring must appear on stderr (wording escape hatch). |
| `compare` | no | `"exit"` \| `"structured"` \| `"golden-file"` \| `"golden-dir"` | Comparison mode ([`DESIGN.md`](DESIGN.md) §3). Defaults to `"exit"` (polarity only). |
| `golden` | cond | string | (required for `golden-file`/`golden-dir`) artifact name resolved under `golden/<version>/`. A file for `golden-file`, a directory for `golden-dir`. `structured` also uses it to point at the reference JSON/s-expr the reduction is derived from. |
| `format` | no | string | Verb-specific output format override (e.g. a non-default netlist format). |
| `args` | no | array\<string\> | Extra verb-specific flags passed through to the adapter, for cases exercising a specific option. Use sparingly; document why in `concept`. |
| `name` | no | string | Short label when a case has several checks, so reports name each. |

**Rules the runner enforces:**

- Exactly one of `input` / `inputs`.
- `golden` is required iff `compare` is `golden-file` or `golden-dir`.
- `error_contains*` is only valid with `expect = "error"`.
- A `failure` case must contain at least one `expect = "error"` check; a `happy` case
  must contain no `expect = "error"` check. (Polarity and directory must agree — this
  catches a miscategorized case.)
- If any `[[check]]` needs a golden, `golden/<version>/` must exist for the pinned
  version, or the case is reported as **needs-regenerate**, not passed.

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

[[check]]
op             = "parse-sch"
expect         = "error"
error_contains = "Expecting"    # KiCad's parser says e.g. "Expecting ')'"; loose on purpose
```

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

No `golden/` directory — a failure case asserts only that the tool rejects the input and
that stderr mentions the parse position. `error_contains` is a loose substring so a
second adapter with different wording still conforms; use `error_contains_any` if even
that is too tight.

### 5.3 One board fixture driving DRC + gerber-export (multi-operation)

This is the "single input, multiple outputs" shape the owner asked for. It lives under
the input's primary suite (`board-parse`) because the artifact under study is a board;
its checks reach into the `drc` and `gerber` verbs.

`suites/board-parse/happy/0007-board-with-clearance-and-fab-output/case.toml`:

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
compare = "structured"          # semantic reduction of the DRC JSON
golden  = "drc.json"

# 3) Gerber export must match the normalized golden file set (per-layer + job file).
[[check]]
name    = "gerbers"
op      = "export-gerbers"
expect  = "ok"
compare = "golden-dir"
golden  = "gerbers/"            # a directory of RS-274X files, headers normalized
```

On-disk tree for this one case:

```
suites/board-parse/happy/0007-board-with-clearance-and-fab-output/
├── case.toml
├── board.kicad_pcb
└── golden/
    └── 10.0.5/
        ├── canonical.kicad_pcb
        ├── drc.json
        └── gerbers/
            ├── board-F_Cu.gbr
            ├── board-B_Cu.gbr
            ├── board-Edge_Cuts.gbr
            └── board-job.gbrjob
```

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
- [ ] Created `suites/<suite>/<polarity>/<NNNN>-<slug>/` with the next free ordinal.
- [ ] Fixture is the **smallest** artifact that demonstrates **exactly one** concept.
- [ ] `case.toml` has a one-sentence `concept` and a `doc` citation.
- [ ] Each `[[check]]` names the correct `op`, `expect`, and `compare`.
- [ ] Polarity agrees with the directory (`failure/` ⇒ at least one `expect="error"`;
      `happy/` ⇒ none).
- [ ] For rich-output checks: ran `python -m runner --regenerate <case>`, **inspected the
      diff**, and committed `golden/<version>/…`.
- [ ] Ran `python -m runner <case>` → passes.
- [ ] For a failure case: confirmed it fails for the **right reason** (the
      `error_contains` substring is present, and removing the defect makes it pass) — a
      test that can't fail is not evidence.
- [ ] For a new normalizer: proved it **load-bearing** (determinism test goes red when
      the normalizer is disabled).
