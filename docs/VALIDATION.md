# Validation — the comparator ladder (L0–L3)

How kicad-conformance decides "did the tool get it *right*", beyond the exit-code
polarity M0 shipped. Companion docs: [`DESIGN.md`](DESIGN.md) (architecture, esp. §2 verbs
and §3 comparison model), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) (`case.toml`),
[`DECISIONS.md`](DECISIONS.md) (DL-0019–DL-0021 ratify what this doc specifies).

Every empirical claim below was produced against **`kicad-cli` 10.0.5, release build**
(`kicad/kicad:10.0.5` Docker image, `wxWidgets 3.2.8`, `FreeType 2.13.3`) on a small
hand-authored populated board (`R_0805` SMD + `C_THT` through-hole, one track, one via,
an `Edge.Cuts` outline), upgraded to the current format via `kicad-cli pcb upgrade
--force` so it is a genuine KiCad artifact. The exact commands are shown inline.

---

## 1. The reframe — validate a parser by its projections

M0 validates a parser by **exit polarity**: did `kicad-cli` load the file (exit 0) or
reject it (graceful non-zero), and — for `failure/` cases — did it reject for a provable
reason (positive control, [DL-0013]). That answers *did it parse*. It does not answer
*did it parse into the **right model***: a tool can load a board, exit 0, and still have
mis-assigned a pad to the wrong net, dropped a via, or placed a footprint at the wrong
angle.

The richer question is answered without ever reaching into the tool's internal data
structures — which would break the language-agnostic subprocess contract ([DL-0007]).
Instead we lean on a property KiCad already gives us: **a parsed model can be projected
back out through many independent export verbs.** One board fixture yields a stats
summary, a placement file, a board-side netlist, a per-layer SVG, gerbers, drill data —
each an independently-derived *view* of the same parsed model. If the model is right,
every projection is right; if a projection is wrong, the model was wrong in a way that
projection is sensitive to. So:

> **One fixture → many projections; each projection is an independent comparator.** A
> projection is "fair" across implementations to the degree it captures *meaning* rather
> than *KiCad's byte-formatting*.

### The comparator ladder

| Ladder | What it compares | Derived from | Cross-impl fairness |
|---|---|---|---|
| **L0 — exit** | success/failure polarity (+ stderr substring) | the process exit code | **fully portable** — every tool exits (DESIGN §3a) |
| **L1 — canonical serialize** | KiCad's exact re-serialized bytes (`… upgrade`, gerbers, drill, bom) after normalization | the tool's own writer | **KiCad-regression only** — over-fits a second tool's formatting ([DL-0015]) |
| **L2 — semantic extraction** | a normalized *structured* projection: connectivity, counts, geometry, placement | an interchange export (`netlist`, `stats`, `pos`, `ipcd356`, `erc`, `drc`) | **portable** — meaning, not bytes; the cross-adapter conformance signal |
| **L3 — vector render** | the drawn geometry of a layer/sheet/symbol/footprint | `… export svg` | **portable-with-a-caveat** — visual meaning; exact for KiCad-vs-KiCad, justified threshold cross-impl |

Two axes, not one. **Richness** (how much meaning is captured) climbs L0 → L3.
**Cross-impl fairness** is *not* monotonic: L1 is the outlier — it is the strongest signal
for *KiCad-vs-KiCad regression* and the weakest for a second implementation, exactly as
[DL-0015] already argued for byte goldens. L0, L2, L3 all port; L1 does not. This doc adds
**L2** (interchange projections — the existing `structured` mode already contains its
embryo in `reduce.py`) and **L3** (SVG render) as first-class alongside the L0/L1 M0 shipped.

L2 is the load-bearing addition: it is where "the pad is on the wrong net" or "the
footprint is rotated 90° wrong" becomes a *comparable value* that ports to a clean-room
adapter, without a byte-golden's formatting over-fit.

---

## 2. Per-suite comparator spec

For each suite: what's under test, the L2 projection(s) with the exact `kicad-cli`
command, the L3 (visual) projection where one exists, and the comparator + its
cross-impl fairness. `export-pos` already exists as a verb (L1 `golden-file` today); this
doc promotes its **L2** reduction. New verbs are marked ⁺.

