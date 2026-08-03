# Roadmap

Milestones for building out kicad-conformance. Realistic, incremental, oracle-first.
Context: [`DESIGN.md`](DESIGN.md), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md),
[`VALIDATION.md`](VALIDATION.md), [`DECISIONS.md`](DECISIONS.md). Primary oracle
throughout: **KiCad 10.0.5** ([DL-0001]).

Each milestone is "done" only when its cases are **green against real `kicad-cli` 10.0.5
in the Docker CI job** — a case that hasn't run against KiCad is not evidence.

---

## M0 — Harness + CI green ✅ done

Runner (Python 3.11 stdlib), reference `kicad-cli` adapter, OK/REJECT/CRASH verdict +
positive-control machinery ([DL-0013]), known-oracle-divergence strict xfail ([DL-0018]),
cheap coverage proxy (DESIGN §7a), CI in `kicad/kicad:10.0.5` plus a non-gating nightly
job ([DL-0010]).

---

## M0.5 — The `model` rework (next; docs are already written, runner is not)

[DL-0022]–[DL-0024] replaced the per-projection case design with **one composite `model`
answer per case**, renamed `golden/` → `expected/`, and deleted the byte-comparison layer.
The docs describe the new design; this milestone migrates the runner and the cases to it.

### Runner work

1. **`runner/model.py`** — `build_board_model(stats_json, pos_csv, d356_text)` and
   `build_schematic_model(netlist_text, fmt)`, exactly to the schema in
   [`VALIDATION.md`](VALIDATION.md) §4. Reuse the existing parsers in `runner/reduce.py`;
   trim `reduce_stats` to the fields §4.1 keeps.
2. **Adapter** — one `cmd_model` that runs the two or three exports into scratch and
   writes `<out>/model.json`. Rename `export-pos`/`export-stats`/`export-ipcd356` to
   `pos`/`stats`/`ipcd356`; collapse `export-svg-*` into `render` (dispatch on suffix);
   delete `upgrade` and `bom`.
3. **Manifest** — `expected` replaces `golden`; `outcome` replaces `expect` and defaults
   from the directory polarity; **delete `compare`** (comparison follows from `op`) and
   `tags` (unused).
4. **Engine** — pick the comparison from `op`. Delete `golden-file`/`golden-dir`,
   `_normalized_dir_tree`, `_write_golden_dir`, and the s-expr/gerber/drill/bom
   normalizers that only served them. Keep the SVG normalizer and CRLF→LF.
5. **`expected/` layout** — `case.golden_dir()` → `case.expected_dir()`, path
   `expected/<version>/`; update `.gitattributes`.
6. **Housekeeping** — `runner/README.md` and `scripts/regen.sh` still describe the old
   modes and say "golden"; update both with the code. Re-record every expected file in
   Docker rather than hand-editing the existing ones (`render-F_Cu.svg` moves from case 4
   to case 2 unchanged, but the JSON answers are all newly-shaped).

### Case migration — exactly what happens to each of the 11 existing cases

| # | Case | Action |
|---|---|---|
| 1 | `board-parse/happy/0001-minimal-two-layer-board` | **Rewrite to `model`.** Drop the `parse-pcb` + `canonical.kicad_pcb` byte check. Keeps documenting the smallest valid `.kicad_pcb`; its `model.json` is mostly zeros, which is the point (`has_outline`, empty counts). Its board also serves as the failure case's control. |
| 2 | `board-parse/happy/0002-populated-board-stats` | **Rename to `0002-populated-board` and make it the one populated-board case.** One `model` check + one `render` check (`--layers F.Cu`). Absorbs cases 3, 4 and 9. New `concept`: *"A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."* |
| 3 | `board-parse/happy/0003-board-net-graph` | **Delete.** Duplicate fixture; its net graph is `model.json`'s `nets` section. |
| 4 | `board-parse/happy/0004-fcu-render` | **Merge into 0002** as the second (`render`) check; delete the directory and its duplicate fixture. |
| 5 | `board-parse/failure/0001-unterminated-sexpr` | **Keep unchanged** except `expect` → `outcome`. This is the XFAIL crash case (DIV-0001, [DL-0018]); its `known_divergence` marker, `error_contains = "Expecting"` and control are untouched. |
| 6 | `drc/happy/0001-clean-board` | **Keep**, rename the expected file `drc.reduced.json` → `drc.json`, drop `compare`/`golden`. |
| 7 | `integration/happy/0001-board-parse-drc-gerber` | **Delete, and retire the `integration/` suite** ([DL-0022] supersedes [DL-0017]). Of its seven checks: `parse-pcb`+`export-gerbers` are deleted comparisons; `drc` duplicates case 6 **on a byte-identical board** (verified: both boards hash `8200bdcd625c…`); `stats`/`pos`/`ipcd356`/`svg` all asserted *emptiness* on a board with no footprints and no nets. Nothing is lost. |
| 8 | `netlist/happy/0001-two-nets` | **Move to `schematic-parse/happy/0002-two-nets-one-shared-pin`, rewrite to `model`.** Two checks sharing **one** expected file: `op = "model"` and `op = "model", format = "kicadxml"` — the cross-format-fairness proof, now free. Add a third check, `render`, for the sheet SVG (this sheet has symbols and wires, so unlike case 11 the drawing is not empty). The `netlist/` suite stays as the home for netlist-interchange-specific cases (hierarchy, buses) — see M2. |
| 9 | `placement/happy/0001-two-footprint-placement` | **Delete, and retire the `placement/` suite.** Fourth copy of the same board; placement is `model.json`'s `placement` section, verified falsifiable ([`VALIDATION.md`](VALIDATION.md) §4.7 perturbation B). |
| 10 | `schematic-parse/failure/0001-unterminated-sexpr` | **Keep unchanged** except `expect` → `outcome`. |
| 11 | `schematic-parse/happy/0001-empty-root-sheet` | **Rewrite to a single `model` check.** Drop the `canonical.kicad_sch` byte check (deleted layer) **and** the empty-sheet SVG check — an SVG of an empty sheet asserts nothing; sheet-render coverage moves to case 8, whose sheet actually draws something. |

