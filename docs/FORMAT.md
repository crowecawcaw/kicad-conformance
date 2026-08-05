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
| `extra` | Optional list. Opt-in answers: `"drc"`, `"erc"`, `"roundtrip"`. |
| `control` | Optional. Names a defect-free sibling fixture; setting it makes the case a rejection case. |
| `error_contains` | Optional, rejection cases only. Substring stderr must contain. |
| `xfail` | Optional. A divergence id (e.g. `"DIV-0004"`) declaring the oracle is known to be wrong here. |

Any other key is a hard error at load time — there is no separate lint step.

## Answers per input type

Every answer is produced by running `kicad-cli` and normalizing the result (see
below). Nothing is synthesized or hand-reduced.

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
| `roundtrip` | none | see below |

`roundtrip` is a pure invariant with no committed answer: the adapter exports the
fixture into `<out>/original/`, re-serializes a scratch copy with `pcb upgrade
--force` / `sch upgrade --force`, exports that into `<out>/roundtripped/`, and the
runner asserts the two are equal after normalization.

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
else in every answer is kept byte-for-byte. `runner/normalize.py` is the reference
implementation; a consumer comparing its own output against a committed answer
should apply the same redactions before diffing.

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