| Suite | Under test | L2 projection → `kicad-cli` | L3 (visual) | Comparator · fairness |
|---|---|---|---|---|
| **schematic-parse** | `.kicad_sch` loads into the right model | net→node graph → `sch export netlist` | `sch export svg` | L0 exit; L1 `upgrade` byte-golden (KiCad-regr.); **L2** netlist `structured`; **L3** sheet SVG. L2/L3 portable |
| **board-parse** | `.kicad_pcb` loads into the right model | counts/areas → `pcb export stats`⁺; placement → `pcb export pos`; board net graph → `pcb export ipcd356`⁺ | `pcb export svg` (per layer)⁺ | L0/L1 as above; **L2** stats+pos+ipcd356 `structured`; **L3** copper/silk SVG. L2/L3 portable |
| **symbol-lib** | `.kicad_sym` pins/geometry | pin inventory (number, name, position, type) reduced from the upgraded `.kicad_sym` s-expr | `sym export svg`⁺ | L0 exit; L1 `sym upgrade` byte-golden; **L2** pin-inventory `structured`; **L3** symbol SVG |
| **footprint-lib** | `.pretty` pads/geometry | pad inventory (number, type, layers, at, drill) reduced from the upgraded `.kicad_mod` | `fp export svg`⁺ | L0 exit; L1 `fp upgrade` byte-golden; **L2** pad-inventory `structured`; **L3** footprint SVG |
| **erc** | electrical-rules findings | violation set → `sch erc --format json` | — | L2 `structured` (exists). Portable |
| **drc** | design-rules findings | violation set → `pcb drc --format json` | — | L2 `structured` (exists). Portable |
| **netlist** | connectivity | net→node graph → `sch export netlist` (`kicadsexpr` **or** `kicadxml`) | — | L2 `structured` (exists; cross-format §3.1). Portable |
| **gerber** | fab copper output | *none as RS-274X reduction* (ruled out, §3.6) — copper **meaning** covered by stats+pos+ipcd356+SVG | `pcb export svg` copper layers | L1 `golden-dir` byte (KiCad-regr.); L2/L3 via the board-parse projections. See [DL-0020] |
| **drill** | fab hole output | hole table → `pcb export stats` `drill_holes[]` + `ipcd356` drill fields | — (drill-map is derivative) | L1 `golden-dir` byte; **L2** drill-table `structured`. Portable at L2 |
| **placement / pos**⁺ | footprint placement | refdes→(x,y,rot,side) → `pcb export pos --format csv` | — | **L2** `structured`, tolerance = printed quantum. Portable |
| **stats**⁺ | board inventory & geometry | field map → `pcb export stats --format json` | — | **L2** `structured`, field/string compare. Portable |
| **render (3D)** | 3D geometry | — deferred, not in scope | `pcb render` PNG/JPEG (3D) | **Skipped** — least deterministic, needs OCC/display ([DL-0012]); 2D SVG (L3) covers the visual need |

---

## 3. L2 reduction schemas

Each L2 extractor emits a **canonical, normalized, JSON-serializable structure**; the
stored golden is that structure (a `*.reduced.json`), never the raw export ([DL-0014]).
At compare time the adapter's output is reduced the same way and checked by **membership
equality** (sets/maps) or **field equality** (printed-quantum strings). All live in
`runner/reduce.py` beside the existing `reduce_drc`/`reduce_erc`/`reduce_netlist`.

### 3.1 netlist graph (extend existing — add cross-format fairness)

`reduce_netlist` today parses `kicadsexpr`. The same net→node graph is recoverable from
`kicadxml`, which proves the graph is a property of the *connectivity*, not of one
serialization. Verified:

```
$ kicad-cli sch export netlist --format kicadsexpr -o n.net s.kicad_sch
    (net (code "1") (name "Net-(U1-Pad1)")
      (node (ref "U1") (pin "1") (pinfunction "1_1") (pintype "passive"))
      (node (ref "U2") (pin "1") (pinfunction "1_1") (pintype "passive")))
$ kicad-cli sch export netlist --format kicadxml -o n.xml s.kicad_sch
    <net code="1" name="Net-(U1-Pad1)" class="Default">
      <node ref="U1" pin="1" pinfunction="1_1" pintype="passive"/>
      <node ref="U2" pin="1" pinfunction="1_1" pintype="passive"/>
```

