# Validation — what a test case actually compares

How kicad-conformance decides "did the tool get it **right**". Companion docs:
[`DESIGN.md`](DESIGN.md) (architecture), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md)
(how to write a case), [`DECISIONS.md`](DECISIONS.md) (numbered rationale —
[DL-0025]–[DL-0028] ratify this document).

Every empirical claim below was produced against **`kicad-cli` 10.0.5** in the
`kicad/kicad:10.0.5` Docker image, on the two committed board fixtures and the committed
schematic fixture. The exact commands are shown inline; all of them were run as:

```
docker run --rm -v "<dir>:/work" -w /work -e LC_ALL=C.UTF-8 -e TZ=UTC \
    kicad/kicad:10.0.5 bash -lc '<command>'
```

---

## 1. The idea in one paragraph

A test case is **one input file**, and the answers KiCad gave for it. The runner feeds the
input to the tool and records a fixed set of outputs chosen by the input's file type — the
**standard answers**. For a board that is four things: a **summary** (one JSON document
describing everything the tool understood), a **render** (the front-copper drawing, as
SVG), the **gerbers**, and the **drill file**. For a schematic it is two: a summary and a
render. Each is compared against the recorded copy in `expected/<version>/`. A **failure
case** records nothing and only checks that a bad file is rejected.

The case author picks none of this. They name the input file; the file type does the rest
([DL-0025]).

## 2. What an "answer" is

An **answer** (an expected file) is the recorded correct output for one thing the runner
records. It is produced once, by running `kicad-cli` on the case's input, and then frozen
in the repo under `expected/<kicad-version>/`. Other test frameworks call the same thing a
*snapshot*, a *baseline*, or a *golden file*; this repo calls it what it is.

Three properties follow from "recorded, not written":

- **It is never hand-authored.** A hand-written answer encodes a human's belief about
  KiCad. A generated one encodes KiCad's behaviour. Only the second is a conformance
  reference ([DL-0004]).
- **It is keyed by KiCad version**, not by tool. `expected/10.0.5/summary.json` means "the
  correct answer as defined by KiCad 10.0.5". A second implementation is compared against
  that same file — there is no per-implementation answer.
- **It is reviewed like source.** Regenerating is a deliberate act (`--regenerate` inside
  the pinned Docker image, [DL-0016]); the contributor reads the diff before committing
  it. A changed answer is a changed claim about KiCad.

## 3. The four kinds of comparison

| Kind | What it compares | Where it is used |
|---|---|---|
| **exit** | did the tool accept (exit 0) or gracefully reject (bounded non-zero) the file | every `failure/` case |
| **summary** | one normalized JSON document describing everything the tool understood | every `happy/` board & schematic case; also the JSON extras |
| **render** | drawn vector geometry — a layer, a sheet, a symbol, a footprint | every `happy/` board, schematic and library case |
| **bytes** | KiCad's fabrication output, file-for-file and byte-for-byte after a named normalizer | `gerbers/` and `drill/` on every board case |

The fourth kind was deleted in the previous revision ([DL-0024]) and is **restored, in a
narrower form**, by [DL-0026]: it applies to fab output only, and it is explicitly a
**KiCad-version-regression signal**, not a cross-implementation conformance bar (§7.4).
An earlier revision numbered these L0–L3; that numbering is retired.

---

## 4. The summary

`summary.json` is the default answer for a happy board or schematic case. The runner
invokes several `kicad-cli` exports, merges them into one JSON document, and compares it
to `expected/<version>/summary.json`. The case author never sees the intermediate exports.

It is called the **summary** because that is what it is: a summary of everything the tool
understood about the file, with the parts that cannot be compared fairly left out. It was
called `model.json` until [DL-0028]; the two are the same thing.

| Input | Composed from | Result |
|---|---|---|
| `.kicad_pcb` | `pcb export stats` + `pcb export pos` + `pcb export ipcd356` | board summary (§4.1) |
| `.kicad_sch` | `sch export netlist` | schematic summary (§4.2) |
| `.kicad_sym`, `.pretty` | — | **none**: there is nothing to build one from (§4.5) |

### 4.0 Canonical form (both summaries)

- **JSON, UTF-8, LF line endings, two-space indent, keys sorted, trailing newline.**
  Written by `json.dumps(summary, indent=2, sort_keys=True) + "\n"`. Sorted keys make the
  document order-independent, so a diff is always a semantic diff.
- **Every list is sorted** by its own printed content. Nothing depends on the order KiCad
  happened to emit.
- **Numbers that KiCad prints as fixed-precision strings stay strings**, verbatim
  (`"0.2500 mm"`, `"20.000000"`). String equality then *is* printed-quantum tolerance
  (DESIGN §3c): `pos` prints 6 decimals of a millimetre = 1 nm = KiCad's own integer board
  unit, so an exact string compare is exactly-1-nm and nothing wider. No float parsing, no
  tolerance band, nothing for a real error to hide inside.
