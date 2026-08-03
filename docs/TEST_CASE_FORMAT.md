# Test-case format — how to write a case

A case is **one input file, one recorded correct answer**. This document is the contract
for what that looks like on disk. Architecture context is in [`DESIGN.md`](DESIGN.md);
what actually gets compared is in [`VALIDATION.md`](VALIDATION.md); rationale is in
[`DECISIONS.md`](DECISIONS.md).

---

## 1. The shape of a case

```
suites/board-parse/happy/0002-populated-board/
├── case.toml                    # what to run, and what the answer should be
├── board.kicad_pcb              # the input
└── expected/
    └── 10.0.5/                  # keyed by KiCad version
        └── model.json           # the recorded correct answer
```

`case.toml` for that case, in full:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"

[[check]]
op       = "model"
expected = "model.json"
```

Six lines. One input, one output, one check. That is the default and the norm.

**What an expected file is.** The **recorded correct answer** for one check: the output
the reference tool (`kicad-cli`) produced when the case was written, generated once and
then frozen in the repo. Other test frameworks call this a *snapshot*, a *baseline*, or a
*golden file*. It is never hand-written — a hand-written answer records a human's belief
about KiCad, a generated one records KiCad's behaviour, and only the second is a
conformance reference. It lives under `expected/<kicad-version>/` because "correct" is
defined by a specific KiCad release; when the pinned version changes, the answers are
regenerated and the diff is reviewed. See [`VALIDATION.md`](VALIDATION.md) §2.

**What `model.json` is.** One normalized JSON document describing everything the tool
understood about the input: for a board, its counts, holes, footprint placement and net
connectivity; for a schematic, its components and nets. The runner builds it by invoking
several `kicad-cli` exports and merging them — the case author never sees the
intermediate files. Full schema, with real examples, in [`VALIDATION.md`](VALIDATION.md)
§4.

---

## 2. Directory layout

```
suites/<suite>/<happy|failure>/<NNNN-slug>/
├── case.toml                 # required
├── <input file(s)>           # required: the smallest artifact showing the concept
└── expected/                 # only for checks that have a recorded answer
    ├── 10.0.5/
    │   └── <answer files>
    └── 11.0.0/               # added when KiCad 11 ships; the input never changes
        └── <answer files>