**Resulting case set — 7 cases, 7 inputs, no duplicated fixture:**

```
suites/board-parse/happy/0001-minimal-two-layer-board/      model
suites/board-parse/happy/0002-populated-board/              model + render(F.Cu)
suites/board-parse/failure/0001-unterminated-sexpr/         reject (XFAIL: oracle segfault)
suites/schematic-parse/happy/0001-empty-root-sheet/         model
suites/schematic-parse/happy/0002-two-nets-one-shared-pin/  model + model(kicadxml) + render
suites/schematic-parse/failure/0001-unterminated-sexpr/     reject
suites/drc/happy/0001-clean-board/                          drc findings
```

Down from 11 cases and 16 checks to 7 cases and 9 checks, with the populated board's four
copies collapsed to one. The two opt-in projection checks that survive are both `render`,
because drawn geometry is the one thing the model provably does not capture
([`VALIDATION.md`](VALIDATION.md) §7.3/§7.4).

### Exit criteria

`python3 -m runner suites/` green in the 10.0.5 Docker job with the new case set; every
`model.json` regenerated **in Docker**; each of the three perturbations in
[`VALIDATION.md`](VALIDATION.md) §4.7 demonstrated red; no `golden`, `compare` or
`expect` left in any manifest or in the runner.

---

## M1 — Parse suites (schematic + board)

Deepen the parse surface — the highest-value format-documentation work.

- `board-parse` / `schematic-parse` `failure/`: a broad set (unterminated s-expr, unknown
  token, bad layer count, missing required field, malformed UUID), each citing its `doc`
  section and carrying a positive control ([DL-0013]). Schematic failures assert
  `Failed to load schematic`; PCB failures may assert the `Expecting` position, and any
  oracle crash is `CRASH` + a ledger entry, never a green pass.
- `happy/`: more boards and sheets whose `model.json` covers format tokens the current two
  fixtures don't reach — zones, multiple footprint types, NPTH holes, blind/buried vias,
  multi-unit symbols, hierarchical sheets.
- Fixture provenance per [DL-0011]: hand-author small/failure inputs, seed-and-`upgrade`
  larger happy ones, all GUI-free.

**Exit criteria:** the parse suites cover the documented top-level tokens of `.kicad_sch`
and `.kicad_pcb`, happy + representative failure.

---

## M2 — ERC, more DRC, netlist specifics

- `erc`: `sch erc --format json --severity-all` → a normalized violation set. An ERC
  finding is data, not a tool failure.
- `drc`: one case per rule class (clearance, unconnected, courtyard, silk-over-pad, …).
  Name-and-exclude any irreducibly nondeterministic fixture with a `skip_reason`.
- `netlist`: the cases that are genuinely about the netlist *interchange format* rather
  than about a schematic's meaning — multi-sheet hierarchy (`root =` + subsheet layout),
  bus and label resolution, power symbols.

**Exit criteria:** ERC + representative DRC rule classes green; at least one multi-sheet
netlist case; mismatch bucketing (names-only / count / membership) reported.

---

## M3 — Symbol and footprint libraries

`model` does not apply to libraries — `kicad-cli` 10.0.5 offers no structured library
export ([`VALIDATION.md`](VALIDATION.md) §4.5). So:

- `symbol-lib` / `footprint-lib` `happy/`: `render` cases (`sym export svg`,
  `fp export svg`), one per interesting geometry (pin types, pad shapes, courtyards).
