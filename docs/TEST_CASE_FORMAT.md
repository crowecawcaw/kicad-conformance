# Test-case format — how to write a case

A case is **one input file and the answers KiCad gave for it**. This document is the
contract for what that looks like on disk. Architecture and what actually gets compared
are in [`DESIGN.md`](DESIGN.md); rationale is in [`DECISIONS.md`](DECISIONS.md).

---

## 1. The shape of a case

```
suites/board-parse/populated-board/
├── case.toml                    # one sentence and a filename
├── board.kicad_pcb              # the input
└── expected/
    └── 10.0.5/                  # the answers, keyed by KiCad version
        ├── summary.json
        ├── render-F_Cu.svg
        ├── gerbers/
        └── drill/
```

`case.toml` for that case, **in full**:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

Three lines. There is no list of things to check, no verb to name, no output filename to
declare. **You say what the file is; the runner already knows what to ask KiCad for.**

To add a case you drop in a board, write one sentence, run `--regenerate`, and read the
diff. That is the whole contributor workflow, and learning it takes no vocabulary
([DL-0025]).

---

## 2. The standard answers

**The runner picks what to record from the input's file type.** Every case of a given
type records the same set of answers, so cases are comparable and nobody has to choose.

### Board — `.kicad_pcb`

| File in `expected/<version>/` | What it is |
|---|---|
| `summary.json` | **One JSON document listing everything KiCad understood about the board** — how many footprints, pads and vias; the drilled holes; where each footprint sits and which way up; which pads are on which nets. |
| `render-F_Cu.svg` | **The drawing of the front copper layer**, as an SVG. |
| `gerbers/` | **The gerber files a fab would receive** — every layer KiCad plots for this board, recorded byte-for-byte. |
| `drill/` | **The drill file a fab would receive** — KiCad's Excellon output, byte-for-byte. |

### Schematic — `.kicad_sch`