```

Three axes:

1. **`<suite>`** — the family the *input* belongs to: `schematic-parse`, `board-parse`,
   `symbol-lib`, `footprint-lib`; plus the two findings families `drc` and `erc`; plus
   `netlist` for cases about the netlist interchange format itself. `gerber/` and
   `drill/` exist but are **empty** — a documented coverage gap
   ([`VALIDATION.md`](VALIDATION.md) §7).
2. **`<happy|failure>`** — polarity. `happy/` = the tool must accept the input and produce
   the recorded answer; `failure/` = the tool must reject it. A listing self-partitions
   into "must accept" and "must reject".
3. **KiCad version** — inside the case, under `expected/<version>/`. Inputs are shared
   across versions; only the answers differ.

There is **no `integration/` suite**. It existed to hold cases where one input drove many
verbs; the `model` verb *is* "one input, many projections", so the case simply lives in
its input's own suite ([DL-0022], superseding [DL-0017]).

The large real-world **`corpus/`** (gitignored) is a separate tree for the scheduled
coverage sweep — not part of `suites/`, never hand-authored.

---

## 3. Naming — the directory listing is the index

```
<NNNN>-<slug>/
```

`<NNNN>` is a 4-digit ordinal, unique within `<suite>/<polarity>/`, zero-padded so
listings sort stably. `<slug>` is a hyphenated phrase describing the one behaviour;
`failure/` slugs name the defect.

```
suites/board-parse/happy/0001-minimal-two-layer-board/
suites/board-parse/happy/0002-populated-board/
suites/board-parse/failure/0001-unterminated-sexpr/
suites/schematic-parse/happy/0001-empty-root-sheet/
suites/schematic-parse/failure/0002-missing-uuid-on-symbol/
suites/drc/happy/0004-clearance-violation-reported/
```

Reading `suites/board-parse/failure/` top to bottom is a checklist of the board parser's
rejection behaviour.

---

## 4. `case.toml` schema

### 4.1 Case fields

| Field | Req | Type | Meaning |
|---|---|---|---|
| `concept` | **yes** | string | One sentence: the single behaviour this case documents. It is the case's headline in reports. |
| `doc` | recommended | string | Format-doc citation, e.g. `"sexpr-pcb"` or `"cli:pcb-drc"`. |
| `input` | yes\* | string | The input file, relative to the case dir. |
| `inputs` | yes\* | array\<string\> | Multi-file input (a multi-sheet schematic, the members of a `.pretty` dir). Exactly one of `input`/`inputs`. |
| `root` | cond | string | Required when `inputs` is a multi-sheet schematic: which entry is the root sheet. |
| `control` | cond | string | Required for `failure/` cases: a defect-free sibling input that must be accepted, proving the case fails for the right reason ([DL-0013]). |
| `min_kicad` | no | string | Skip (counted) below this oracle version. |
| `skip_reason` | no | string | If present the case is skipped and counted, with this reason. |
| `known_divergence` | no | table | Declares a known, tracked bug **in the reference oracle itself** as a strict xfail (§4.3, [DL-0018]). |
| `[[check]]` | **yes** | table array | One or more checks. |

### 4.2 `[[check]]` fields

| Field | Req | Type | Meaning |
|---|---|---|---|
| `op` | **yes** | string | What to run: `model`, `render`, `drc`, `erc`, `netlist`, `pos`, `ipcd356`, `stats`, `parse-pcb`, `parse-sch`, `parse-sym`, `parse-fp`, `version`. |
| `expected` | cond | string | The recorded-answer file, resolved under `expected/<version>/`. Required for every `op` that has a recorded answer; omitted for `parse-*` (exit-only) checks. |
| `outcome` | no | `"ok"` \| `"error"` | Must the tool accept or reject the input. **Defaults to the directory polarity** — `happy/` → `"ok"`, `failure/` → `"error"` — so happy cases never write it. Stating it is allowed; stating something that contradicts the directory is an authoring error the runner rejects. |
| `error_contains` | no | string | (`outcome = "error"` only) substring that must appear on **stderr**. |
| `error_contains_any` | no | array\<string\> | (`outcome = "error"` only) at least one substring must appear on stderr — the wording escape hatch for a second implementation. |
| `format` | no | string | Output-format override for the verb, e.g. `format = "kicadxml"` on a `netlist`/`model` check of a schematic. |
| `args` | no | array\<string\> | Extra flags for the verb, e.g. `args = ["--layers", "F.Cu"]` on a board `render`. Use sparingly; say why in `concept`. |
| `name` | no | string | Short label when a case has more than one check, so reports can name each. |
| `control` | no | string | Per-check override of the case-level `control`. |
| `known_divergence` | no | table | Per-check override of the case-level marker (§4.3). |

**There is no `compare` field.** The `op` decides how the answer is compared: `model`,
`drc`, `erc`, `netlist`, `pos`, `ipcd356` and `stats` compare a normalized JSON document;
`render` compares the SVG byte-exact after `<title>`/`<desc>` normalization; `parse-*`
compares nothing but the exit code. A separate field would only have let a case declare a
comparison its verb cannot perform ([DL-0023]).

**There is no `golden` field.** It is `expected` — same thing, a name that needs no prior
knowledge ([DL-0023]).

**Rules the runner enforces:**

- Exactly one of `input` / `inputs`.
- `expected` is required for every op **except** the exit-only ones — `parse-*`,
  `version`, `export-gerbers`, `export-drill` — and must not be set for those. (The two
  fab-export verbs are exit-only because no comparator for their output exists:
  [`VALIDATION.md`](VALIDATION.md) §7.)
- A `failure/` case has at least one `outcome = "error"` check (usually by default) and a
  `control`; a `happy/` case has none.
- If a check needs an expected file, `expected/<pinned version>/<name>` must exist or the
  case is reported **needs-regenerate**, never passed.
- **A crash is never a pass.** Each invocation is classified `OK` / `REJECT` / `CRASH`
  (termination by signal, or exit code > 128, detected portably — never a hard-coded
  139). `outcome = "error"` is satisfied only by a `REJECT` ([DL-0013],
  [`DESIGN.md`](DESIGN.md) §3a).
- **Every failure case must be falsifiable.** The runner runs the `control` input through
  the same check and requires it to reach `OK`. If it doesn't, the case is reported
  **not-evidence**, never passed.

### 4.3 `known_divergence` — strict xfail ([DL-0018])

Unchanged by this revision. A case (or one check) may declare that the **reference oracle
itself** is known to diverge from the behaviour the case asserts:

| Field | Req | Type | Meaning |
|---|---|---|---|
| `reason` | **yes** | string | One line: what actually happens instead. Cite `docs/DIVERGENCES.md`. |
| `kind` | **yes** | string | The category — currently `"crash"`. |
| `tracking` | no | string | Upstream issue URL/id, or `"TODO: file upstream"`. |

If the actual verdict matches the declared `kind`, the check scores **`XFAIL`** and the
build stays green. If the check instead comes back clean — the oracle got fixed — that is
an **`XPASS`**, which **fails the build** until a human retires the marker and updates the
ledger. A bad verdict that is *not* the declared kind is an ordinary `FAIL`/`CRASH`.

---

## 5. Three fully-worked examples

### 5.1 A board, validated by its model (the default shape)

`suites/board-parse/happy/0002-populated-board/case.toml`:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"

[[check]]
op       = "model"
expected = "model.json"
```