**Schema (unchanged shape):** `{ net-name : sorted [[refdes, pin], …] }`. `code`,
`class`, `pinfunction`, `pintype` and the `(design …)` header are dropped as before.
**Extension:** add a `kicadxml` reader alongside the s-expr reader so a second adapter may
emit *either* interchange format and be judged on the identical reduced graph — the
membership compare is format-blind. This is the cleanest demonstration that L2 measures
meaning.

### 3.2 DRC / ERC violation set (existing)

Unchanged: sorted list of `{type, severity, items:[{description, pos}]}`, sorted by
content, never by UUID (`reduce_drc`, DESIGN §3b). Kept here for completeness — it is the
canonical L2 template every new reduction below follows.

### 3.3 stats-json (new — field compare)

`pcb export stats --format json` is a compact, richly-diffable inventory. Verified
output (populated board, trimmed):

```
$ kicad-cli pcb export stats --format json -o s.json b.kicad_pcb
{
  "metadata": { "date": "2026-08-03T02:49:38", "generator": "KiCad 10.0.5",
                "project": "b", "board_name": "b" },
  "board": { "has_outline": true, "width": "50.0000 mm", "height": "35.0000 mm",
             "area": "1750.00 mm²", "front_copper_area": "11.899 mm²",
             "min_track_width": "0.2500 mm", "min_drill_diameter": "0.4000 mm",
             "board_thickness": "1.6000 mm", … },
  "pads": { "through_hole": 2, "smd": 2, "npth": 0, … },
  "vias": { "through": 1, "blind": 0, "buried": 0, "micro": 0 },
  "components": { "tht": {…}, "smd": {…}, "unspecified": {"front":1,"back":1,"total":2}, … },
  "drill_holes": [ {"count":2,"shape":"Round","x_size":"0.8000 mm","plated":true,
                    "source":"Pad","start_layer":"F.Cu","stop_layer":"B.Cu"}, … ]
}
```

**Determinism (run twice, 1 s apart):** *only* `metadata.date` differs —

```
$ diff s1.json s2.json
3c3
<     "date": "2026-08-03T02:49:38",
>     "date": "2026-08-03T02:49:39",
```

