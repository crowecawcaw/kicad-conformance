# Case format

## Directory layout

```
suites/<suite>/<case>/
├── case.toml            # manifest
├── <input file>          # named by case.toml's `input`
├── control.kicad_pcb     # rejection cases only: a defect-free sibling
├── expected/
│   └── <kicad-version>/  # recorded answers, e.g. 10.0.5/
└── perturb/<slug>/       # optional: overlay proving the case can fail
```

Every file and directory in the case directory besides `case.toml`, `expected/`
and `perturb/` is copied alongside the input into the scratch directory the
adapter runs against. That covers hierarchical schematic sub-sheets, a board's
`.kicad_dru`/`.kicad_pro` siblings, and a rejection case's `control` fixture —
none of these are declared anywhere in the manifest.

## `case.toml` keys

| Key | Meaning |
|---|---|
| `concept` | Required. One sentence: what this case asserts. |
| `doc` | Optional. The upstream KiCad doc anchor the concept comes from. |
| `input` | Required. Filename of the entry file inside the case directory. |
| `extra` | Optional list. Opt-in answers: `"drc"`, `"erc"`, `"refill"`, `"roundtrip"`. |
| `control` | Optional. Names a defect-free sibling fixture; setting it makes the case a rejection case. |
| `error_contains` | Optional, rejection cases only. Substring stderr must contain. |
| `xfail` | Optional. A divergence id (e.g. `"DIV-0004"`) declaring the oracle is known to be wrong here. |

Any other key is a hard error at load time — there is no separate lint step.

## Answers per input type

Every answer is produced by running `kicad-cli` and normalizing the result (see
below). Nothing is synthesized. Exactly one answer — `refill` — is a *reduction* of what
the tool wrote rather than the whole artifact; it is the only one, and it justifies itself
below.

### `.kicad_pcb` (board)

| Answer | Produced by |
|---|---|
| `stats.json` | `kicad-cli pcb export stats --format json -o stats.json BOARD` |
| `pos.csv` | `kicad-cli pcb export pos --format csv --side both --units mm -o pos.csv BOARD` |
| `ipcd356.d356` | `kicad-cli pcb export ipcd356 -o ipcd356.d356 BOARD` |
| `render-F_Cu.svg` | `kicad-cli pcb export svg --layers F.Cu --page-size-mode 2 --exclude-drawing-sheet --black-and-white -o render.svg BOARD` |
| `gerbers/` | `kicad-cli pcb export gerbers -o <dir>/ BOARD` (directory compared) |
| `drill/` | `kicad-cli pcb export drill -o <dir>/ BOARD` (directory compared) |

### `.kicad_sch` (schematic)

| Answer | Produced by |
|---|---|
| `netlist.net` | `kicad-cli sch export netlist --format kicadsexpr -o netlist.net ROOT_SHEET` |
| `render.svg` | `kicad-cli sch export svg --exclude-drawing-sheet --black-and-white -o <dir>/ ROOT_SHEET` |

### `.kicad_sym` / `.pretty` (libraries)

| Answer | Produced by |
|---|---|
| `render/` | `kicad-cli sym export svg` / `kicad-cli fp export svg` (`--black-and-white -o <dir>/ LIB`) |

### `extra` (opt-in)

| `extra` value | Answer | Produced by |
|---|---|---|
| `drc` | `drc.json` | `kicad-cli pcb drc --format json --units mm --severity-all -o drc.json BOARD` |
| `erc` | `erc.json` | `kicad-cli sch erc --format json --severity-all -o erc.json ROOT_SHEET` |
| `refill` | `zone-fills.txt` | `kicad-cli pcb drc --refill-zones --save-board BOARD`, projected — see below |
| `roundtrip` | none | see below |

`roundtrip` is a pure invariant with no committed answer: the adapter exports the
fixture into `<out>/original/`, re-serializes a scratch copy with `pcb upgrade
--force` / `sch upgrade --force`, exports that into `<out>/roundtripped/`, and the
runner asserts the two are equal after normalization.

### `refill`: computed zone fills

`refill` is the only answer about geometry the tool **computes** rather than geometry the
input already carries. Every other board answer reads the `(filled_polygon …)` blocks
stored in the file — `render-F_Cu.svg` and the gerbers plot exactly what is there — so a
case can pin what a fill *looks like* only if its author pasted one in, and nothing
exercises the fill engine. `pcb drc --refill-zones --save-board` recomputes every zone's
fill from the zone outline, the copper around it, and the zone's own clearance / thermal /
`min_thickness` settings, and writes the result back into a scratch copy of the board.

Boards only: `refill` on a schematic or a library input is an adapter error.

The adapter hands the whole refilled board back to the runner. What is **recorded** is a
projection of it — the fill geometry alone:

