# Validation — what a test case actually compares

How kicad-conformance decides "did the tool get it **right**". Companion docs:
[`DESIGN.md`](DESIGN.md) (architecture), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md)
(how to write a case), [`DECISIONS.md`](DECISIONS.md) (numbered rationale —
[DL-0022]–[DL-0024] ratify this document).

Every empirical claim below was produced against **`kicad-cli` 10.0.5** in the
`kicad/kicad:10.0.5` Docker image, on a small hand-authored populated board (one SMD
resistor `R1`, one through-hole capacitor `C1`, one `F.Cu` track, one through via, an
`Edge.Cuts` outline) and a two-symbol schematic. The exact commands are shown inline; all
of them were run as:

```
docker run --rm -v "<dir>:/work" -w /work -e LC_ALL=C.UTF-8 -e TZ=UTC \
    kicad/kicad:10.0.5 bash -lc '<command>'
```

---

## 1. The idea in one paragraph

A test case is **one input file** — a board or a schematic — and **one recorded correct
answer**. The runner feeds the input to the tool, asks the tool to describe what it
understood, and compares that description to the recorded answer. The description is a
single normalized JSON document called the **model**. If the model matches, the tool
parsed the file into the same thing KiCad did: same footprints in the same places, same
pads on the same nets, same counts, same holes. If it doesn't match, the diff says
exactly which fact the tool got wrong.

That's the whole validation story. Two smaller pieces sit beside it: a **failure case**
only checks that a bad file is rejected (there is no model to compare), and a case whose
whole point is *drawn geometry* compares a **render** (SVG) instead.

## 2. What an "expected file" is

An **expected file** is the recorded correct answer for one check. It is produced once,
by running the reference tool (`kicad-cli`) on the case's input, and then frozen in the
repo under `expected/<kicad-version>/`. Other test frameworks call the same thing a
*snapshot*, a *baseline*, or a *golden file*; this repo calls it what it is.

Three properties follow from "recorded, not written":

- **It is never hand-authored.** A hand-written expected file encodes a human's belief
  about KiCad. A generated one encodes KiCad's behaviour. Only the second is a
  conformance reference ([DL-0004]).
- **It is keyed by KiCad version**, not by tool. `expected/10.0.5/model.json` means "the
  correct answer as defined by KiCad 10.0.5". A second implementation is compared against
  that same file — there is no per-implementation expected file.
- **It is reviewed like source.** Regenerating is a deliberate act (`--regenerate` inside
  the pinned Docker image, [DL-0016]); the contributor reads the diff before committing
  it. A changed expected file is a changed claim about KiCad.

## 3. The three kinds of comparison

| Kind | What it compares | Used by |
|---|---|---|
| **exit** | did the tool accept (exit 0) or gracefully reject (bounded non-zero) the file | every `failure/` case; `parse-*` checks |
| **model** | one normalized JSON document describing everything the tool understood | every `happy/` board & schematic case (the default) |
| **render** | the drawn vector geometry of a layer / sheet / symbol / footprint | cases whose concept *is* the drawing |

An earlier revision of this doc numbered these L0–L3 and included a fourth rung, **L1**,
which compared KiCad's re-serialized bytes (canonical `.kicad_pcb`, gerber files, drill
files). **L1 is gone** ([DL-0024]); the numbering is retired with it, because three
named things do not need a ladder. Where other docs still say "L2" read "model", and "L3"
read "render".

Findings verbs (`drc`, `erc`) are a fourth, narrower thing: they compare a normalized
*finding set*, not a model of the file. They are unchanged by this revision.

---

## 4. The `model` verb

`op = "model"` is the default check for a happy board or schematic case. The runner
invokes several `kicad-cli` exports internally, merges them into one JSON document, and
compares it to `expected/<version>/model.json`. The case author never sees the
intermediate exports; they are an implementation detail of the verb.

The verb **dispatches on the input's suffix**:

| Input | Composed from | Result |
|---|---|---|
| `.kicad_pcb` | `pcb export stats` + `pcb export pos` + `pcb export ipcd356` | board model (§4.1) |
| `.kicad_sch` | `sch export netlist` | schematic model (§4.2) |
| `.kicad_sym`, `.pretty` | — | **error**: `model` does not apply (§4.5) |

### 4.0 Canonical form (applies to both models)

- **JSON, UTF-8, LF line endings, two-space indent, keys sorted, trailing newline.**
  Written by `json.dumps(model, indent=2, sort_keys=True) + "\n"`. Sorted keys make the
  document order-independent, so a diff is always a semantic diff.