**Reduction `reduce_stats`:** **drop the entire `metadata` object** — `date` is wall-clock
noise, `generator` is the version string ([DL-0005] keeps the format-version key, not the
app version), and `project`/`board_name` are derived from the *scratch-copy filename* (here
`"b"`) so they leak the adapter's temp name. Keep `board`, `pads`, `vias`, `components`
verbatim, and `drill_holes` as a **content-sorted list** (sort key = all fields) so hole
ordering never matters. **Compare = field/string equality**: every numeric value is a
KiCad-printed fixed-precision string (`"0.2500 mm"`, `"11.899 mm²"`), so string-exact
compare *is* printed-quantum tolerance (DESIGN §3d) — no float parsing, no band. Count
fields (`through_hole: 2`) compare as integers. A footprint dropped on load → wrong
`components.total`; a mis-parsed pad → wrong `pads`; a wrong outline → wrong `board.area`.
Note `min_track_clearance` can be a sentinel (`"2147.4836 mm"` ≈ INT_MAX nm, "only one
segment on the net") — stable, kept as-is; if a fixture makes it wobble it is named-and-
excluded ([DL-0005]), not band-normalized.

### 3.4 pos — placement rows (new — printed-quantum tolerance)

`pcb export pos --format csv` (the **CSV** form, deliberately, because the **ASCII** form
carries a `created on <timestamp>` header the CSV does not). Verified:

```
$ kicad-cli pcb export pos --format csv --side both --units mm -o pos.csv b.kicad_pcb
Ref,Val,Package,PosX,PosY,Rot,Side
"C1","100n","C_THT",40.000000,-30.000000,180.000000,bottom
"R1","1k","R_0805",20.000000,-20.000000,90.000000,top
$ diff pos.csv pos2.csv     # run twice
$                            # byte-identical — CSV has NO timestamp header
```

**Reduction `reduce_pos`:** parse the CSV into
`{ refdes : {"val", "package", "x", "y", "rot", "side"} }`. Rows are keyed by refdes
(unique per board), so CSV row order is irrelevant. **Compare = field equality** with
**tolerance = the printed quantum**: the CSV prints 6 decimal places in mm = **1 nm**, i.e.
KiCad's native integer board unit, so string-exact compare on `x/y/rot` is exactly-1-nm and
nothing wider — a genuine placement error cannot hide (DESIGN §3d, no pre-authorized band).
`val`/`package`/`side` compare as strings. (Note KiCad's pos Y is board-Y-flipped —
`20 mm` → `-20.000000`; that is KiCad's fab convention, identical on both compare sides, so
it is invisible to the compare.) `pos` needs **no** normalizer for the CSV form — honoring
the honesty rule (DESIGN §4): "add no normalizer where output is byte-stable."

### 3.5 ipcd356 — board-side net graph (new)

`pcb export ipcd356` is the **board's own** netlist + test-point geometry (as opposed to
the *schematic's* netlist from §3.1) — a second, independent connectivity projection.
Verified, byte-identical run-to-run (no timestamp):

```
$ kicad-cli pcb export ipcd356 -o board.d356 b.kicad_pcb
P  UNITS CUST 0
317NET1             VIA        MD0157PA00X+015748Y-007874X0315Y0000R000S3
327NET1             R1    -1          A01X+007874Y-008248X0394Y0551R270S2
327GND              R1    -2          A01X+007874Y-007500X0394Y0551R270S2
317GND              C1    -1    D0315PA00X+016732Y-011811X0630Y0000R000S0
317NET1             C1    -2    D0315PA00X+014764Y-011811X0630Y0000R000S0
999
$ diff board.d356 board2.d356    # byte-identical run-to-run
```

Record types: `317` = through-hole/via feature (has a drill `MD…`/`D…`), `327` = SMD
feature; columns carry the **net name**, **refdes + pad** (or `VIA`), the **access layer**
(`A00` = both, `A01` = top), **X/Y** in 0.0001-inch units, pad size, rotation, and a
trailing `S…` code.

**Reduction `reduce_ipcd356`:** two membership structures.
1. **Board net graph** — `{ net-name : sorted set of (refdes, pad) }`, mirroring the
   schematic netlist's shape so the two are directly comparable (a board that routes a pad
   to the wrong net diverges here even if the schematic netlist is right). `VIA` features
   contribute the net but no `(refdes,pad)` member.
2. **Test-point geometry (optional, tolerance-compared)** — `{ (refdes, pad) : (x, y,
   access-layer) }` in the printed 0.1-mil quantum, for cases that assert access-point
   placement.

**Normalizer:** DESIGN §4 already anticipates "IPC-D-356 trailing `S…` serial on VIA
records only." On this board the VIA carried `S3` and was stable run-to-run; because the
reduction keys on **net + refdes/pad membership** and drops the raw `S…`/rotation tail
entirely, the reduction is robust whether or not that serial is stable on a given board —
the graph shape is what ports to a second adapter.

### 3.6 gerber geometry — **ruled out as a structural reduction** ([DL-0020])

**Decision: do NOT build an RS-274X structural reduction** (apertures + flashes/draws with
per-layer coordinates). Board copper *meaning* is instead covered by the composition of
**stats** (copper areas, track/via/pad counts, min widths — §3.3) + **pos** (placement —
§3.4) + **ipcd356** (net-to-pad connectivity + access-point geometry — §3.5) + **SVG**
(the actual drawn copper geometry, L3 — §4).

**Why.** A faithful RS-274X reducer is disproportionately hard for the marginal value: it
must implement a Gerber *interpreter* (aperture macros/`AM`, `%LP` dark/clear polarity,
`G36/G37` region fills, arc `G02/G03` interpolation, step-and-repeat `SR`, coordinate-
format `%FS`), then canonicalize a *rendered flash/draw set* that two conformant plotters
may legitimately decompose differently (a pad as one flashed aperture vs. an outline region;
a track as a draw vs. a thin region). That is a second rasterizer's worth of engineering,
and its cross-impl output is exactly as formatting-sensitive as the L1 byte golden it was
meant to improve on. The three semantic projections above already localize every copper
defect that matters (wrong count, wrong net, wrong placement, wrong area), and L3-SVG
captures the drawn geometry directly and *fairly* (a rendered raster is decomposition-
blind). **Tradeoff, stated honestly:** we lose a *native-Gerber* semantic check — a bug
that corrupts RS-274X output while leaving the `.kicad_pcb` model intact (e.g. a plotter-
only aperture bug) is caught only by the **L1 byte golden** (a KiCad-regression signal, not
cross-impl) and by L3-SVG, not by a portable Gerber-native L2. Given gerber is a *fab
output* verb whose cross-impl story [DL-0015] already scopes to "semantic subset," this is
an acceptable, documented gap; the door stays open to add a Gerber reducer later if a
concrete second-adapter need appears.