- **Counts are JSON integers.**
- **No timestamps, no versions, no paths, no UUIDs, no net codes** — every such field is
  dropped by construction, so the summary needs no normalizer step at all.

### 4.1 Board summary

Six top-level keys. Shown in the order they are explained; on disk they are sorted.

| Key | Type | Source | Meaning |
|---|---|---|---|
| `kind` | string | — | Always `"board"`. Says which schema this document follows. |
| `has_outline` | bool | `stats` | Did the board yield a closed `Edge.Cuts` outline. |
| `min_track_width` | string | `stats` | Narrowest track actually on the board, e.g. `"0.2500 mm"`. |
| `min_drill_diameter` | string | `stats` | Smallest drilled hole, e.g. `"0.4000 mm"`. |
| `counts` | object | `stats` | Integer inventory: `footprints{tht,smd,unspecified,total}`, `pads{through_hole,smd,connector,npth,castellated,press_fit}`, `vias{through,blind,buried,micro}`. Copied verbatim from KiCad's own key names except `components` → `footprints`. The front/back split is dropped — `placement` already records each footprint's side. |
| `drill_holes` | array | `stats` | The hole table: `count`, `shape`, `x_size`, `y_size`, `plated`, `source`, `start_layer`, `stop_layer`. Content-sorted, so hole ordering never matters. |
| `placement` | object | `pos` | `refdes → {value, package, x, y, rotation, side}`. Strings, verbatim from the CSV. |
| `nets` | object | `ipcd356` | `net-name → sorted array of "REFDES.PAD"` strings. |

**Sources, and the exact commands.**

```
$ kicad-cli pcb export stats  --format json                             -o stats.json  board.kicad_pcb
$ kicad-cli pcb export pos    --format csv --side both --units mm       -o pos.csv     board.kicad_pcb
$ kicad-cli pcb export ipcd356                                          -o board.d356  board.kicad_pcb
```

`pos` uses the **CSV** form deliberately: the ASCII form carries a `created on
<timestamp>` header that CSV does not, so the CSV needs no normalizer at all. `ipcd356`
carries no timestamp either. `stats` has exactly one nondeterministic field,
`metadata.date` — and the whole `metadata` object is dropped anyway (it also carries the
KiCad version string and the scratch-copy filename).

**`nets` — member format and the uppercase gotcha.** A net member is the single string
`"<refdes>.<pad>"` (`"R1.1"`), sorted lexicographically — one token per line in the
formatted JSON, which is what makes a connectivity change read as a one-line diff. Split
on the **first** `.`; a refdes never contains one. Vias contribute their **net name** but
no member, so a net routed only through a via appears with an empty array.

> **IPC-D-356 is an uppercase-only format.** The board's net is literally `Net-1`, but
> `pcb export ipcd356` emits `NET-1`, and the summary records what the export prints.
> Verified: the fixture contains `(net "Net-1")`, the export line is
> `327NET-1            R1    -1          A01X+007874Y…`. Consequence for a second
> implementation: **upper-case net names in `summary.nets`**, and be aware that two nets
> differing only in case would collide here. Schematic-side net names (§4.2) are *not*
> uppercased — the netlist export preserves them.

**Board Y is flipped.** `pos` reports fab-convention coordinates: a footprint at board
`y = 20 mm` prints as `-20.000000`. Both sides of a comparison see the same convention, so
it is invisible to the check — but it is why the recorded `y` values are negative.

**What is deliberately excluded from `stats`, and why.**

`pcb export stats` also reports `area`, `front_copper_area`, `back_copper_area`,
`front_footprint_area`, `back_footprint_area`, `front_component_density`,
`back_component_density`, `min_track_clearance`, `width`, `height`. **None of them are in
the summary.** The rule: *keep counts and echoed input values; drop computed geometry.*

- **Areas and densities are computed float geometry** — polygon unions, clipping, and
  rounding to 3–4 significant digits (`"9.258 mm²"`). Two conformant implementations can
  legitimately differ in the last printed digit for reasons that are not bugs (different
  tessellation of an arc, different rounding at a boundary), so these fields are a
  **false-failure generator with very little conformance signal**: every defect they could
  catch is already caught exactly and integer-precisely by `counts`, `placement` and
  `has_outline`. Same reasoning that rules out pre-authorized tolerance bands (DESIGN
  §3c): a float you cannot compare exactly is a float you end up comparing loosely, and a
  loose compare hides real errors.
- **`min_track_clearance` is excluded** for the same reason *and* because it is usually the
  sentinel `"2147.4836 mm"` (INT_MAX nanometres — KiCad's "only one segment on this net"
  placeholder).
- **`width`/`height` are excluded** as bounding-box arithmetic over the outline; the
  boolean `has_outline` carries the fact that matters without the arithmetic.
- **`min_track_width` and `min_drill_diameter` are kept** because they are *echoed input
  values*, not computed geometry: the width of a track literally in the file, and the
  drill diameter of a pad literally in the file.