- `failure/`: `parse-sym` / `parse-fp` rejection cases, honouring the `-o` gotchas
  (DESIGN §2) — `fp` needs a `.pretty` **directory**, never a lone `.kicad_mod`.

**Exit criteria:** library render cases green; library parse failures carry controls.

---

## M4 — Fabrication output: close the gerber/drill gap

**This is the milestone that pays back [DL-0024].** Today `suites/gerber/` and
`suites/drill/` are **empty**: the byte comparison that used to cover them measured
KiCad's formatting rather than anyone's correctness and was deleted, and a structural
RS-274X reduction was ruled out as a second plotter's worth of engineering ([DL-0020]).
The honest consequence, restated: **a bug that corrupts gerber or drill output while
leaving the board model intact is not caught anywhere in this suite.** The model's
`drill_holes` table catches a dropped or mis-sized *hole*; nothing catches a bad *plot*.

Two candidate approaches — pick one, with the owner:

1. **Byte-recorded answers for fab output only.** Re-introduce a narrow file/tree compare
   used *exclusively* by `gerber/` and `drill/`, labelled explicitly as a
   KiCad-version-regression signal that a second implementation is **not** judged on. The
   header normalizers are already specified (DESIGN §4: `G04` `TF.CreationDate` /
   `TF.GenerationSoftware`, the `.gbrjob` JSON `CreationDate`, the Excellon header date,
   the drill report's "Created on"). Cheapest path; least fair across implementations.
2. **Rasterize and compare images.** Plot each gerber/drill layer to a raster with a
   pinned renderer and compare pixels, the same way the cross-implementation `render` path
   works ([DL-0021]). Fair across implementations and decomposition-blind; costs a gerber
   rasterizer in the CI image and a threshold that must be shown load-bearing.

**Exit criteria:** `gerber/` and `drill/` are no longer empty, and the README's
known-gap note is deleted because it is no longer true.

---

## M5 — Coverage infrastructure

The gap-finding development loop ([DL-0006]) — unchanged by this revision.

- `tools/coverage/`: build instrumented KiCad from source (Debug `-O0 --coverage`,
  `KICAD_BUILD_QA_TESTS=OFF`), run the whole suite once, merge with `lcov`/`gcovr`
  (GCC-version-matched), filter `/usr` + `thirdparty/` + `qa/`, emit HTML + a summary.
- `.github/workflows/coverage.yml`: **scheduled weekly / per-KiCad-bump**, self-hosted
  runner, never on the PR path.
- Process: turn uncovered KiCad modules into a new-case backlog feeding M1–M4.

**Exit criteria:** one full coverage run produces a gap report; ≥1 new case authored from
an identified gap.

---

## M6 — Second adapter (ecosystem) + divergence ledger

Prove goal #2: the same corpus drives a non-KiCad implementation. This is materially
easier after the `model` rework — a second implementation emits `model.json` **directly**
and never has to imitate KiCad's stats/pos/ipcd356 export formats.

- Implement a second adapter (candidate: the local `pcb` Rust engine) against the verb
  protocol (DESIGN §2).
- Run `suites/` through it against the **KiCad-recorded answers**; stand up the ledger
  ([DL-0009]) with a per-entry verdict.
- Add the cross-implementation `render` path: pinned `resvg`, raster + SSIM diff, per-case
  documented threshold proven load-bearing ([DL-0021]).
- CI: add the second-adapter job (may be non-gating initially).

**Exit criteria:** the identical `suites/` corpus runs through two adapters; every
second-adapter failure is triaged in the ledger, not hidden.

---

## Later / conditional

- **STEP / 3D conformance** — only if ratified ([DL-0012]); opt-in suite, geometric
  (bbox/mesh) comparison at printed-quantum tolerance, never byte-exact.
- **Library `model`** — if a future KiCad adds a structured symbol/footprint export, a pin
  inventory / pad inventory model slots into [`VALIDATION.md`](VALIDATION.md) §4.5.
- **`import`** (`pcb import` from Altium/Eagle/… → `.kicad_pcb`) as a parse-target suite.
- **KiCad 11** — when `kicad/kicad:11.0.0` publishes (~Q1 2027): add the matrix entry,
  `--regenerate` to populate `expected/11.0.0/`, promote from non-gating when stable.
- **`corpus/` broad regression** — a scheduled run of the real-world corpus for
  round-trip/DRC stability beyond the curated cases.

---

## Standing rules (apply from M0)

- **Record the full invocation + oracle version + date beside every headline number.**
  "A figure without a corpus size is stale by construction." Re-run a subagent's headline
  measurement yourself, ideally by a different method.
- **A test that cannot fail is not evidence** — break what a new case covers and watch it
  go red before trusting its green.
- **Never background a long sweep** — run in the foreground, in chunks.
- **Never record a number you did not just watch a command print.**