---

## 4. L3 — SVG render comparison

### 4.1 What ships in the image (probed, not assumed)

```
$ for t in rsvg-convert resvg inkscape cairosvg convert magick dvisvgm pdftocairo gs; \
      do printf "%s: " $t; command -v $t || echo MISSING; done
rsvg-convert: MISSING   resvg: MISSING   inkscape: MISSING   cairosvg: MISSING
convert: MISSING        magick: MISSING  dvisvgm: MISSING     pdftocairo: MISSING
gs: /usr/bin/gs         # ghostscript 10.05.1 — PostScript/PDF only, NOT an SVG rasterizer
$ python3 -c "import cairosvg" → ModuleNotFoundError; import cairo → ModuleNotFoundError
$ python3 -c "import PIL; print(PIL.__version__)" → 11.1.0   # present, but cannot rasterize SVG
```

**Finding: the `kicad/kicad:10.0.5` image ships NO SVG rasterizer.** Only ghostscript
(PS/PDF, not SVG) and Pillow (raster ops, not an SVG engine) are present. So a rasterizing
L3 needs a rasterizer *added* to the container/CI. But the probe of KiCad's SVG itself
reshapes the whole design:

### 4.2 KiCad's SVG is deterministic modulo one line

```
$ kicad-cli pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet \
      -o fcu.svg b.kicad_pcb
$ diff fcu.svg fcu2.svg      # run twice
11c11
< <title>SVG Image created as fcu.svg date 2026-08-03T02:50:41 </title>
> <title>SVG Image created as fcu2.svg date 2026-08-03T02:50:43 </title>
```

The **only** run-to-run difference is the `<title>` (filename + wall-clock date). The path
geometry (`d="M 9.5500,11.4500 …"`, coordinates in mm to 4 decimals) and all fills are
byte-stable. That single fact lets L3 avoid rasterization entirely *for the KiCad-vs-KiCad
case*.

### 4.3 The chosen method — a hybrid