**`min_track_width` is also how the summary notices a lost track** — the one thing `stats`
gives no count for. Verified by deleting the board's only `(segment …)` and re-running:

```
$ diff summary-base.json summary-notrack.json
49c49
<   "min_track_width": "0.2500 mm",
---
>   "min_track_width": "2147.4836 mm",
```

That is *coarse* — the summary counts no tracks and records no track geometry. It used to
be the honest limit of the suite; since [DL-0026] the **gerbers** cover the actual plotted
copper geometry byte-for-byte (§7), so a moved or dropped track now shows up there too.

### 4.2 Schematic summary

For a schematic the netlist export is already very nearly the complete semantic
projection — it is KiCad's own answer to "what did I understand this schematic to mean" —
so the schematic summary is a thin, de-noised rewrite of it. Three top-level keys:

| Key | Type | Meaning |
|---|---|---|
| `kind` | string | Always `"schematic"`. |
| `components` | object | `refdes → {value, part, footprint, sheet, pins[]}`. `part` is the library part name, `footprint` the `Footprint` field (`""` when unset), `sheet` the sheet path (`"/"` for a single-sheet design), `pins` the sorted list of pin numbers the symbol declares. |
| `nets` | object | `net-name → sorted array of "REFDES.PIN"` strings — identical shape to the board summary's `nets`, so the two are directly comparable. |

```
$ kicad-cli sch export netlist --format kicadsexpr -o net.net sheet.kicad_sch
$ kicad-cli sch export netlist --format kicadxml   -o net.xml sheet.kicad_sch
```

**Dropped:** the `(design …)` header (absolute source path, wall-clock date, tool
version), every `tstamps` UUID, the net `code` and `class`, `pinfunction`/`pintype` (they
describe the library symbol, not the connectivity), and the whole `title_block`.

**`pins` is what makes the summary catch a dropped pin.** `nets` only lists pins that are
*connected*; a symbol whose unconnected pin was silently lost would not change `nets` at
all, but it changes `components.<ref>.pins`.

**Either interchange format produces the identical summary.** `kicadsexpr` and `kicadxml`
carry the same content (verified field-by-field on the two-symbol fixture), so the reducer
reads both and a case may assert the *same* `summary.json` twice — once per format — via
`extra = ["summary-kicadxml"]`. That is the cheapest available proof that the summary
measures meaning rather than one serialization, and it is why a second implementation may
emit whichever format it prefers.

### 4.3 A real, verbatim example — board

**This is exactly what `expected/10.0.5/summary.json` must contain** for
`suites/board-parse/happy/0002-populated-board/`:

```json
{
  "counts": {
    "footprints": { "smd": 1, "tht": 1, "total": 2, "unspecified": 0 },
    "pads": { "castellated": 0, "connector": 0, "npth": 0, "press_fit": 0, "smd": 2, "through_hole": 2 },
    "vias": { "blind": 0, "buried": 0, "micro": 0, "through": 1 }
  },
  "drill_holes": [
    {
      "count": 1, "plated": true, "shape": "Round", "source": "Via",
      "start_layer": "F.Cu", "stop_layer": "B.Cu",
      "x_size": "0.4000 mm", "y_size": "0.4000 mm"
    },
    {
      "count": 2, "plated": true, "shape": "Round", "source": "Pad",
      "start_layer": "F.Cu", "stop_layer": "B.Cu",
      "x_size": "0.8000 mm", "y_size": "0.8000 mm"
    }
  ],
  "has_outline": true,
  "kind": "board",
  "min_drill_diameter": "0.4000 mm",
  "min_track_width": "0.2500 mm",
  "nets": {
    "GND": [ "C1.1", "R1.2" ],
    "NET-1": [ "C1.2", "R1.1" ]
  },
  "placement": {
    "C1": {
      "package": "C_Disc_D3.0mm_W1.6mm_P2.50mm", "rotation": "180.000000",
      "side": "bottom", "value": "100n", "x": "40.000000", "y": "-30.000000"
    },
    "R1": {
      "package": "R_0805_2012Metric", "rotation": "90.000000",
      "side": "top", "value": "1k", "x": "20.000000", "y": "-20.000000"
    }
  }
}
```

(Shown compactly for readability; on disk every object is fully expanded by
`indent=2`.) Read it top to bottom and it is a complete, plain description of the board:
two footprints (one SMD, one through-hole), four pads, one via, three drilled holes in two
sizes, an outline, a 0.25 mm minimum track, `R1` at 20/−20 rotated 90° on top, `C1` at
40/−30 rotated 180° on the bottom, and two nets each joining one pad of each part.

### 4.4 A real, verbatim example — schematic

From the two-symbol fixture (two 2-pin parts sharing both endpoints):