On disk:

```
suites/board-parse/happy/0002-populated-board/
├── case.toml
├── board.kicad_pcb
└── expected/
    └── 10.0.5/
        └── model.json
```

**What the runner does.** Copies `board.kicad_pcb` to a scratch directory (KiCad writes
side-effect files next to a board it merely reads), asks the adapter for the `model`
projection — which internally runs `pcb export stats`, `pcb export pos` and
`pcb export ipcd356` and merges them — and compares the resulting JSON to
`expected/10.0.5/model.json`. Exit must be 0.

**What the recorded answer looks like** (abridged; the verbatim file is in
[`VALIDATION.md`](VALIDATION.md) §4.3):

```json
{
  "counts": { "footprints": {"smd": 1, "tht": 1, "total": 2, "unspecified": 0}, … },
  "drill_holes": [ {"count": 1, "source": "Via", "x_size": "0.4000 mm", …}, … ],
  "has_outline": true,
  "kind": "board",
  "min_track_width": "0.2500 mm",
  "nets": { "GND": ["C1.1", "R1.2"], "NET-1": ["C1.2", "R1.1"] },
  "placement": { "R1": {"x": "20.000000", "y": "-20.000000", "rotation": "90.000000", "side": "top", …}, … }
}
```

Move a pad to another net, rotate a footprint, delete a track — each is a one- or
two-line diff in this file.

**Why there is no separate `parse-pcb` check here.** A model that matches already proves
the file parsed; asserting "it loaded" beside "it loaded into exactly this" is noise.
`parse-*` checks are for `failure/` cases.

### 5.2 A malformed board, rejected (a failure case)

`suites/board-parse/failure/0001-unterminated-sexpr/case.toml`:

```toml
concept = "A board whose (version ...) form is unterminated is rejected with a parse-position error."
doc     = "sexpr-intro"
input   = "board.kicad_pcb"
control = "control.kicad_pcb"     # the same board with the paren restored -> must be accepted

# KNOWN ORACLE DIVERGENCE (DL-0018, docs/DIVERGENCES.md): kicad-cli 10.0.5 prints the
# correct "Expecting" message and then segfaults instead of exiting gracefully. Today's
# CRASH scores XFAIL; if a future KiCad rejects this cleanly the check XPASSes and fails
# the build until the ledger and this marker are updated.
[known_divergence]
kind     = "crash"
reason   = "kicad-cli 10.0.5 segfaults (SIGSEGV) after printing the correct 'Expecting' parse-position message on this truncated board -- see docs/DIVERGENCES.md."
tracking = "TODO: file upstream"

[[check]]
op             = "parse-pcb"
outcome        = "error"
error_contains = "Expecting"      # e.g. "Expecting ')' ... line 3, offset 2."
```