- **Every list is sorted** by its own printed content. Nothing in the model depends on the
  order KiCad happened to emit.
- **Numbers that KiCad prints as fixed-precision strings stay strings**, verbatim
  (`"0.2500 mm"`, `"20.000000"`). String equality then *is* printed-quantum tolerance
  (DESIGN §3c): `pos` prints 6 decimals of a millimetre = 1 nm = KiCad's own integer board
  unit, so an exact string compare is exactly-1-nm and nothing wider. No float parsing, no
  tolerance band, nothing for a real error to hide inside.
- **Counts are JSON integers.**
- **No timestamps, no versions, no paths, no UUIDs, no net codes** — every such field is
  dropped by construction (see the per-section notes), so the model needs no separate
  "normalizer" step.

### 4.1 Board model

Six top-level keys. Shown in the order they are explained; on disk they are sorted.

| Key | Type | Source | Meaning |
|---|---|---|---|
| `kind` | string | — | Always `"board"`. Says which schema this document follows. |
| `has_outline` | bool | `stats` → `board.has_outline` | Did the board yield a closed `Edge.Cuts` outline. |
| `min_track_width` | string | `stats` → `board.min_track_width` | Narrowest track actually on the board, e.g. `"0.2500 mm"`. |
| `min_drill_diameter` | string | `stats` → `board.min_drill_diameter` | Smallest drilled hole, e.g. `"0.4000 mm"`. |
| `counts` | object | `stats` | Integer inventory: `footprints{tht,smd,unspecified,total}`, `pads{through_hole,smd,connector,npth,castellated,press_fit}`, `vias{through,blind,buried,micro}`. Copied verbatim from KiCad's own key names except `components` → `footprints` (on a board they are placed footprints; "component" is KiCad's word in the stats report). The front/back split of the component table is dropped — `placement` already records each footprint's side. |
| `drill_holes` | array | `stats` → `drill_holes[]` | The hole table. Each entry keeps `count`, `shape`, `x_size`, `y_size`, `plated`, `source`, `start_layer`, `stop_layer`. Content-sorted (sort key = the entry serialized with sorted keys), so hole ordering never matters. |
| `placement` | object | `pos` | `refdes → {value, package, x, y, rotation, side}`. Strings, verbatim from the CSV. |
| `nets` | object | `ipcd356` | `net-name → sorted array of "REFDES.PAD"` strings. |

**Sources, and the exact commands.**

```
$ kicad-cli pcb export stats  --format json                             -o stats.json  b.kicad_pcb
$ kicad-cli pcb export pos    --format csv --side both --units mm       -o pos.csv     b.kicad_pcb
$ kicad-cli pcb export ipcd356                                          -o board.d356  b.kicad_pcb
```

`pos` uses the **CSV** form deliberately: the ASCII form carries a `created on
<timestamp>` header that CSV does not, so the CSV needs no normalizer at all. `ipcd356`
carries no timestamp either. `stats` has exactly one nondeterministic field, `metadata.date`
— and the whole `metadata` object is dropped anyway (it also carries the KiCad version
string and the scratch-copy filename).

**`nets` — member format and the uppercase gotcha.** A net member is the single string
`"<refdes>.<pad>"` (`"R1.1"`), sorted lexicographically — one token per line in the
formatted JSON, which is what makes a connectivity change read as a one-line diff. Split
on the **first** `.`; a refdes never contains one. Vias contribute their **net name** but
no member, so a net routed only through a via appears with an empty array.

> **IPC-D-356 is an uppercase-only format.** The board's net is literally `Net-1`, but
> `pcb export ipcd356` emits `NET-1`, and the model records what the export prints.
> Verified: the fixture contains `(net "Net-1")`, the export line is
> `327NET-1            R1    -1          A01X+007874Y…`. Consequence for a second
> implementation: **upper-case net names in `model.nets`**, and be aware that two nets
> differing only in case would collide here. Schematic-side net names (§4.2) are *not*
> uppercased — the netlist export preserves them.

**Board Y is flipped.** `pos` reports fab-convention coordinates: a footprint at board
`y = 20 mm` prints as `-20.000000`. Both sides of a comparison see the same convention, so
it is invisible to the check — but it is why the recorded `y` values are negative.

**What is deliberately excluded from `stats`, and why.**

`pcb export stats` also reports `area`, `front_copper_area`, `back_copper_area`,
`front_footprint_area`, `back_footprint_area`, `front_component_density`,
`back_component_density`, `min_track_clearance`, `width`, `height`. **None of them are in
the model.** The rule: *keep counts and echoed input values; drop computed geometry.*