```json
{
  "components": {
    "U1": { "footprint": "", "part": "T2", "pins": [ "1", "2" ], "sheet": "/", "value": "T2" },
    "U2": { "footprint": "", "part": "T2", "pins": [ "1", "2" ], "sheet": "/", "value": "T2" }
  },
  "kind": "schematic",
  "nets": {
    "Net-(U1-Pad1)": [ "U1.1", "U2.1" ],
    "Net-(U1-Pad2)": [ "U1.2", "U2.2" ]
  }
}
```

### 4.5 Symbol and footprint libraries: renders only

`kicad-cli` 10.0.5 offers exactly two things for a library — `upgrade` and `export svg`:

```
$ kicad-cli sym export --help
Usage: sym export [--help] {svg}
$ kicad-cli fp export --help
Usage: fp export [--help] {svg}
```

There is **no structured export** to build a summary from (no pin table, no pad table).
So a library case's standard answers are its **drawings and nothing else** — one SVG per
symbol-unit or per footprint, in a `render/` directory. Verified filenames:

```
$ kicad-cli sym export svg -o out --black-and-white test.kicad_sym
Plotting symbol 'T1' unit 1 to 'out/T1_unit1.svg'
Plotting symbol 'T2' unit 1 to 'out/T2_unit1.svg'

$ kicad-cli fp export svg -o out --black-and-white ./test.pretty
Plotting footprint 'PadOnly' to 'out/PadOnly.svg'
```

Both are deterministic apart from the `<title>` line (§6), verified by a second run two
seconds later:

```
$ diff out1/T1_unit1.svg out2/T1_unit1.svg
11c11
< <title>SVG Image created as T1_unit1.svg date 2026-08-03T04:55:50 </title>
---
> <title>SVG Image created as T1_unit1.svg date 2026-08-03T04:55:53 </title>
```

If a future KiCad grows a structured symbol/footprint export, a library summary (pin
inventory / pad inventory) is the obvious extension and this section is where it lands.

### 4.6 The summary is deterministic — verified

Generated twice in the same container, one second apart:

```
$ diff summary-board-1.json summary-board-2.json && echo IDENTICAL
IDENTICAL
$ diff summary-sch-1.json summary-sch-2.json && echo IDENTICAL
IDENTICAL
```