**(a) KiCad-vs-KiCad regression → normalized-SVG structural exact match (no rasterizer).**
Normalize the `<title>` line (and the `<desc>`) to a constant, then compare the SVG
**byte-exact after normalization**, exactly like an L1 text golden but over the vector
drawing. It is fully deterministic (§4.2), needs no added dependency, and — per the
project's "no pre-authorized tolerance bands" principle — is **exact-match, zero
tolerance**. This is the default L3 comparator and the one the M0-style regression build
uses. Determinism is pinned at the source: fixed `--black-and-white` (removes theme-color
dependence), `--page-size-mode 2` (board-area only, so page size can't drift),
`--exclude-drawing-sheet`, and the existing `LC_ALL=C.UTF-8`/`TZ=UTC` (number formatting).

**(b) Cross-implementation → rasterize both, perceptual diff.** A clean-room adapter emits
*valid-but-differently-structured* SVG (arcs vs. polyline approximations, different element
order/grouping, different path decomposition) — structural exact-match would over-fit it
exactly as an L1 byte golden does ([DL-0015]). So for the second-adapter path, **rasterize
both SVGs to PNG with a single pinned deterministic renderer and pixel/perceptual-diff.**

Recommended renderer: **`resvg`** (the Rust `resvg`/`usvg` CLI), pinned by exact version and
added to the CI image (and/or the container). Rationale for `resvg` over the alternatives:
it is a single static binary (no cairo/Qt/GTK system-library variance), renders on the CPU
deterministically, ships its own font handling (no system-font drift), and is reproducible
across machines — the properties CI determinism needs. `rsvg-convert`/Inkscape/cairosvg all
pull in system libraries (cairo, pango, fontconfig) whose versions and installed fonts
change the pixels; ghostscript cannot read SVG at all. The renderer is invoked at a **fixed
DPI, white opaque background, and no anti-aliasing variance** (documented flags), so its
output is itself reproducible.

**Why not rasterize KiCad-vs-KiCad too?** We can, but we don't need to: §4.2 shows KiCad's
SVG is already exact, and an exact vector compare is *stronger and cheaper* than a raster
compare (no renderer in the loop, no threshold). Rasterization is reserved for the case that
genuinely needs it — comparing two different tools' drawings.

### 4.4 Tolerance policy — reconciled with "no pre-authorized bands"

- **KiCad-vs-KiCad (a):** **exact**, zero tolerance. Normalized-SVG byte compare; a
  single differing pixel of geometry is a diff. There is no band to hide a bug in.
- **Cross-impl (b):** an **explicit, per-comparator, documented threshold**, never a silent
  global band. The threshold lives in the `case.toml` (e.g. `max_diff_ratio = 0.001`),
  must cite *why* that value (which legitimate rendering difference it tolerates — e.g.
  sub-pixel arc-tessellation at the pinned DPI), and must be shown **load-bearing**: the
  determinism/falsifiability test perturbs the geometry (shift a pad by one quantum) and
  requires the comparator to go **red**, proving the threshold cannot swallow a real error.
  A threshold that never triggers a red is dead and is removed — same rule as a normalizer
  (DESIGN §4a). This is the [DL-0015] cross-impl-fairness carve-out made concrete for
  pixels: exact where we can, an *audited* threshold only where cross-tool rendering
  legitimately differs.

### 4.5 The diff report

On an L3 failure the runner writes, next to the scratch output: (1) the **normalized
candidate SVG** and the **reference SVG** (for `(a)`, a textual diff pinpoints the changed
path); (2) for `(b)`, the two **rendered PNGs**, a **diff image** (per-pixel XOR/highlight
of changed regions), the **% of pixels differing**, and an **SSIM** score. The report names
*where* on the board the drawing diverges, so a mismatch is actionable, not "images
differ."

---

## 5. Runner integration

### 5.1 New / promoted adapter verbs (real `kicad-cli` mappings, explicit `-o`)

Following DESIGN §2a (the runner always dictates an explicit output path; the adapter
scratch-copies the input first). 3D `render` is **skipped** ([DL-0012]).

| Verb | `kicad-cli` command (adapter fills `<out>`) | Artifact the runner reads |
|---|---|---|
| `export-stats`⁺ | `pcb export stats --format json -o <out>/stats.json <in>` | `<out>/stats.json` |
| `export-ipcd356`⁺ | `pcb export ipcd356 -o <out>/board.d356 <in>` | `<out>/board.d356` |
| `export-pos` (promote) | `pcb export pos --format csv --side both --units mm -o <out>/pos.csv <in>` | `<out>/pos.csv` (now L2-reducible) |
| `export-svg-pcb`⁺ | `pcb export svg --layers <L> --page-size-mode 2 --exclude-drawing-sheet --black-and-white -o <out>/render.svg <in>` | `<out>/render.svg` |
| `export-svg-sch`⁺ | `sch export svg --no-background-color -o <out>/ <in>` (writes `<stem>.svg`) | `<out>/<stem>.svg` |
| `export-svg-sym`⁺ | `sym export svg --black-and-white -o <out>/ <in>` | `<out>/<sym>.svg` |
| `export-svg-fp`⁺ | `fp export svg --black-and-white -o <out>/ <in>` | `<out>/<fp>.svg` |

`export-svg-*` share one adapter helper differing only in the `pcb|sch|sym|fp` subcommand
and the layer/theme flags; the SVG `--layers` set is a per-case `args` parameter (like the
gerber layer set, DESIGN §2b). Add the four `export-svg-*` (or a single `export-svg` that
dispatches on input suffix), `export-stats`, and `export-ipcd356` to `runner/verbs.py`
`VERB_TABLE`/`IMPLEMENTED_VERBS`, and a resolver branch in `engine._resolve_artifact`.

### 5.2 New / extended `compare` modes

- **`structured`** — extended, not replaced. Add `reduce_stats`, `reduce_pos`,
  `reduce_ipcd356` to `runner/reduce.py`, and dispatch them in
  `engine._reduce_structured` for ops `export-stats`/`export-pos`/`export-ipcd356` (and the
  `netlist` `kicadxml` reader). The golden stays a stored `*.reduced.json` ([DL-0014]);
  the existing membership/field machinery and `describe_structured_mismatch` bucketing are
  reused unchanged.
- **`image`** (new, L3) — normalize the SVG (strip `<title>`/`<desc>`), then: mode `(a)`
  compare the normalized SVG **byte-exact** against a stored reference SVG (default); mode
  `(b)` rasterize both via the pinned `resvg` and pixel/SSIM-diff under a declared,
  load-bearing threshold (§4.4). Golden is the reference SVG (mode a) or reference PNG
  (mode b), stored per KiCad version. Add an `image`/`svg-image` branch to
  `engine._compare_rich_output` and an `svg` normalizer to `runner/normalize.py`
  (`_SVG_TITLE_RE`, `_SVG_DESC_RE` → constant), exercised by the determinism self-test.

### 5.3 Example `case.toml` snippets

**Board stats-json (L2 field compare):**
```toml
concept = "A populated two-layer board reports the correct pad/via/component inventory."
doc     = "cli:pcb-export-stats"
input   = "board.kicad_pcb"

[[check]]
op      = "export-stats"
expect  = "ok"
compare = "structured"          # reduce_stats: drop metadata, field/string compare
golden  = "stats.reduced.json"  # stored canonical reduction, NOT the raw stats json
```

**Placement / pos (L2, printed-quantum tolerance):**
```toml
concept = "Footprint placement (x, y, rotation, side) is recovered exactly to the nm quantum."
doc     = "cli:pcb-export-pos"
input   = "board.kicad_pcb"

[[check]]
op      = "export-pos"
expect  = "ok"
compare = "structured"          # reduce_pos: refdes -> (x,y,rot,side); string-exact = 1 nm
golden  = "pos.reduced.json"
```

**PCB copper SVG (L3 render):**
```toml
concept = "The front-copper layer renders to the expected vector geometry."
doc     = "cli:pcb-export-svg"
input   = "board.kicad_pcb"

[[check]]
op      = "export-svg-pcb"
expect  = "ok"
compare = "image"               # (a) normalized-SVG exact for KiCad-vs-KiCad
golden  = "render-F_Cu.svg"     # reference SVG, per KiCad version
args    = ["--layers", "F.Cu"]  # per-case layer selection (DESIGN §2b)
```

---

## 6. Cross-implementation semantics

Restating [DL-0015] for the ladder: **L2 and L3 are the conformance signal for a second
(non-KiCad) adapter; L1 byte-goldens remain a KiCad-version-regression signal.** A
clean-room engine that parses correctly but formats its s-expr/gerbers/SVG differently must
*not* light up the divergence ledger with formatting non-findings — it is judged on the
portable subset (L0 exit + L2 reductions + L3 render), and an L1 diff that reduces to an
identical L2/L3 projection is auto-classified formatting-only and kept out of the ledger.

**How goldens work per layer:**
- **L2** — the golden is the **stored canonical reduction** (`stats.reduced.json`,
  `pos.reduced.json`, `board-net.reduced.json`, the net map, the DRC/ERC set), committed
  under `golden/<version>/`, regenerated in the Docker Linux image ([DL-0016]). A second
  adapter's output is reduced the same way and compared by membership/field. The reduction
  *is* the contract shape a second adapter must satisfy.
- **L3** — the golden is the **stored reference render**, per KiCad version: the normalized
  reference **SVG** for mode `(a)` (KiCad-vs-KiCad exact), and/or a reference **PNG** for
  mode `(b)` (cross-impl raster diff). Because both the KiCad SVG (§4.2) and the pinned
  `resvg` raster (§4.3) are deterministic, the reference is stable to regenerate and review;
  it re-baselines on a KiCad version bump exactly like every other golden (DESIGN §5).

---

## 7. Decisions

Ratified in [`DECISIONS.md`](DECISIONS.md): **[DL-0019]** (L2/L3 as first-class comparators),
**[DL-0020]** (gerber-geometry ruled out; board copper covered by stats+pos+ipcd356+SVG),
**[DL-0021]** (SVG rasterization method — hybrid normalized-SVG-exact + pinned-`resvg`
cross-impl raster — and the tolerance policy).

---

## 8. Build plan (for the implementing agent)

Ordered **highest-value / lowest-effort first**. Each step lands a verb + a reduction (or
compare mode) + at least one worked case + its Docker-regenerated golden, green in the
`kicad/kicad:10.0.5` CI job before moving on. All goldens regenerated **inside the Docker
Linux image** ([DL-0016]).

1. **stats-json (L2)** — *highest value, lowest effort; no new dependency.*
   - Verb `export-stats` in `verbs.py` + adapter `cmd_export_stats` + `_resolve_artifact`
     branch.
   - `reduce_stats(raw)` in `reduce.py`: drop `metadata`; keep `board`/`pads`/`vias`/
     `components`; content-sort `drill_holes`. Wire into `engine._reduce_structured`.
   - Case: `suites/board-parse/happy/0002-populated-board-stats/` (reuse a populated board
     fixture — seed-and-`upgrade` per [DL-0011]) with the §5.3 stats snippet.
   - Golden: `golden/10.0.5/stats.reduced.json` via `--regenerate` in Docker.
   - Falsifiability: bump a pad/footprint in the fixture, watch `components`/`pads` go red.

2. **pos (L2)** — *promote the existing verb; tiny reduction.*
   - `reduce_pos(csv_text)` → `{refdes: {val,package,x,y,rot,side}}`; dispatch `export-pos`
     to `structured`. (Keep the L1 `golden-file` path available for KiCad-regression.)
   - Case: `suites/placement/happy/0001-two-footprint-placement/` (new `placement` suite,
     or under `board-parse`) with the §5.3 pos snippet.
   - Golden: `pos.reduced.json`. Falsifiability: rotate a footprint 90° in the fixture →
     `rot` mismatch red.

3. **ipcd356 (L2)** — *second, independent connectivity projection.*
   - Verb `export-ipcd356` + adapter mapping (`pcb export ipcd356 -o <out>/board.d356`).
   - `reduce_ipcd356(text)` → board net graph `{net: sorted[(refdes,pad)]}` (+ optional
     test-point geometry map). Wire into `_reduce_structured`.
   - Case: `suites/board-parse/happy/0003-board-net-graph/` asserting the board net graph
     matches the schematic netlist's shape. Golden: `board-net.reduced.json`.
   - Falsifiability: move a pad to another net in the fixture → membership red.

4. **svg-image (L3)** — *needs a normalizer + a new compare mode; raster path deferred to
   cross-impl.*
   - Verbs `export-svg-pcb`/`-sch`/`-sym`/`-fp` (or one `export-svg` dispatching on suffix)
     with the §5.1 pinned flags.
   - `svg` normalizer in `normalize.py` (strip `<title>`/`<desc>`), added to the determinism
     self-test. New `image` compare mode in `engine._compare_rich_output`: mode `(a)`
     normalized-SVG byte-exact vs. a stored reference SVG.
   - Case: `suites/board-parse/happy/0004-fcu-render/` with the §5.3 svg snippet; golden
     `render-F_Cu.svg`. One `sch export svg` case likewise.
   - Cross-impl raster mode `(b)`: pin `resvg` (exact version) into the CI image, add the
     rasterize+SSIM/pixel-diff path and the load-bearing per-case threshold — needed only
     when the **second adapter** (M7) lands, so schedule it there, not in the KiCad-only
     pass.

5. **gerber-geometry** — *nothing to build* (ruled out, §3.6 / [DL-0020]). Board copper is
   covered by steps 1–4 (stats + pos + ipcd356 + SVG) plus the existing L1 gerber
   `golden-dir`. Record the decision; revisit only if a concrete second-adapter Gerber-
   native need appears.

**Suggested landing order in the roadmap:** steps 1–3 slot naturally beside M2/M3 (they
are `structured` reductions like netlist/DRC); step 4a beside M5 (library SVG); step 4b with
M7 (second adapter). Symbol/footprint L2 pin/pad inventories (§2) follow the same
`structured` template and can piggyback on M5.