| File | What it is |
|---|---|
| `summary.json` | The same kind of document, for a schematic: its components, their pins, and its nets. |
| `render.svg` | The drawing of the sheet (the root page — see §2's multi-sheet note below). |

### Symbol library — `.kicad_sym`

| File | What it is |
|---|---|
| `render/` | One SVG per symbol per unit, under KiCad's own names (`T2_unit1.svg`). |

### Footprint library — `.pretty` directory (or a lone `.kicad_mod`)

| File | What it is |
|---|---|
| `render/` | One SVG per footprint, under KiCad's own names (`PadOnly.svg`). |

**Libraries get drawings only.** `kicad-cli` 10.0.5 offers exactly one export for a
symbol or footprint library — `export svg`. There is no pin table and no pad table to
build a `summary.json` from, so a library case records its drawings and nothing else
(`DESIGN.md` §3b.4).

**Where the file names come from.** A single answer is a flat file with a name that says
what it holds (`summary.json`, `render-F_Cu.svg`). When KiCad produces a *set* of files
whose size depends on the board, the answer is a **directory** holding KiCad's own
filenames (`gerbers/`, `drill/`, `render/`). A directory answer is compared as a whole:
the same filenames must be present, and every file must match.

**Multi-sheet schematics.** A schematic case may declare `inputs = [...]` (the root sheet
plus every sub-sheet) and `root = "…"` naming which entry is the root. The runner passes
every declared input to the adapter, which copies all of them into one scratch directory
under their own filenames before running `sch export netlist` against the root — this is
what lets `summary.json` cover components and nets from every sheet, not just the root
(`DESIGN.md` §9 walks through the proof case, `suites/schematic-parse/hierarchical-sheet/`).
The recorded `render.svg` is still only the root page — `kicad-cli` writes one SVG per
page for a hierarchical sheet, and only the root page's file is part of the standard
answer today (`DESIGN.md` §3c's open item).

### What "an answer" is

An **answer** is output the reference tool (`kicad-cli`) produced when the case was
written, generated once and then frozen in the repo. Other test frameworks call this a
*snapshot*, a *baseline*, or a *golden file*.

It is never hand-written. A hand-written answer records a human's belief about KiCad; a
generated one records KiCad's behaviour, and only the second is a conformance reference.
It lives under `expected/<kicad-version>/` because "correct" is defined by a specific
KiCad release; when the pinned version changes, the answers are regenerated and the diff
is reviewed. See [`DESIGN.md`](DESIGN.md) §5.

### Every standard answer is always produced — there is no "skip"

A degenerate input does not produce a missing answer; it produces an empty one. Verified
on the repo's most minimal board (no footprints, no pads, no vias, no holes): its drill
export is a well-formed Excellon file with no tools in it, its `F.Cu` render is a valid
726-byte SVG with an empty drawing group, and its gerber export still produces the full
21-file set.

So **there is no mechanism for "this answer is legitimately absent", and adding one would
be machinery for a situation that does not occur.** If KiCad ever produces nothing where
the standard answers say it should, the runner reports a **failure**, not a skip. The
only escape hatch remains the case-level `skip_reason`, which skips the whole case and is
counted and printed.

---

## 3. Directory layout

```
suites/<suite>/<slug>/
├── case.toml                 # required
├── <input file(s)>           # required: the smallest artifact showing the concept
└── expected/                 # happy cases only
    ├── 10.0.5/
    │   └── <the standard answers, plus any extras>
    └── 11.0.0/               # added when KiCad 11 ships; the input never changes
        └── <the same answers, re-recorded>
```

Two axes:

1. **`<suite>`** — the family the *input* belongs to: `schematic-parse`, `board-parse`,
   plus the findings suite `drc`. Suites for `erc`, `netlist`-specific cases,
   `symbol-lib`/`footprint-lib`, and fab-specific `gerber`/`drill` cases don't exist yet
   in the tree — they are created (`mkdir`) when their first case is authored, per
   [`ROADMAP.md`](ROADMAP.md) M1–M4. An empty suite directory holding a placeholder file
   is not a down payment on that work; it is a false coverage claim on every `ls`.
2. **KiCad version** — inside the case, under `expected/<version>/`. Inputs are shared
   across versions; only the answers differ.

**Polarity is not a directory level.** A case's slug lives directly under its suite
(`suites/board-parse/populated-board/`, not
`suites/board-parse/happy/0002-populated-board/`) — whether it is a happy case or a
rejection case follows from whether `case.toml` sets `control` (§7), not from a
`happy/`/`failure/` path segment. By convention (not enforced by the runner) a rejection
case's slug is prefixed `rejects-`, so a suite listing still self-partitions:

```
$ ls suites/board-parse/
minimal-two-layer-board  populated-board  rejects-unterminated-sexpr
```

reads as "two things it accepts, one thing it rejects" in one level, instead of two.

There is **no `integration/` suite** ([DL-0022]). A large real-world **`corpus/`** for a
scheduled coverage sweep is a separate, later idea ([DL-0009]) — it does not exist in the
tree yet, and costs nothing to `mkdir` when that work starts.

---

## 4. Naming — the directory listing is the index

```
<slug>/
```

`<slug>` is a hyphenated phrase describing the one behaviour a rejection-case slug names
the defect, prefixed `rejects-` by convention:

```
suites/board-parse/minimal-two-layer-board/
suites/board-parse/populated-board/
suites/board-parse/rejects-unterminated-sexpr/
suites/schematic-parse/empty-root-sheet/
```

Reading `suites/board-parse/` top to bottom is a checklist of the board parser's
behaviour: what it accepts, and what it rejects.

> **Name board inputs `board.kicad_pcb` and sheets `sheet.kicad_sch`** (a multi-sheet
> case's root, specifically — sub-sheets are named for what they are, e.g.
> `sub.kicad_sch`). This is not cosmetic: gerber output embeds the input's filename in
> every file, in both the filename and the content. Verified — the same board copied to
> two names:
>
> ```
> board.kicad_pcb   -> board-F_Cu.gtl    %TF.ProjectId,board,626f6172-642e-46b6-9963-61645f706362,rev?*%
> renamed.kicad_pcb -> renamed-F_Cu.gtl  %TF.ProjectId,renamed,72656e61-6d65-4642-9e6b-696361645f70,rev?*%
> ```
>
> (The GUID is the filename's own bytes.) Renaming an input therefore rewrites every
> gerber answer. The runner copies the input to its scratch directory under its original
> name so this stays stable.

**Recognized sibling files, discovered by same stem, never declared in `case.toml`
([DL-0034]/[DL-0038]).** A board case may drop one or more of these next to its
`board.kicad_pcb`:

| Sibling | Enables | Read by |
|---|---|---|
| `board.kicad_dru` | a custom DRC rule file | `drc`, `refill-zones` |
| `board.kicad_pro` | project settings, incl. per-check severity overrides | `drc`, `refill-zones`, `parity` |
| `board.kicad_sch` | a schematic to check board/schematic parity against | `parity` |

`pcb drc` has no `--rules`/`--project`/schematic-path flag in 10.0.5 — same-stem,
same-directory is the *only* way any of these three reaches it. The adapter copies
whichever of the three are present alongside the board into scratch automatically; a
case that wants one just ships it, the same way it ships the board itself. Copying is
unconditional (not gated on which `extra` a case sets) and was verified harmless for
every board answer that doesn't consult a given sibling.

---

## 5. `case.toml` — every field there is

| Field | Req | Type | Meaning |
|---|---|---|---|
| `concept` | **yes** | string | One sentence: the single behaviour this case documents. It is the case's headline in reports. |
| `doc` | recommended | string | Format-doc citation, e.g. `"sexpr-pcb"` or `"cli:pcb-drc"`. |
| `input` | yes\* | string | The input file, relative to the case dir. Its suffix chooses the standard answers (§2). |
| `inputs` | yes\* | array\<string\> | Multi-file input (a multi-sheet schematic: root + subsheets). Exactly one of `input`/`inputs`. |
| `root` | cond | string | Required when `inputs` is a multi-sheet schematic: which entry is the root sheet. |
| `extra` | no | array\<string\> | Extra answers to record beyond the standard set (§6). |
| `control` | cond | string | **Setting this is what makes a case a rejection case** (§7): a defect-free sibling input that must be accepted, proving the case fails for the right reason ([DL-0013]). A case with no `control` is a happy case. |
| `error_contains` | no | string | (rejection cases only) substring that must appear on **stderr** (§7). |
| `error_contains_any` | no | array\<string\> | (rejection cases only) at least one of these substrings must appear on stderr. |
| `skip_reason` | no | string | If present the case is skipped and counted, with this reason. |
| `known_divergence` | no | table | Declares a known, tracked bug **in the reference oracle itself** as a strict xfail (§8). |

There is **no `[[check]]` block, no `op`, no `expected`, no `outcome`, no `args`, no
`compare`, and no `min_kicad`.** All are gone ([DL-0025]):

- **`op`** named a verb from a vocabulary a contributor had to learn. The input's file
  suffix already says which verbs apply, so the runner infers them.
- **`expected`** named an output file. The standard answers have fixed names.
- **`outcome`** said accept-or-reject; that is now exactly what setting `control`
  expresses, so a separate field would be two spellings of one fact.
- **`args`** passed flags through to `kicad-cli`. It existed almost entirely to pick a
  render layer, which is now a fixed decision (`DESIGN.md` §2b).
- **`compare`** chose a comparison mode. How something is compared follows from what it
  is ([DL-0023]).
- **`min_kicad`** was parsed and stored but never consulted by anything — a doc lie, not
  a feature. If a version floor is ever needed, it will be implemented and documented
  alongside actual behavior, not added back speculatively.

`error_contains` was **kept** rather than shortened. It is already plain English that
needs no prior knowledge, and renaming a field that reads correctly is churn.

---

## 6. Extras — the one knob

Some answers are not projections of the file; they are separate questions about it. A DRC
run is a *rule check*, not a description of the board, so it is not in the standard set.
Cases that want one list it:

```toml
extra = ["drc"]
```

Each name adds exactly one more answer, and the name is the filename:

| `extra` name | Adds | Answer file |
|---|---|---|
| `drc` | design-rule-check findings for a board | `drc.json` |
| `erc` | electrical-rule-check findings for a schematic | `erc.json` |
| `pos` | the placement (pick-and-place) file on its own | `pos.json` |
| `stats` | KiCad's board-statistics report on its own | `stats.json` |
| `ipcd356` | the IPC-D-356 netlist, including test-point positions | `ipcd356.json` |
| `netlist` | the netlist interchange file on its own | `netlist.json` |
| `summary-kicadxml` | rebuilds `summary.json` from KiCad's **XML** netlist instead of its s-expression netlist, and compares it to the **same `summary.json`** — proof the summary measures meaning, not one file format | *(none — reuses `summary.json`)* |
| `refill-zones` | a DRC run with `--refill-zones` first — the only way to exercise `ZONE_FILLER::Fill`, since every committed zone fixture ships a pre-computed fill ([DL-0036]) | `refill-zones.json` |
| `parity` | a DRC run with `--schematic-parity` against a same-stem `.kicad_sch` sibling (§4 below) — board/schematic parity findings ([DL-0038]) | `parity.json` |
| `pdf` | a PDF export (board: fixed `F.Cu` layer, single file; schematic: the whole hierarchy, one multi-page file) ([DL-0037]) | `pdf.pdf` |
| `dxf` | a DXF export of the board (fixed `F.Cu` layer) — board-only, kicad-cli has no `sch export dxf` ([DL-0037]) | `dxf.dxf` |

That `summary-kicadxml` row is the only entry that adds no file, and it is the reason the
list is strings rather than a lookup by filename.

**`refill-zones`/`parity` inherit a nondeterminism risk from plain `drc`, not one they
introduce.** Both extras record the same three-part shape `drc` does
(`violations`/`unconnected_items`/`schematic_parity`), and KiCad's own ratsnest/
unconnected-items reporting has been observed (verified, [DL-0038]) to occasionally
report a *different pairing* of a board's mutually-unconnected same-net endpoints across
otherwise-identical runs, when three or more such endpoints exist with no unambiguous
closest pair. Pick (or verify) a fixture with an unambiguous DRC result — ideally zero or
few unconnected items — before committing a `refill-zones`/`parity` case, and run
`--determinism-check` on it more than once before trusting a green result.

**When to reach for an extra.** When the projection *is* the concept the case documents —
"this board reports zero DRC violations", "this pad's access point is at these
coordinates." Not as a way to spread one board's validation across several cases; the
standard answers already cover the board from four angles.

---

## 7. Rejection cases

A case that sets `control` asserts that a bad input is **rejected**. There is nothing to
project from a file that will not load, so it records **no answers at all** — no
`expected/` directory. It needs only the message it must produce and the control:

```toml
concept = "A board whose (version ...) form is unterminated is rejected with a parse-position error."
doc     = "sexpr-intro"
input   = "board.kicad_pcb"
control = "control.kicad_pcb"     # the same board with the paren restored -> must be accepted
error_contains = "Expecting"      # e.g. "Expecting ')' ... line 3, offset 2."
```

Rules the runner enforces:

- Setting `control` is what makes a case a rejection case; a happy case must not set
  `error_contains`/`error_contains_any` (they would have nothing to attach to).
- **A crash is never a pass.** Each invocation is classified `OK` / `REJECT` / `CRASH`
  (termination by signal, or exit code > 128, detected portably — never a hard-coded
  139). A rejection case is satisfied only by a `REJECT` ([DL-0013],
  [`DESIGN.md`](DESIGN.md) §3a).
- **Every rejection case must be falsifiable.** The runner loads the `control` input the
  same way and requires it to reach `OK`. If it doesn't, the case is reported
  **not-evidence**, never passed.
- A happy case whose `expected/<pinned version>/` is missing or incomplete is reported
  **needs-regenerate**, never passed.

**Schematic rejection cases differ from board ones.** KiCad's schematic loader collapses
every defect — unterminated, truncated, unknown token, missing `(version)` — to the same
`Failed to load schematic`, with no position. So a schematic rejection case pins that
coarse message and leans entirely on the control to prove *which* defect fired. The PCB
loader does surface a position, so a PCB case may assert the real `Expecting` substring.

---

## 8. `known_divergence` — strict xfail ([DL-0018])

A case may declare that the **reference oracle itself** is known to diverge from the
behaviour the case asserts:

| Field | Req | Type | Meaning |
|---|---|---|---|
| `reason` | **yes** | string | One line: what actually happens instead. Cite `docs/DIVERGENCES.md`. |
| `kind` | **yes** | string | The category — currently `"crash"`. |
| `tracking` | no | string | Upstream issue URL/id, or `"TODO: file upstream"`. |
| `probe` | no | string | A verb name that overrides the derived loader verb for THIS case only ([DL-0029]). A narrow escape hatch, not a general per-case verb knob — its only current use is `rejects-unterminated-sexpr` pinning itself to `"parse-pcb-upgrade"` so it keeps exercising a crash that moved off the default `parse-pcb` probe's path. |

If the actual verdict matches the declared `kind`, the case scores **`XFAIL`** and the
build stays green. If it instead comes back clean — the oracle got fixed — that is an
**`XPASS`**, which **fails the build** until a human retires the marker and updates the
ledger. A bad verdict that is *not* the declared kind is an ordinary `FAIL`/`CRASH`.

---

## 9. Two worked examples

### 9.1 A board (the default shape)

`suites/board-parse/populated-board/case.toml`, in full:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

On disk:

```
suites/board-parse/populated-board/
├── case.toml
├── board.kicad_pcb
└── expected/
    └── 10.0.5/
        ├── summary.json
        ├── render-F_Cu.svg
        ├── gerbers/
        │   ├── board-B_Courtyard.gbr
        │   ├── board-B_Cu.gbl
        │   ├── board-Edge_Cuts.gm1
        │   ├── board-F_Courtyard.gbr
        │   ├── board-F_Cu.gtl
        │   ├── board-Margin.gbr
        │   └── board-job.gbrjob
        └── drill/
            └── board.drl
```

**What the runner does.** Copies `board.kicad_pcb` to a scratch directory under the same
name, then runs six `kicad-cli` exports and compares all four answers:

```
pcb export stats   --format json                              ┐
pcb export pos     --format csv --side both --units mm        ├─ merged into summary.json
pcb export ipcd356                                            ┘
pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet --black-and-white
pcb export gerbers                                            (no --layers: KiCad's own set)
pcb export drill
```

The real, committed `summary.json` is the answer file itself
(`suites/board-parse/populated-board/expected/10.0.5/summary.json`); its schema is in
[`DESIGN.md`](DESIGN.md) §3b.1. Move a pad to another net, rotate a footprint, delete a
track — each is a one- or two-line diff in that file, and the gerbers move with it.

**Why the gerber file list is only seven files here** and twenty-one for the minimal
board next door: KiCad plots the layer set stored *in the board*, and this board carries
a `(pcbplotparams (layerselection …))` block while the minimal one does not
([`DESIGN.md`](DESIGN.md) §2b/§3d, [DL-0026]).

### 9.2 A malformed board, rejected

`suites/board-parse/rejects-unterminated-sexpr/case.toml`:

```toml
concept = "A board whose (version ...) form is unterminated is rejected with a parse-position error."
doc     = "sexpr-intro"
input   = "board.kicad_pcb"
control = "control.kicad_pcb"     # the same board with the paren restored -> must be accepted
error_contains = "Expecting"      # e.g. "Expecting ')' ... line 3, offset 2."

# KNOWN ORACLE DIVERGENCE (DL-0018, docs/DIVERGENCES.md): kicad-cli 10.0.5 prints the
# correct "Expecting" message and then segfaults instead of exiting gracefully. Today's
# CRASH scores XFAIL; if a future KiCad rejects this cleanly the case XPASSes and fails
# the build until the ledger and this marker are updated.
[known_divergence]
kind     = "crash"
reason   = "kicad-cli 10.0.5 segfaults (SIGSEGV) after printing the correct 'Expecting' parse-position message on this truncated board -- see docs/DIVERGENCES.md."
tracking = "TODO: file upstream"
```

On disk: `case.toml`, the malformed `board.kicad_pcb`, the well-formed
`control.kicad_pcb`. **No `expected/` directory.** The control is there because a test
that cannot fail is not evidence: the runner requires it to be **accepted**.

(TOML note: `[known_divergence]` is a table header, so it must come *after* the top-level
keys. Everything above it belongs to the case.)

---

## 10. Where a behaviour fires — parse-time vs rule-time

- **Parse/load-time** failures (malformed s-expr, unknown token, bad layer count) →
  `schematic-parse`/`board-parse`, a rejection case (`control` set).
- **Rule-time** findings (a clearance violation, an unconnected net) are **not**
  failures: the tool exits 0 and *reports* them. Those are `drc`/`erc` happy cases with
  `extra = ["drc"]` or `["erc"]`, whose answer is the finding set. Never pass
  `--exit-code-violations`.

---

## 11. `perturb/` — committing the falsifiability check ([DL-0030])

Passing once is not the same as being falsifiable. [`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md)
turns *"broke the input and watched it go red"* (formerly this section's closing checklist
item, by hand, with no record) into a committed fixture and a runner mode that re-checks
it forever: `--verify-assertions`.

```
suites/board-parse/populated-board/
├── case.toml
├── board.kicad_pcb
├── expected/10.0.5/…
└── perturb/
    ├── pad-to-other-net/
    │   └── board.kicad_pcb        # same board, C1 pad 2 moved to another net
    └── silk-text-recased/
        └── board.kicad_pcb        # same board, one silkscreen property recased
```

**`case.toml` gains no key.** A perturbation is a directory, `perturb/<slug>/`, holding a
copy of the case's input(s) with something changed:

- **It is an overlay, by filename.** Any file in `perturb/<slug>/` whose name matches a
  declared `input`/`inputs` entry replaces that input for this perturbation's run; every
  other declared input is used unchanged (this is what lets a multi-sheet schematic case
  perturb one sheet without copying every sheet into every slug).
- **A file whose name matches none of the case's inputs is an error**
  (`INVALID-PERTURBATION`), not a silent no-op — a misnamed file must be loud.
- **It must still load.** A happy case's perturbed input must be *accepted* the same way
  the original is; a perturbation that merely breaks the file scores
  `INVALID-PERTURBATION`, not `ASSERTED` — that would be a rejection case wearing a
  disguise, proving nothing about what the recorded answers actually check.
- **`<slug>` is the only documentation.** A hyphenated phrase naming what changed
  (`pad-to-other-net`, `via-moved-1mm`) — no description field. `--verify-assertions`
  prints `diff <input> perturb/<slug>/<input>` on every non-`ASSERTED` outcome, which is
  the complete statement of what the perturbation does.
- **A rejection case (sets `control`) must not have a `perturb/` directory.** It records
  no answers, so "the answer changed" is undefined; its `control` is already this check
  (§7, [DL-0013]).
- **Keep the input's filename.** A perturbation is a fixture like any other ([DL-0011],
  [DL-0016]) and, per §4's gerber-filename rule ([DL-0026]), must be named exactly like
  the input it replaces.

**Running it:**

```bash
scripts/run.sh --verify-assertions <case-dir>     # one case
scripts/run.sh --verify-assertions suites/        # everything with a perturb/
```

Per perturbation, the runner substitutes the overlay, regenerates the case's answers
against it, and compares each to the case's **committed** `expected/<version>/` — never
`--regenerate`s, never writes an expected file. It reports one of four statuses:

| Status | Meaning |
|---|---|
| `ASSERTED` | at least one committed answer differs — this perturbation proves the case would notice |
| `INERT` | every committed answer is byte-identical — the case asserts **nothing** about this change; adjust the perturbation, don't delete the finding |
| `INVALID-PERTURBATION` | the perturbed input wasn't accepted, an overlay filename matched no input, or a `perturb/` sat on a rejection case |
| `CRASH` | the oracle was killed by a signal on the perturbed input |

`ASSERTED` is labelled `[semantic]` or `[byte-only]`: `gerbers/`/`drill/` are a
KiCad-self-consistency signal only ([DL-0015]/[DL-0026]), so a perturbation that moves
*only* those doesn't assert anything a second implementation is judged on. `INERT`,
`INVALID-PERTURBATION` and `CRASH` all fail the build — a perturbation is worse than none
if it's wrong. A happy case with **no** `perturb/` at all is `UNASSERTED-CASE`: counted
and printed, not failed (the corpus is backfilled gradually, not all at once), but CI
gates on that count never going up.

This is Tier 1 of [DL-0030]. Tier 2 — attributing *which lines* an assertion credits,
against the gcov-instrumented image — is [DL-0031] and remains design-only.

## 12. Contributor checklist

- [ ] Right **suite** (the input's family).
- [ ] `suites/<suite>/<slug>/` — a rejection case's slug conventionally starts
      `rejects-`.
- [ ] The input is the **smallest** artifact that shows **exactly one** concept, is named
      `board.kicad_pcb` / `sheet.kicad_sch` (§4), and is reproducible from the CLI without
      the GUI ([DL-0011]).
- [ ] One-sentence `concept`, plus a `doc` citation. That plus `input` is usually the
      whole manifest.
- [ ] Added an `extra` **only** if the case is genuinely about that projection.
- [ ] Generated the answers with `scripts/run.sh --regenerate <case>` (runs **inside the
      `kicad/kicad:10.0.5` Docker image**, LF / platform-canonical, [DL-0016]), **read the
      diff**, and committed `expected/10.0.5/…`.
- [ ] Ran `scripts/run.sh <case>` → passes.
- [ ] **Committed the perturbation that proves it** (§11, [DL-0030]): `perturb/<slug>/`
      holding a copy of the input with one thing changed (move a pad to another net,
      rotate a footprint, delete a track), named exactly like the input. Ran
      `scripts/run.sh --verify-assertions <case>` and confirmed `ASSERTED`, naming the
      answer that moved. A test that cannot fail is not evidence.
- [ ] Rejection case: set `control`, confirmed the defect-free variant is accepted, and
      asserted `error_contains`.
- [ ] Rejection case: confirmed the rejection is **graceful, not a crash** — a crash is
      never a pass; it is a ledger entry ([DL-0013], `docs/DIVERGENCES.md`).
