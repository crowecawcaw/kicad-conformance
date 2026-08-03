# Test-case format — how to write a case

A case is **one input file and the answers KiCad gave for it**. This document is the
contract for what that looks like on disk. Architecture context is in
[`DESIGN.md`](DESIGN.md); what actually gets compared is in [`VALIDATION.md`](VALIDATION.md);
rationale is in [`DECISIONS.md`](DECISIONS.md).

---

## 1. The shape of a case

```
suites/board-parse/happy/0002-populated-board/
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
That set is called the **standard answers**.

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
| `render.svg` | The drawing of the sheet. |

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
([`VALIDATION.md`](VALIDATION.md) §4.5). Verified:

```
$ kicad-cli sym export --help
Usage: sym export [--help] {svg}
$ kicad-cli fp export --help
Usage: fp export [--help] {svg}
```

**Where the file names come from.** A single answer is a flat file with a name that says
what it holds (`summary.json`, `render-F_Cu.svg`). When KiCad produces a *set* of files
whose size depends on the board, the answer is a **directory** holding KiCad's own
filenames (`gerbers/`, `drill/`, `render/`). A directory answer is compared as a whole:
the same filenames must be present, and every file must match.

### What "an answer" is

An **answer** is output the reference tool (`kicad-cli`) produced when the case was
written, generated once and then frozen in the repo. Other test frameworks call this a
*snapshot*, a *baseline*, or a *golden file*.

It is never hand-written. A hand-written answer records a human's belief about KiCad; a
generated one records KiCad's behaviour, and only the second is a conformance reference.
It lives under `expected/<kicad-version>/` because "correct" is defined by a specific
KiCad release; when the pinned version changes, the answers are regenerated and the diff
is reviewed. See [`VALIDATION.md`](VALIDATION.md) §2.

### Every standard answer is always produced — there is no "skip"

A degenerate input does not produce a missing answer; it produces an empty one. Verified
on the repo's most minimal board (no footprints, no pads, no vias, no holes):

```
$ kicad-cli pcb export drill -o /tmp/d/ 0001-minimal-two-layer-board/board.kicad_pcb
Created file '/tmp/d/board.drl'
$ cat /tmp/d/board.drl
M48
; DRILL file KiCad 10.0.5 date 2026-08-03T04:53:31
; FORMAT={-:-/ absolute / metric / decimal}
…
FMAT,2
METRIC
%
G90
G05
M30
```

A well-formed Excellon file with no tools in it — which is the correct answer for a board
with no holes, and a genuinely useful thing to have recorded. The same board's `F.Cu`
render is a valid 726-byte SVG with an empty drawing group, and its gerber export still
produces the full 21-file set.

So **there is no mechanism for "this answer is legitimately absent", and adding one would
be machinery for a situation that does not occur.** If KiCad ever produces nothing where
the standard answers say it should, the runner reports a **failure**, not a skip — "the
tool produced nothing" is precisely the bug this suite exists to catch. The only escape
hatch remains the case-level `skip_reason`, which skips the whole case and is counted and
printed.

---

## 3. Directory layout

```
suites/<suite>/<happy|failure>/<NNNN-slug>/
├── case.toml                 # required
├── <input file(s)>           # required: the smallest artifact showing the concept
└── expected/                 # happy cases only
    ├── 10.0.5/
    │   └── <the standard answers, plus any extras>
    └── 11.0.0/               # added when KiCad 11 ships; the input never changes
        └── <the same answers, re-recorded>
```

Three axes:

1. **`<suite>`** — the family the *input* belongs to: `schematic-parse`, `board-parse`,
   `symbol-lib`, `footprint-lib`; plus the two findings families `drc` and `erc`; plus
   `netlist` for cases about the netlist interchange format itself. `gerber/` and
   `drill/` remain as suite directories for cases *about* fab output specifically (an
   unusual aperture, an oval hole); **routine gerber and drill coverage is no longer
   their job** — every board case carries it ([DL-0026]).
2. **`<happy|failure>`** — polarity. `happy/` = the tool must accept the input and produce
   the recorded answers; `failure/` = the tool must reject it. A listing self-partitions
   into "must accept" and "must reject".
3. **KiCad version** — inside the case, under `expected/<version>/`. Inputs are shared
   across versions; only the answers differ.

There is **no `integration/` suite** ([DL-0022], superseding [DL-0017]). The large
real-world **`corpus/`** (gitignored) is a separate tree for the scheduled coverage
sweep — not part of `suites/`, never hand-authored.

---

## 4. Naming — the directory listing is the index

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
suites/drc/happy/0004-clearance-violation-reported/
```

Reading `suites/board-parse/failure/` top to bottom is a checklist of the board parser's
rejection behaviour.