```
zones 1
zone 0 net "GND" layers "F.Cu"
  fill 0 layer "F.Cu" points 67
    xy 17.943039 2.019685
    xy 17.988794 2.072489
    …
```

Zones appear in document order (the writer's own order is part of the answer), wherever
in the file they sit — a zone nested inside a footprint is projected like any other. Each
zone line carries its net and its declared layer(s); each `(filled_polygon …)` carries its
layer, its vertex count, any flag it holds (`island`, …) and every vertex, as the exact
tokens the writer emitted — never reparsed as floats, so the answer pins geometry to the
last digit. A zone that came back with no fill records `no fill`; a rule area, which
carries no net at all, records the bare word `none`, which can never collide with a real
value because a real value keeps its quotes. Everything else in the board — footprints,
tracks, `setup`, uuids, the zone's authored outline — is dropped.

Why a projection rather than the refilled board file:

- **It fails for the right reason.** A whole board file also pins the *serializer*:
  indentation, block order, how many digits an unrelated coordinate keeps. An
  implementation with a correct fill engine and a different writer would fail this answer
  for a reason that has nothing to do with fills — and write-path questions already have
  their own answer, `roundtrip`.
- **The answer stays about one thing.** A refilled board restates every footprint, pad and
  setting the input already declares; a one-vertex regression then hides inside a file that
  is mostly a copy of the input.
- **It reads as a diff.** One vertex per line means a failure names the vertices that
  moved, not "line 214 of a board file differs".
- **The whole board is not byte-stable and the fill is** — see the determinism note below.

The projection lives in `runner/normalize.py`, not in the adapter, and is keyed on the
board suffix like every other normalizer. That is deliberate: the *suite* owns the
reduction, so every implementation is judged by the same one, and an implementation under
test only has to hand back a refilled board in KiCad's board format.

**Determinism.** Measured by refilling four different zone fixtures twelve times each (48
refills, each a fresh `kicad-cli` process on a fresh copy of the input) and hashing both
the returned board and its projection:

| Fixture shape | distinct board files | distinct projections |
|---|---|---|
| footprints carrying their `Reference`/`Value`/… property blocks | 1 of 12 | 1 of 12 |
| footprints written without those property blocks | **12 of 12** | 1 of 12 |

The fill geometry itself never moved: one projection, byte-identical across all twelve
runs, on all four fixtures. The board file is not byte-stable, for a reason that has
nothing to do with fills — a footprint whose mandatory properties the input omitted gets
them back with a freshly-minted uuid on every save, and both fixture shapes are legal
input. So "record the refilled board" would have needed a uuid redaction it does not need
as a projection, and would have been recording that redaction's correctness alongside the
geometry. `--determinism-check` reports the second row's shape for `refill` as *raw output
DIFFERED across runs but normalized output matched*.

## Rejection cases

A case with `control` set records no answers. It runs the input type's loader
probe (`pcb export stats`, `sch upgrade --force`, `sym upgrade --force`, or `fp
upgrade --force`) for exit polarity only: the input must be rejected, and
`error_contains` must match a substring of stderr. The `control` fixture — a
defect-free sibling — must be accepted; a rejection case that can't pass a good
input isn't evidence. A crash (signal termination, or exit code > 128) is never a
pass, on either the input or the control.

## `xfail`

`xfail = "DIV-NNNN"` declares the reference oracle itself is known to be wrong for
this case. On a happy case it scopes to the `roundtrip` answer only — a failing
round-trip there is XFAIL (green); every other answer on the case is still scored
normally. On a rejection case it means the oracle crashes instead of rejecting
cleanly, and that crash is XFAIL. Either way a clean result is XPASS and fails the
build, so a fixed upstream divergence can't silently rot in the manifest.

## Normalization

Three redactions cover every structured text answer: ISO timestamps, UUID-shaped
tokens, and the directory part of an embedded scratch-file path (basename kept).
`drc.json`/`erc.json` additionally get their `items[]` lists sorted by content,
since KiCad's own iteration order for those is not stable run to run — everything
else in every answer is kept byte-for-byte. The one exception is the board file the
`refill` answer produces, which is projected down to its zone-fill geometry (above)
rather than redacted. `runner/normalize.py` is the reference implementation; a consumer
comparing its own output against a committed answer should apply the same redactions —
and, for `refill`, the same projection — before diffing.

## Perturbations

A case may carry `perturb/<slug>/` — a modified copy of the input, named exactly
like the file it replaces — proving the case is falsifiable. `scripts/run.sh
--verify-assertions` runs each perturbation and checks it moves at least one
recorded answer away from its committed value; a perturbation that changes
nothing the case checks reports `INERT`. Rejection cases don't carry `perturb/`;
their `control` fixture already serves this purpose.

## Version parametricity

Recorded answers live under `expected/<kicad-version>/`. A new KiCad version is
added as a sibling directory, produced with `scripts/run.sh --regenerate`; nothing
about the case format changes.