- **Areas and densities are computed float geometry** — polygon unions, clipping,
  and rounding to 3–4 significant digits (`"9.258 mm²"`). Two conformant implementations
  can legitimately differ in the last printed digit for reasons that are not bugs
  (different polygon tessellation of an arc, different rounding at a boundary), so these
  fields are a **false-failure generator with very little conformance signal**: every
  defect they could catch — a dropped pad, a lost footprint, a mis-parsed outline — is
  already caught exactly and integer-precisely by `counts`, `placement`, and `has_outline`.
  This is the owner's explicit call, and it is the same reasoning that rules out
  pre-authorized tolerance bands (DESIGN §3c): a float you cannot compare exactly is a
  float you end up comparing loosely, and a loose compare hides real errors.
- **`min_track_clearance` is excluded** for the same reason (a pairwise geometry
  computation) *and* because it is usually the sentinel `"2147.4836 mm"` (INT_MAX
  nanometres — KiCad's "only one segment on this net" placeholder).
- **`width`/`height` are excluded** as bounding-box arithmetic over the outline; the
  boolean `has_outline` carries the fact that matters (the outline parsed) without the
  arithmetic.
- **`min_track_width` and `min_drill_diameter` are kept** because they are *echoed input
  values*, not computed geometry: they are the width of a track that is literally in the
  file and the drill diameter of a pad that is literally in the file. Comparing them is
  comparing a parsed number, not a derived one.

**`min_track_width` is also how the model notices a lost track** — the one thing `stats`
gives no count for. Verified by deleting the board's only `(segment …)` and re-running:

```
$ diff model-base.json model-notrack.json
49c49
<   "min_track_width": "0.2500 mm",
---
>   "min_track_width": "2147.4836 mm",
```

The trackless board reports the INT_MAX sentinel, so "all tracks vanished" is a one-line
model diff. That is *coarse* — the model counts no tracks and records no track geometry
(§7). It is the honest limit of what `kicad-cli` 10.0.5 exposes without a gerber
interpreter.

### 4.2 Schematic model

For a schematic the netlist export is already very nearly the complete semantic
projection — it is KiCad's own answer to "what did I understand this schematic to mean" —
so the schematic model is a thin, de-noised rewrite of it. Three top-level keys:

| Key | Type | Meaning |
|---|---|---|
| `kind` | string | Always `"schematic"`. |
| `components` | object | `refdes → {value, part, footprint, sheet, pins[]}`. `part` is the library part name (`libsource/part`), `footprint` the `Footprint` field (`""` when unset), `sheet` the sheet path (`"/"` for a single-sheet design), `pins` the sorted list of pin numbers the symbol declares. |
| `nets` | object | `net-name → sorted array of "REFDES.PIN"` strings — identical shape to the board model's `nets`, so the two are directly comparable. |

```
$ kicad-cli sch export netlist --format kicadsexpr -o net.net s.kicad_sch
$ kicad-cli sch export netlist --format kicadxml   -o net.xml s.kicad_sch
```

**Dropped:** the `(design …)` header (absolute source path, wall-clock date, tool
version), every `tstamps` UUID, the net `code` and `class`, `pinfunction`/`pintype` (they
describe the library symbol, not the connectivity), and the whole `title_block`.

**`pins` is what makes the model catch a dropped pin.** `nets` only lists pins that are
*connected*; a symbol whose unconnected pin was silently lost would not change `nets` at
all, but it changes `components.<ref>.pins`.

**Either interchange format produces the identical model.** `kicadsexpr` and `kicadxml`
carry the same `components`/`units/pins`/`nets` content (verified field-by-field on the
two-symbol fixture), so the model reducer reads both and a case may assert the *same*
`model.json` twice, once per format. That is the cheapest available proof that the model
measures meaning rather than one serialization — and it is why a second implementation may
emit whichever format it prefers.

### 4.3 A real, verbatim example — board

Generated by running the three commands in §4.1 on the populated fixture and merging them
with the reduction described above. **This is exactly what
`expected/10.0.5/model.json` must contain** for
`suites/board-parse/happy/0002-populated-board/`:

```json
{
  "counts": {
    "footprints": {
      "smd": 1,
      "tht": 1,
      "total": 2,
      "unspecified": 0
    },
    "pads": {
      "castellated": 0,
      "connector": 0,
      "npth": 0,
      "press_fit": 0,
      "smd": 2,
      "through_hole": 2
    },
    "vias": {
      "blind": 0,
      "buried": 0,
      "micro": 0,
      "through": 1
    }
  },
  "drill_holes": [
    {
      "count": 1,
      "plated": true,
      "shape": "Round",
      "source": "Via",
      "start_layer": "F.Cu",
      "stop_layer": "B.Cu",
      "x_size": "0.4000 mm",
      "y_size": "0.4000 mm"
    },
    {
      "count": 2,
      "plated": true,
      "shape": "Round",
      "source": "Pad",
      "start_layer": "F.Cu",
      "stop_layer": "B.Cu",
      "x_size": "0.8000 mm",
      "y_size": "0.8000 mm"
    }
  ],
  "has_outline": true,
  "kind": "board",
  "min_drill_diameter": "0.4000 mm",
  "min_track_width": "0.2500 mm",
  "nets": {
    "GND": [
      "C1.1",
      "R1.2"
    ],
    "NET-1": [
      "C1.2",
      "R1.1"
    ]
  },
  "placement": {
    "C1": {
      "package": "C_Disc_D3.0mm_W1.6mm_P2.50mm",
      "rotation": "180.000000",
      "side": "bottom",
      "value": "100n",
      "x": "40.000000",
      "y": "-30.000000"
    },
    "R1": {
      "package": "R_0805_2012Metric",
      "rotation": "90.000000",
      "side": "top",
      "value": "1k",
      "x": "20.000000",
      "y": "-20.000000"
    }
  }
}
```

Read it top to bottom and it is a complete, plain description of the board: two
footprints (one SMD, one through-hole), four pads, one via, three drilled holes in two
sizes, an outline, a 0.25 mm minimum track, `R1` at 20/−20 rotated 90° on top, `C1` at
40/−30 rotated 180° on the bottom, and two nets each joining one pad of each part.

### 4.4 A real, verbatim example — schematic

From the two-symbol fixture (`sheet.kicad_sch`, two 2-pin parts sharing both endpoints):

```json
{
  "components": {
    "U1": {
      "footprint": "",
      "part": "T2",
      "pins": [
        "1",
        "2"
      ],
      "sheet": "/",
      "value": "T2"
    },
    "U2": {
      "footprint": "",
      "part": "T2",
      "pins": [
        "1",
        "2"
      ],
      "sheet": "/",
      "value": "T2"
    }
  },
  "kind": "schematic",
  "nets": {
    "Net-(U1-Pad1)": [
      "U1.1",
      "U2.1"
    ],
    "Net-(U1-Pad2)": [
      "U1.2",
      "U2.2"
    ]
  }
}
```

### 4.5 Symbol and footprint libraries: no model

`kicad-cli` 10.0.5 offers exactly two things for a library — `upgrade` and `export svg`:

```
$ kicad-cli sym export --help
Usage: sym export [--help] {svg}
$ kicad-cli fp export --help
Usage: fp export [--help] {svg}
```

There is **no structured export** to build a model from (no pin table, no pad table), and
`upgrade`'s re-serialized bytes are exactly the comparison this revision deleted
([DL-0024]). So:

> **`model` does not apply to `.kicad_sym` or `.pretty` inputs.** A library case uses
> `render` (the SVG projection) as its expected output, plus `parse-sym`/`parse-fp` exit
> checks for failure cases. The runner rejects `op = "model"` on a library input with a
> clear error rather than inventing a projection.

If a future KiCad grows a structured symbol/footprint export, a library `model` (pin
inventory / pad inventory) is the obvious extension and this section is where it lands.

### 4.6 The model is deterministic — verified

Generated twice in the same container, one second apart:

```
$ diff model-board-1.json model-board-2.json && echo IDENTICAL
IDENTICAL
$ diff model-sch-1.json model-sch-2.json && echo IDENTICAL
IDENTICAL
```

No normalizer is involved: the nondeterministic fields (`metadata.date` in `stats`, the
netlist header's date/path/tool) are *dropped by the reduction itself*, not scrubbed after
the fact. This satisfies the honesty rule (DESIGN §4): add no normalizer where the output
is already stable.

### 4.7 The model is falsifiable — verified

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
broken the fixture and watched the model go red like this.

---

## 5. Individual projections (opt-in)

The single-projection verbs still exist, and a case uses one **when that projection *is*
the concept the case documents** — not as a way to spread one board's validation across
several cases (which is what this revision removed).

| Verb | `kicad-cli` | Expected file | Use it when the case is about… |
|---|---|---|---|
| `render` | `pcb\|sch\|sym\|fp export svg` | `render*.svg` | the **drawing**: copper geometry, silkscreen, a symbol's or footprint's shape |
| `pos` | `pcb export pos --format csv --side both --units mm` | `pos.json` | placement specifically (e.g. a rotation/side edge case) |
| `ipcd356` | `pcb export ipcd356` | `ipcd356.json` | board connectivity or **test-point/access-point geometry** specifically |
| `stats` | `pcb export stats --format json` | `stats.json` | the inventory report itself |
| `netlist` | `sch export netlist` | `netlist.json` | the netlist interchange format itself (formats, hierarchy) |
| `drc` / `erc` | `pcb drc` / `sch erc --format json --severity-all` | `drc.json` / `erc.json` | **findings** — a rule violation is data to compare, not a tool failure |

The reductions for `pos`, `ipcd356`, `stats` and `netlist` are the same ones the `model`
verb composes, emitted standalone. `ipcd356` standalone additionally exposes the
**test-point geometry** map (`REFDES.PAD → {x, y, access-layer}` in the printed 0.1-mil
integers) that the model omits — see §7.

**Rule of thumb.** If you find yourself writing a second case on the same fixture to
assert a second projection, you want one case with a `model` check. If you are writing a
case whose one-sentence `concept` names the projection ("the front-copper layer draws the
pad, track and via"), the projection verb is right.

---

## 6. `render` — the SVG comparison

Unchanged in substance by this revision ([DL-0021] still stands); only the verb name is
simpler (`render`, dispatching on the input suffix, replaces `export-svg-pcb`/`-sch`/
`-sym`/`-fp`).

**KiCad's SVG is deterministic except one line.** Verified again for this revision:

```
$ kicad-cli pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet \
      --black-and-white -o r1.svg b.kicad_pcb      # and again as r2.svg, 1 s later
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
  per-case, documented threshold that must be shown load-bearing (perturb the geometry by
  one quantum, watch it go red, or delete the threshold). Full rationale in [DL-0021].

The layer set for a board render is a per-case parameter: `args = ["--layers", "F.Cu"]`.

---

## 7. Known gaps — stated plainly

This revision deliberately deleted comparisons. Here is what is no longer covered, so
nobody discovers it by accident.

### 7.1 Gerber output: **no coverage at all**

The `gerber/` suite is **empty**. Before this revision it held (or was to hold) byte
comparisons of KiCad's RS-274X files; those are deleted with the rest of the byte layer
([DL-0024]), and a *structural* gerber comparison was already ruled out for good reasons
([DL-0020]: a faithful RS-274X reducer means implementing aperture macros, `%LP`
polarity, `G36/G37` regions, arc interpolation, step-and-repeat and `%FS` coordinate
formats — a second plotter's worth of engineering whose output is as
formatting-sensitive as the byte compare it replaces).

**What this means concretely:** a bug that corrupts gerber output while leaving the
`.kicad_pcb` model intact — a plotter-only aperture or polarity bug — **is not caught by
this suite.** The board model proves the *board* was understood correctly; nothing proves
the *plot* of it is correct, except indirectly through the `render` SVG of the same
copper layers.

**Two ways back, when someone wants it:**
1. **Byte expected-files for gerbers only** — cheap to build (the normalizers for the
   `G04` header dates and the `.gbrjob` JSON date are already specified in DESIGN §4), and
   honest as long as it is labelled a KiCad-version-regression check rather than a
   cross-implementation one.
2. **Rasterize gerbers → image compare** — plot each gerber to a raster with a pinned
   renderer and compare images, exactly as the cross-implementation `render` path does.
   This is the fair-across-implementations option and the better long-term answer, at the
   cost of adding a gerber rasterizer to the CI image.

### 7.2 Drill output: **no file-level coverage; the hole table survives**

The `drill/` suite is **empty** for the same reason. What remains is the model's
`drill_holes` section — the hole *table* (count, shape, size, plated, source, layer
span), which does catch a dropped or mis-sized hole. What is **not** covered: the Excellon
file itself (coordinate formatting, tool assignment, header) and **hole positions** — the
table has no coordinates.

Restoration options are the same two as gerbers; option 1 (byte expected-files, with the
already-specified header-date normalizer) is materially easier for drill than for gerber.

### 7.3 Pad geometry within a footprint

The model records where each *footprint* sits (1 nm precision) and which net each *pad* is
on, but not where each pad sits. A bug that applies footprint rotation to the origin but
not to the pad offsets would leave the model unchanged.

This is a deliberate trade. `ipcd356` prints pad positions in 0.0001-inch integers — a
2.54 µm quantum reached by unit conversion and rounding, which is exactly the
false-failure risk that got the float areas excluded (§4.1). Instead: the **`render` check
on the same fixture** compares the actual drawn copper, which *does* move when a pad
moves, and a case specifically about access-point geometry can use the standalone
`ipcd356` projection, which exposes the test-point map.

### 7.4 Track and graphic geometry

`stats` counts no tracks and no graphics, so the model sees routing only through
`min_track_width` (§4.1) and net membership. Track *paths* are covered by `render`, not by
the model. If KiCad ever adds a track count to `stats`, it belongs in `counts`.

---

## 8. Runner integration (for the implementing agent)

### 8.1 Verb table

| Verb | `kicad-cli` invocation (adapter fills `<out>`) | Comparison |
|---|---|---|
| `version` | `version --format plain` | — (identity record) |
| `parse-pcb` / `parse-sch` | `pcb\|sch upgrade --force` on a scratch copy | exit only |
| `parse-sym` / `parse-fp` | `sym\|fp upgrade --force -o <out> <in>` | exit only |
| `model` | composes `stats`+`pos`+`ipcd356` (pcb) or `netlist` (sch) | `model.json` |
| `drc` / `erc` | `pcb drc` / `sch erc --format json --severity-all -o <out>/…` | `drc.json` / `erc.json` |
| `netlist` | `sch export netlist --format kicadsexpr\|kicadxml -o <out>/netlist.net` | `netlist.json` |
| `pos` | `pcb export pos --format csv --side both --units mm -o <out>/pos.csv` | `pos.json` |
| `ipcd356` | `pcb export ipcd356 -o <out>/board.d356` | `ipcd356.json` |
| `stats` | `pcb export stats --format json -o <out>/stats.json` | `stats.json` |
| `render` | `pcb\|sch\|sym\|fp export svg` (+ `--layers` from `args` for pcb) | `*.svg`, byte-exact after `<title>`/`<desc>` normalization |
| `export-gerbers` / `export-drill` | `pcb export gerbers\|drill …` | **exit only** — no comparator exists (§7.1/§7.2) |
| `export-step` | — | reserved, unused ([DL-0012]) |

Deleted verbs: `upgrade` (its only purpose was the byte compare), `bom` (a BOM is the
schematic model's `components` section by another name), and the four `export-svg-*`
variants (folded into `render`). Renamed: `export-pos` → `pos`, `export-stats` → `stats`,
`export-ipcd356` → `ipcd356`.

### 8.2 Where `model` lives in the runner

- **`runner/model.py`** (new) — `build_board_model(stats_json, pos_csv, d356_text)` and
  `build_schematic_model(netlist_text, fmt)`. The existing `reduce_stats` / `reduce_pos` /
  `reduce_ipcd356` / `reduce_netlist` / `reduce_netlist_kicadxml` in `runner/reduce.py`
  are the raw parsers these compose; keep them (the standalone projections still use them)
  and drop from `reduce_stats` everything §4.1 excludes.
- **`runner/adapters/kicad.py`** — one new `cmd_model` that runs the two or three
  `kicad-cli` exports into the same scratch `--out` dir and writes `<out>/model.json`
  itself. Composition happens **in the adapter**, so a non-KiCad implementation can emit
  its model directly without imitating three KiCad exports.
- **`runner/engine.py`** — comparison mode is now chosen by `op`, not by a `compare`
  field: `model`/`drc`/`erc`/`netlist`/`pos`/`ipcd356`/`stats` → JSON equality against the
  expected file; `render` → normalized-SVG byte equality; everything else → exit only.
  Delete `golden-file`/`golden-dir` handling, `_normalized_dir_tree`, and the s-expr /
  gerber / drill / bom normalizers that only served them.

### 8.3 Failure-case machinery is unchanged

`OK`/`REJECT`/`CRASH` classification ([DL-0013]), the positive control, and the
`known_divergence` strict-xfail layer ([DL-0018]) are untouched by this revision.

---

## 9. Decisions

[DL-0022] (composite `model` as the default; single projections opt-in),
[DL-0023] (`golden` → `expected`, `expect` → `outcome`, `compare` deleted),
[DL-0024] (the byte-comparison layer deleted, and the gerber/drill gap that follows).
Earlier: [DL-0020] (no structural gerber reduction), [DL-0021] (SVG method).