No `expected/` directory: a failure case asserts only that the input is rejected, and
that the rejection mentions the right thing. Two files sit beside the manifest — the
malformed `board.kicad_pcb` and the well-formed `control.kicad_pcb` — because a test that
cannot fail is not evidence: the runner requires the control to be **accepted** through
the same check.

`outcome = "error"` is written out here even though `failure/` already implies it, because
in a failure case the polarity *is* the point.

**Schematic failure cases differ.** KiCad's schematic loader collapses every defect —
unterminated, truncated, unknown token, missing `(version)` — to the same
`Failed to load schematic`, with no position. So a schematic failure case pins that coarse
message and leans entirely on the control to prove *which* defect fired. The PCB loader
does surface a position, so a PCB case may assert the real `Expecting` substring.

### 5.3 A drawing, validated by its render (an opt-in projection)

Use a single projection when that projection **is** the concept — here, the actual drawn
copper, which the model does not capture (it records placement and connectivity, not
geometry).

```toml
concept = "The front-copper layer draws the SMD pad, the track and the via."
doc     = "cli:pcb-export-svg"
input   = "board.kicad_pcb"

[[check]]
name     = "render"
op       = "render"
expected = "render-F_Cu.svg"
args     = ["--layers", "F.Cu"]    # per-case layer selection
```

The runner exports the layer to SVG with determinism pinned at the source
(`--page-size-mode 2`, `--exclude-drawing-sheet`, `--black-and-white`), normalizes the one
nondeterministic line (`<title>`, which carries the filename and wall-clock date), and
compares byte-exact. Zero tolerance — KiCad's SVG path geometry is byte-stable
run-to-run ([`VALIDATION.md`](VALIDATION.md) §6).

In the repo this check rides on the same case as §5.1 rather than duplicating the board
fixture into a second directory:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"

[[check]]
name     = "model"
op       = "model"
expected = "model.json"

[[check]]
name     = "render"
op       = "render"
expected = "render-F_Cu.svg"
args     = ["--layers", "F.Cu"]
```

**The rule about second checks.** Adding a check is right when it documents a genuinely
different concept about the *same* input (here: what the board *means* vs. what it
*draws*). Copying the input into a second case directory to assert a second projection is
wrong — that is fixture duplication, and it is what the `model` verb exists to prevent.

---

## 6. Where a behaviour fires — parse-time vs rule-time

- **Parse/load-time** failures (malformed s-expr, unknown token, bad layer count) →
  `schematic-parse` / `board-parse` `failure/`, `op = "parse-*"`, rejected.
- **Rule-time** findings (a clearance violation, an unconnected net) are **not** failures:
  the tool exits 0 and *reports* them. Those are `drc`/`erc` `happy/` cases whose expected
  file is the finding set. Never pass `--exit-code-violations`.

---

## 7. Contributor checklist

- [ ] Right **suite** (the input's family) and **polarity** (`happy`/`failure`).
- [ ] `suites/<suite>/<polarity>/<NNNN>-<slug>/` with the next free ordinal.
- [ ] The input is the **smallest** artifact that shows **exactly one** concept, and is
      reproducible from the CLI without the GUI ([DL-0011]).
- [ ] One-sentence `concept`, plus a `doc` citation.
- [ ] Default to **one `model` check**. A second check only for a genuinely different
      concept about the same input; a second *case* only for a different input.
- [ ] Generated the expected file with `python -m runner --regenerate <case>` **inside the
      `kicad/kicad:10.0.5` Docker image** (LF / platform-canonical, [DL-0016]), **read the
      diff**, and committed `expected/10.0.5/…`.
- [ ] Ran `python -m runner <case>` → passes.
- [ ] **Broke the input and watched it go red.** Move a pad to another net, rotate a
      footprint, delete a track — confirm the diff points at the change. A test that
      cannot fail is not evidence.
- [ ] Failure case: added the `control` input, confirmed the defect-free variant is
      accepted, and asserted `error_contains`.
- [ ] Failure case: confirmed the rejection is **graceful, not a crash** — a crash is
      never a pass; it is a ledger entry ([DL-0013], `docs/DIVERGENCES.md`).