No normalizer is involved: the nondeterministic fields (`metadata.date` in `stats`, the
netlist header's date/path/tool) are *dropped by the reduction itself*, not scrubbed after
the fact. This satisfies the honesty rule (DESIGN §4): add no normalizer where the output
is already stable.

### 4.7 The summary is falsifiable — verified

Three single-token perturbations of the fixture, each producing a minimal, legible diff:

```
# A -- delete the board's only track
49c49
<   "min_track_width": "0.2500 mm",
>   "min_track_width": "2147.4836 mm",

# B -- rotate R1 from 90 to 45 degrees
71c71
<       "rotation": "90.000000",
>       "rotation": "45.000000",

# C -- move R1 pad 1 from Net-1 to GND
52a53
>       "R1.1",
56,57c57
<       "C1.2",
<       "R1.1"
>       "C1.2"
```

"A test that cannot fail is not evidence": a new case is not trusted until its author has
broken the fixture and watched it go red like this.

---

## 5. Extras — the opt-in answers

A few things are not projections of the file but separate questions about it, so they are
not in the standard set. A case asks for them by name ([DL-0027]):

```toml
extra = ["drc"]
```

| `extra` | `kicad-cli` | Answer file | Use it when the case is about… |
|---|---|---|---|
| `drc` | `pcb drc --format json --severity-all` | `drc.json` | **findings** — a rule violation is data to compare, not a tool failure |
| `erc` | `sch erc --format json --severity-all` | `erc.json` | the same, for schematics |
| `pos` | `pcb export pos --format csv --side both --units mm` | `pos.json` | placement specifically (a rotation/side edge case) |
| `stats` | `pcb export stats --format json` | `stats.json` | the inventory report itself |
| `ipcd356` | `pcb export ipcd356` | `ipcd356.json` | **test-point/access-point geometry** specifically |
| `netlist` | `sch export netlist` | `netlist.json` | the netlist interchange format itself (formats, hierarchy) |
| `summary-kicadxml` | `sch export netlist --format kicadxml` | *(reuses `summary.json`)* | cross-format fairness (§4.2) |

The reductions for `pos`, `ipcd356`, `stats` and `netlist` are the same ones the summary
composes, emitted standalone. `ipcd356` standalone additionally exposes the **test-point
geometry** map (`REFDES.PAD → {x, y, access-layer}` in printed 0.1-mil integers) that the
summary omits — see §8.1.

**Rule of thumb.** If the case's one-sentence `concept` names the projection ("this board
reports zero DRC violations"), the extra is right. Otherwise the standard answers already
cover the input from four angles and an extra adds noise.

---

## 6. `render` — the SVG comparison, and the layer decision

### 6.1 The comparison

**KiCad's SVG is deterministic except one line.** Verified again for this revision:

```
$ kicad-cli pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet \
      --black-and-white -o r1.svg board.kicad_pcb      # and again as r2.svg, 1 s later
$ diff r1.svg r2.svg
11c11
< <title>SVG Image created as r1.svg date 2026-08-03T04:13:09 </title>
---
> <title>SVG Image created as r2.svg date 2026-08-03T04:13:11 </title>
```

The path geometry (`d="M 9.5500,11.4500 …"`, mm to 4 decimals) and every fill are
byte-stable. So:

- **KiCad vs KiCad (today's only case):** normalize `<title>` and `<desc>` to a constant
  and compare the SVG **byte-exact**. Zero tolerance, no rasterizer needed — none ships in
  the image. Determinism is pinned at the source with `--page-size-mode 2` (board area
  only), `--exclude-drawing-sheet`, `--black-and-white`, plus `LC_ALL=C.UTF-8`/`TZ=UTC`.
- **Cross-implementation (arrives with the second adapter):** a clean-room tool emits
  valid-but-differently-structured SVG, so exact matching would over-fit it. That path
  rasterizes both sides with a **pinned `resvg`** and pixel/SSIM-diffs under an explicit,
  per-case, documented threshold that must be shown load-bearing. Full rationale in
  [DL-0021].

### 6.2 Which layer a board renders — the decision

**One layer: `F.Cu`, recorded as `render-F_Cu.svg`.**

This has to be a decision because **KiCad has no default here.** Unlike `pcb export
gerbers`, the SVG export refuses to run without an explicit layer list, in both its output
modes:

```
$ kicad-cli pcb export svg --mode-multi -o out --page-size-mode 2 \
      --exclude-drawing-sheet --black-and-white board.kicad_pcb
At least one layer must be specified
```

So the choice is the harness's, and it is **minimal on purpose** ([DL-0025]):

1. **The gerbers already cover per-layer geometry byte-exactly** — and not just copper:
   the minimal fixture's default set includes silkscreen, mask, paste, adhesive,
   courtyard, fab and edge cuts (§7.1). Rendering those layers as SVG too would record the
   same geometry a second time in a second format.
2. **`F.Cu` is the one layer every KiCad board has** (copper layer 1 is mandatory in the
   layer table) and it is where routing and SMD pads live — the layer most likely to move
   when someone edits the board. A fixed layer also keeps every board case comparable with
   every other.
3. **Rendering all layers would multiply repo bytes and runtime by roughly twenty** for
   geometry the gerbers pin exactly.
4. It is what the repo already records, so this decision regenerates nothing.

The render's remaining jobs are the two the gerbers cannot do: it is the **human-readable**
artifact a reviewer can open, and it is the artifact that survives the move to
cross-implementation comparison, because an SVG can be rasterized and a gerber cannot
(without adding a gerber rasterizer — §7.4).

**Schematics need no decision:** `sch export svg` takes no layer list, so the sheet is the
only thing there is. The runner records it as `render.svg`.

> **Open item.** A multi-page sheet produces one SVG per page. The repo has no multi-sheet
> fixture yet, so the naming for that case is **not verified** and is deliberately not
> invented here; the first multi-sheet case (ROADMAP M1/M2) pins it.

**Libraries** render every symbol-unit / footprint into `render/` under KiCad's own names
(§4.5). There is no selection to make: a library case is about the library.

---

## 7. Fabrication output — restored as byte answers ([DL-0026])

Every board case records the gerbers and the drill file that KiCad produces, **file for
file and byte for byte** after the normalizers in §7.3. This replaces the coverage gap the
previous revision opened ([DL-0024]) and closes ROADMAP M4 by its option 1.

### 7.1 The layer set is KiCad's, not a flag

`pcb export gerbers` is run with **no `--layers`**. KiCad plots the layer set stored in
the board, falling back to its own built-in default when the board carries none. That set
varies per board — and that is the point: it is exactly what the fab receives, and it
removes a knob from the manifest.

Verified on both committed fixtures:

```
$ kicad-cli pcb export gerbers -o out 0002-populated-board/board.kicad_pcb
Plotted to 'out/board-F_Cu.gtl'.
Plotted to 'out/board-B_Cu.gbl'.
Plotted to 'out/board-Edge_Cuts.gm1'.
Plotted to 'out/board-Margin.gbr'.
Plotted to 'out/board-F_Courtyard.gbr'.
Plotted to 'out/board-B_Courtyard.gbr'.
                       -> 6 gerbers + board-job.gbrjob  (7 files, 5 573 bytes)

$ kicad-cli pcb export gerbers -o out 0001-minimal-two-layer-board/board.kicad_pcb
                       -> 20 gerbers + board-job.gbrjob (21 files, 12 317 bytes):
   board-F_Cu.gtl          board-B_Cu.gbl          board-F_Adhesive.gta    board-B_Adhesive.gba
   board-F_Paste.gtp       board-B_Paste.gbp       board-F_Silkscreen.gto  board-B_Silkscreen.gbo
   board-F_Mask.gts        board-B_Mask.gbs        board-User_Drawings.gbr board-User_Comments.gbr
   board-User_Eco1.gbr     board-User_Eco2.gbr     board-Edge_Cuts.gm1     board-Margin.gbr
   board-F_Courtyard.gbr   board-B_Courtyard.gbr   board-F_Fab.gbr         board-B_Fab.gbr
```

**Why the populated board plots fewer layers than the minimal one:** it carries a stored
plot-settings block and the minimal one does not.

```
$ grep -A2 pcbplotparams 0002-populated-board/board.kicad_pcb
35:		(pcbplotparams
36-			(layerselection 0x00000000_00000000_55555555_5755f5ff)
$ grep -c pcbplotparams 0001-minimal-two-layer-board/board.kicad_pcb
0
```

Both are stable run-to-run (§7.3), which is the property the comparison needs.

The drill export likewise takes no options beyond `-o`: it produces exactly one file,
`<input-stem>.drl`, in both the with-holes and no-holes cases. No `--generate-map`, no
`--generate-report`, no `--excellon-separate-th`.

### 7.2 What is recorded

```
expected/10.0.5/
├── gerbers/           # every file `pcb export gerbers -o <dir>` wrote, KiCad's own names
└── drill/             # every file `pcb export drill -o <dir>/` wrote (one .drl)
```

Compared as **directory trees**: the set of filenames must match exactly (a missing or
extra file is a failure), and every file must be byte-identical after §7.3.

### 7.3 The normalizers — each one verified against the binary

Method: export twice into different directories, two seconds apart, in the same container,
and diff. **Everything that differed is listed below; nothing else differed.**

**Gerber layer files** (`.gtl .gbl .gts .gbs .gto .gbo .gtp .gbp .gta .gba .gm1 .gbr`) —
exactly **two** lines, in every one of the 21 files:

```
$ diff -u run1/board-F_Cu.gtl run2/board-F_Cu.gtl
 %TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%
-%TF.CreationDate,2026-08-03T04:52:25+00:00*%
+%TF.CreationDate,2026-08-03T04:52:27+00:00*%
 %TF.ProjectId,board,626f6172-642e-46b6-9963-61645f706362,rev?*%
 %TF.SameCoordinates,Original*%
 %TF.FileFunction,Copper,L1,Top*%
 %TF.FilePolarity,Positive*%
 %FSLAX46Y46*%
 G04 Gerber Fmt 4.6, Leading zero omitted, Abs format (unit mm)*
-G04 Created by KiCad (PCBNEW 10.0.5) date 2026-08-03 04:52:25*
+G04 Created by KiCad (PCBNEW 10.0.5) date 2026-08-03 04:52:27*
```

→ Normalizer **G1**: replace the value in `%TF.CreationDate,<ts>*%` with a constant.
→ Normalizer **G2**: replace the trailing ` date <YYYY-MM-DD HH:MM:SS>` in the
`G04 Created by KiCad (PCBNEW <ver>) date …*` line with a constant.

**Gerber job file** (`.gbrjob`, JSON) — exactly **one** line:

```
$ diff -u run1/board-job.gbrjob run2/board-job.gbrjob
       "Version": "10.0.5"
     },
-    "CreationDate": "2026-08-03T04:52:25+00:00"
+    "CreationDate": "2026-08-03T04:52:27+00:00"
   },
```

→ Normalizer **G3**: set the `Header.CreationDate` key to a constant.

**Excellon drill file** (`.drl`) — exactly **two** lines, on both the populated and the
empty board:

```
$ diff -u run1/board.drl run2/board.drl
 M48
-; DRILL file KiCad 10.0.5 date 2026-08-03T04:52:57
+; DRILL file KiCad 10.0.5 date 2026-08-03T04:53:00
 ; FORMAT={-:-/ absolute / metric / decimal}
-; #@! TF.CreationDate,2026-08-03T04:52:57+00:00
+; #@! TF.CreationDate,2026-08-03T04:53:00+00:00
 ; #@! TF.GenerationSoftware,Kicad,Pcbnew,10.0.5
 ; #@! TF.FileFunction,MixedPlating,1,2
```

→ Normalizer **D1**: replace the trailing timestamp in `; DRILL file KiCad <ver> date …`.
→ Normalizer **D2**: replace the value in `; #@! TF.CreationDate,<ts>`.

**Four things this revision does NOT normalize, and why — all three of these were on the
list inherited from DESIGN §4 and are wrong:**

| Inherited claim | Evidence | Call |
|---|---|---|
| Normalize `TF.GenerationSoftware` | `%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%` is **identical across runs** — it is a version string, not a timestamp | **Do not normalize.** Answers are keyed by KiCad version; leaving this line intact makes every gerber assert, for free, that it was produced by the pinned version. Scrubbing it would delete signal. |
| Normalize the `.gbrjob` `Header/GenerationSoftware` | same — stable | **Do not normalize.** Only `Header.CreationDate` moves. |
| Normalize the Excellon *version* in the header | `; #@! TF.GenerationSoftware,Kicad,Pcbnew,10.0.5` is stable; only the two date lines move | **Do not normalize.** |
| Normalize the drill report's `Created on` line | **The drill report is never produced.** It requires `--generate-report`, which the standard answers do not pass; the default export writes exactly one file, `board.drl` | **Delete this normalizer from the spec.** It has no input. |

That is five normalizers, not the eight the previous spec implied. Each one is
demonstrably load-bearing by the diffs above, which is the standard DESIGN §4a sets.

**One rule that is not a normalizer:** gerber output embeds the input's filename, in both
the filenames and the `%TF.ProjectId` line, whose GUID is the filename's own bytes.

```
board.kicad_pcb   -> board-F_Cu.gtl    %TF.ProjectId,board,626f6172-642e-46b6-9963-61645f706362,rev?*%
renamed.kicad_pcb -> renamed-F_Cu.gtl  %TF.ProjectId,renamed,72656e61-6d65-4642-9e6b-696361645f70,rev?*%
```

The runner therefore copies each input to scratch **under its original name**, and case
authors name board inputs `board.kicad_pcb`. Normalizing the project id instead would
throw away a real assertion (that the tool identified the project correctly) to buy
nothing.

### 7.4 What byte answers do and do not prove — stated plainly

Byte answers are a **KiCad-version-regression signal**. They catch, exactly:

- a plotter change between KiCad patch releases (an aperture emitted differently, a
  polarity flipped, a coordinate re-rounded, a layer dropped from the default set);
- a board edit that changes the plot — a moved track, a resized pad, a deleted via —
  including the track *geometry* the summary only sees through `min_track_width` (§4.1);
- a hole's *position*, which the summary's hole table does not record at all.

They **do not** prove a second implementation is correct. A clean-room tool emitting valid
RS-274X with different-but-equivalent apertures, a different coordinate format, or
regions instead of strokes would fail every one of these files while being perfectly
conformant. So, exactly as [DL-0015] scoped the old byte layer:

> **A second implementation is not judged on `gerbers/` or `drill/`.** In ecosystem mode
> the runner reports these as `INFO`, never `FAIL`. The cross-implementation path is
> rasterize-and-compare ([DL-0021], ROADMAP M4 option 2), and it stays on the roadmap.

This is the same scoping that made the *old* byte layer feel vestigial, so it is worth
saying why it is right this time: the old layer also covered re-serialized `.kicad_pcb`
and `.kicad_sch` bytes, where the semantic comparison (the summary) already gave a better
answer, so it was pure duplication. Fab output has **no** semantic comparator — a
structural RS-274X reduction was ruled out as a second plotter's worth of engineering
([DL-0020]) — so here the byte answer is not duplicating anything. It is the only thing
in the suite that looks at what a fab actually gets.

### 7.5 Cost

Recorded fab answers are small: 12 317 bytes for the 21-file set, 5 573 for the 7-file
set, under 1 kB per drill file. Regenerating them is ~0.4 s per board (§8.3). Both scale
linearly with case count and neither is a concern at the current size.

---

## 8. Remaining gaps — stated plainly

### 8.1 Pad geometry within a footprint

The summary records where each *footprint* sits (1 nm precision) and which net each *pad*
is on, but not where each pad sits. A bug that applies footprint rotation to the origin
but not to the pad offsets would leave the summary unchanged.

This is a deliberate trade: `ipcd356` prints pad positions in 0.0001-inch integers — a
2.54 µm quantum reached by unit conversion and rounding, exactly the false-failure risk
that got the float areas excluded (§4.1). It is now **well covered elsewhere**: the F.Cu
render *and* the gerbers both move when a pad moves. A case specifically about
access-point geometry can still use `extra = ["ipcd356"]`, which exposes the test-point
map.

### 8.2 Graphic geometry on unplotted layers

`stats` counts no graphics, and the gerbers only cover layers KiCad plots. A graphic on a
layer that is disabled in the board's plot settings is recorded nowhere. This is narrow
and is the correct behaviour to have: an unplotted layer does not reach the fab.

### 8.3 Cross-implementation fab comparison

Covered above (§7.4): byte answers are KiCad-vs-KiCad only. Rasterize-and-compare remains
the fair-across-implementations answer and remains on the roadmap (M4 option 2), now as an
*upgrade* to real coverage rather than a rescue from zero coverage.

---

## 9. Runner integration (for the implementing agent)

### 9.1 What the runner runs, per input type

| Input | Invocations | Answers written |
|---|---|---|
| `.kicad_pcb` | `pcb export stats --format json`; `pcb export pos --format csv --side both --units mm`; `pcb export ipcd356`; `pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet --black-and-white`; `pcb export gerbers`; `pcb export drill` | `summary.json`, `render-F_Cu.svg`, `gerbers/`, `drill/` |
| `.kicad_sch` | `sch export netlist --format kicadsexpr`; `sch export svg --exclude-drawing-sheet --black-and-white` | `summary.json`, `render.svg` |
| `.kicad_sym` | `sym export svg --black-and-white` | `render/` |
| `.pretty` / `.kicad_mod` | `fp export svg --black-and-white` | `render/` |
| any, `failure/` | the type's loader (`pcb\|sch\|sym\|fp upgrade --force` on a scratch copy) | none — exit + stderr only |

Extras add one invocation each (§5).

**`-o` semantics differ per verb and are easy to get wrong** — verified:

- `pcb export svg` (default single mode): `-o` is a **file path**. Passing a directory
  fails with `Failed to create file '<dir>'`.
- `pcb export gerbers`, `pcb export drill`, `sch export svg`, `sym export svg`,
  `fp export svg`: `-o` is a **directory**, and it is created if it does not exist
  (verified).
- `pcb export svg` also warns *"This command has deprecated behavior as of KiCad 9.0 …
  The new behavior will match --mode-multi"*. The runner should keep using single mode
  with an explicit output filename while 10.0.5 is pinned, and re-verify at the KiCad 11
  bump — the warning is a scheduled behaviour change, and the version bump is exactly when
  the answers are regenerated and diffed anyway.

### 9.2 Comparison dispatch

Comparison follows from the answer, not from a manifest field:

- `*.json` → parse both sides, compare for equality, diff structurally.
- `render*.svg` and `render/*.svg` → normalize `<title>`/`<desc>`, compare bytes.
- `gerbers/`, `drill/` → directory-tree compare; per-file normalizers G1–G3 / D1–D2 (§7.3),
  then bytes.

### 9.3 Where the code goes

- **`runner/summary.py`** — `build_board_summary(stats_json, pos_csv, d356_text)` and
  `build_schematic_summary(netlist_text, fmt)`, exactly to §4. The existing
  `reduce_stats` / `reduce_pos` / `reduce_ipcd356` / `reduce_netlist` /
  `reduce_netlist_kicadxml` in `runner/reduce.py` are the raw parsers these compose; keep
  them (the extras still use them) and drop from `reduce_stats` everything §4.1 excludes.
- **`runner/adapters/kicad.py`** — one entry point per input type that runs the whole
  standard set into one scratch directory. Composition happens **in the adapter**, so a
  non-KiCad implementation can emit its `summary.json` directly without imitating three
  KiCad exports.
- **`runner/engine.py`** — no `op` dispatch left. Reinstate a directory-tree comparator
  for `gerbers/`/`drill/` (the one [DL-0024] deleted) and the gerber/Excellon normalizers,
  narrowed to the five in §7.3. Keep the SVG normalizer and CRLF→LF.
- **`.gitattributes`** — gerber and Excellon answers are text and must be stored LF
  ([DL-0016]); add `expected/**/gerbers/**` and `expected/**/drill/**`.

### 9.4 Runtime, and what to do about it

Measured per invocation on the populated fixture, inside the container:

```
stats 448 ms   pos 385 ms   ipcd356 362 ms   svg 373 ms   gerbers 384 ms   drill 353 ms
```

A board case is six invocations ≈ **2.3 s**; it was four ≈ 1.6 s before the fab answers
returned. A schematic case is two ≈ 0.8 s. The current 7-case suite is roughly **10 s**.

Recommendations (the build agent decides implementation):

1. **Run the whole suite inside one container.** `docker run` startup is comparable to a
   whole case; a container per invocation would dominate everything measured above. This
   is already how CI invokes the runner.
2. **Parallelize across cases, not within them.** Cases are independent and each gets its
   own scratch directory; a process pool over cases is a few lines. The six invocations
   *inside* a case share a scratch directory and are only ~0.4 s each, so splitting them is
   contention for no gain.
3. **Do not add a cache.** Caching a 2-second operation keyed on file content is more
   machinery than it saves, and a stale cache in a conformance suite is a false green —
   the one failure mode this repo can least afford. Revisit only if the suite passes ~100
   cases, and then prefer parallelism first.

### 9.5 Failure-case machinery is unchanged

`OK`/`REJECT`/`CRASH` classification ([DL-0013]), the positive control, and the
`known_divergence` strict-xfail layer ([DL-0018]) are untouched.

---

## 10. Decisions

[DL-0025] (standard answers chosen by input type; `op` and `[[check]]` deleted),
[DL-0026] (gerbers + drill restored as byte answers on every board, KiCad's own layer set),
[DL-0027] (the `extra` list and the failure-case shape),
[DL-0028] (`model.json` → `summary.json`).
Earlier and still standing: [DL-0013] (crash verdict + controls), [DL-0018] (strict
xfail), [DL-0020] (no structural gerber reduction), [DL-0021] (SVG method), [DL-0022]
(one composite answer per case), [DL-0023] (`expected`, no `compare`).