> **Name board inputs `board.kicad_pcb` and sheets `sheet.kicad_sch`.** This is not
> cosmetic: gerber output embeds the input's filename in every file, in both the filename
> and the content. Verified — the same board copied to two names:
>
> ```
> board.kicad_pcb   -> board-F_Cu.gtl    %TF.ProjectId,board,626f6172-642e-46b6-9963-61645f706362,rev?*%
> renamed.kicad_pcb -> renamed-F_Cu.gtl  %TF.ProjectId,renamed,72656e61-6d65-4642-9e6b-696361645f70,rev?*%
> ```
>
> (The GUID is the filename's own bytes.) Renaming an input therefore rewrites every
> gerber answer. The runner copies the input to its scratch directory under its original
> name so this stays stable.

---

## 5. `case.toml` — every field there is

| Field | Req | Type | Meaning |
|---|---|---|---|
| `concept` | **yes** | string | One sentence: the single behaviour this case documents. It is the case's headline in reports. |
| `doc` | recommended | string | Format-doc citation, e.g. `"sexpr-pcb"` or `"cli:pcb-drc"`. |
| `input` | yes\* | string | The input file, relative to the case dir. Its suffix chooses the standard answers (§2). |
| `inputs` | yes\* | array\<string\> | Multi-file input (a multi-sheet schematic, the members of a `.pretty` dir). Exactly one of `input`/`inputs`. |
| `root` | cond | string | Required when `inputs` is a multi-sheet schematic: which entry is the root sheet. |
| `extra` | no | array\<string\> | Extra answers to record beyond the standard set (§6). |
| `control` | cond | string | Required for `failure/` cases: a defect-free sibling input that must be accepted, proving the case fails for the right reason ([DL-0013]). |
| `error_contains` | no | string | (`failure/` only) substring that must appear on **stderr** (§7). |
| `error_contains_any` | no | array\<string\> | (`failure/` only) at least one of these substrings must appear on stderr. |
| `min_kicad` | no | string | Skip (counted) below this oracle version. |
| `skip_reason` | no | string | If present the case is skipped and counted, with this reason. |
| `known_divergence` | no | table | Declares a known, tracked bug **in the reference oracle itself** as a strict xfail (§8). |

There is **no `[[check]]` block, no `op`, no `expected`, no `outcome`, no `args`, and no
`compare`.** All five are gone ([DL-0025]):

- **`op`** named a verb from a 13-word vocabulary a contributor had to learn. The input's
  file suffix already says which verbs apply, so the runner infers them.
- **`expected`** named an output file. The standard answers have fixed names, so there is
  nothing to name.
- **`outcome`** said accept-or-reject. The `happy/` vs `failure/` directory already said
  it, twice.
- **`args`** passed flags through to `kicad-cli`. It existed almost entirely to pick a
  render layer, which is now a fixed decision ([`VALIDATION.md`](VALIDATION.md) §6). A
  per-case flag knob is also how a suite quietly acquires cases that are not comparable
  with each other.
- **`compare`** chose a comparison mode. How something is compared follows from what it
  is ([DL-0023]).

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

That last row is the only entry that adds no file, and it is the reason the list is
strings rather than a lookup by filename.

**When to reach for an extra.** When the projection *is* the concept the case documents —
"this board reports zero DRC violations", "this pad's access point is at these
coordinates". Not as a way to spread one board's validation across several cases; the
standard answers already cover the board from four angles.

---

## 7. Failure cases

A `failure/` case asserts that a bad input is **rejected**. There is nothing to project
from a file that will not load, so a failure case records **no answers at all** — no
`expected/` directory. It needs only the message it must produce and a control:

```toml
concept = "A board whose (version ...) form is unterminated is rejected with a parse-position error."
doc     = "sexpr-intro"
input   = "board.kicad_pcb"
control = "control.kicad_pcb"     # the same board with the paren restored -> must be accepted
error_contains = "Expecting"      # e.g. "Expecting ')' ... line 3, offset 2."
```

Rules the runner enforces:

- A `failure/` case must have a `control`; a `happy/` case must not.
- **A crash is never a pass.** Each invocation is classified `OK` / `REJECT` / `CRASH`
  (termination by signal, or exit code > 128, detected portably — never a hard-coded
  139). A `failure/` case is satisfied only by a `REJECT` ([DL-0013],
  [`DESIGN.md`](DESIGN.md) §3a).
- **Every failure case must be falsifiable.** The runner loads the `control` input the
  same way and requires it to reach `OK`. If it doesn't, the case is reported
  **not-evidence**, never passed.
- A `happy/` case whose `expected/<pinned version>/` is missing or incomplete is reported
  **needs-regenerate**, never passed.

**Schematic failure cases differ from board ones.** KiCad's schematic loader collapses
every defect — unterminated, truncated, unknown token, missing `(version)` — to the same
`Failed to load schematic`, with no position. So a schematic failure case pins that coarse
message and leans entirely on the control to prove *which* defect fired. The PCB loader
does surface a position, so a PCB case may assert the real `Expecting` substring.

---

## 8. `known_divergence` — strict xfail ([DL-0018])

Unchanged. A case may declare that the **reference oracle itself** is known to diverge
from the behaviour the case asserts:

| Field | Req | Type | Meaning |
|---|---|---|---|
| `reason` | **yes** | string | One line: what actually happens instead. Cite `docs/DIVERGENCES.md`. |
| `kind` | **yes** | string | The category — currently `"crash"`. |
| `tracking` | no | string | Upstream issue URL/id, or `"TODO: file upstream"`. |

If the actual verdict matches the declared `kind`, the case scores **`XFAIL`** and the
build stays green. If it instead comes back clean — the oracle got fixed — that is an
**`XPASS`**, which **fails the build** until a human retires the marker and updates the
ledger. A bad verdict that is *not* the declared kind is an ordinary `FAIL`/`CRASH`.

---

## 9. Three fully-worked examples

### 9.1 A board (the default shape)

`suites/board-parse/happy/0002-populated-board/case.toml`, in full:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

On disk:

```
suites/board-parse/happy/0002-populated-board/
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
name (KiCad writes side-effect files next to a board it merely reads, and gerber content
embeds the filename — §4), then runs six `kicad-cli` exports and compares all four
answers:

```
pcb export stats   --format json                              ┐
pcb export pos     --format csv --side both --units mm        ├─ merged into summary.json
pcb export ipcd356                                            ┘
pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet --black-and-white
pcb export gerbers                                            (no --layers: KiCad's own set)
pcb export drill
```

**What `summary.json` looks like** (abridged; the verbatim file is in
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
two-line diff in this file, and the gerbers move with it.

**Why the gerber file list is only seven files here** and twenty-one for the minimal board
next door: KiCad plots the layer set stored *in the board*, and this board carries a
`(pcbplotparams (layerselection …))` block while the minimal one does not. That is the
correct behaviour to record — it is what the fab receives
([`VALIDATION.md`](VALIDATION.md) §7, [DL-0026]).

### 9.2 A malformed board, rejected (a failure case)

`suites/board-parse/failure/0001-unterminated-sexpr/case.toml`:

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

### 9.3 A board plus a rule check (a case using an extra)

`suites/drc/happy/0001-clean-board/case.toml`:

```toml
concept = "A minimal two-layer board with a valid Edge.Cuts outline reports zero DRC violations."
doc     = "cli:pcb-drc"
input   = "board.kicad_pcb"
extra   = ["drc"]
```

On disk:

```
suites/drc/happy/0001-clean-board/
├── case.toml
├── board.kicad_pcb
└── expected/
    └── 10.0.5/
        ├── summary.json         ┐
        ├── render-F_Cu.svg      ├─ the standard board answers, free
        ├── gerbers/             │
        ├── drill/               ┘
        └── drc.json             <- what `extra = ["drc"]` added
```

The case is *about* the DRC result — that is what `concept` says and what a reader is
meant to take from it. It gets the standard board answers anyway, because they cost one
line of manifest each and catch regressions the DRC report never would. An empty
`violations`/`unconnected`/`parity` set in `drc.json` is the point of the case.

---

## 10. Where a behaviour fires — parse-time vs rule-time

- **Parse/load-time** failures (malformed s-expr, unknown token, bad layer count) →
  `schematic-parse` / `board-parse` `failure/`, rejected.
- **Rule-time** findings (a clearance violation, an unconnected net) are **not** failures:
  the tool exits 0 and *reports* them. Those are `drc`/`erc` `happy/` cases with
  `extra = ["drc"]` or `["erc"]`, whose answer is the finding set. Never pass
  `--exit-code-violations`.

---

## 11. Contributor checklist

- [ ] Right **suite** (the input's family) and **polarity** (`happy`/`failure`).
- [ ] `suites/<suite>/<polarity>/<NNNN>-<slug>/` with the next free ordinal.
- [ ] The input is the **smallest** artifact that shows **exactly one** concept, is named
      `board.kicad_pcb` / `sheet.kicad_sch` (§4), and is reproducible from the CLI without
      the GUI ([DL-0011]).
- [ ] One-sentence `concept`, plus a `doc` citation. That plus `input` is usually the
      whole manifest.
- [ ] Added an `extra` **only** if the case is genuinely about that projection.
- [ ] Generated the answers with `python -m runner --regenerate <case>` **inside the
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
