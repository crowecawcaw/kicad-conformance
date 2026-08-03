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

## M0.5 — The standard-answers rework (next; docs are written, runner is not)

[DL-0025]–[DL-0028] finish what [DL-0022]/[DL-0023] started. A case no longer declares
*what* to check: the input's file suffix chooses a fixed set of **standard answers**, so
`op`, `[[check]]`, `expected`, `outcome` and `args` are all deleted from the manifest.
Gerbers and drill return as byte-compared answers on every board case ([DL-0026]),
reversing the fab half of [DL-0024]. `model.json` becomes `summary.json` ([DL-0028]).

### Runner work

1. **`runner/summary.py`** — `build_board_summary(stats_json, pos_csv, d356_text)` and
   `build_schematic_summary(netlist_text, fmt)`, exactly to
   [`VALIDATION.md`](VALIDATION.md) §4. Reuse the parsers in `runner/reduce.py`; trim
   `reduce_stats` to the fields §4.1 keeps.
2. **Adapter** — one entry point per input type that runs the whole standard set into a
   single scratch directory ([`VALIDATION.md`](VALIDATION.md) §9.1). Six invocations for a
   board, two for a schematic, one for a library. Mind the `-o` asymmetry: `pcb export svg`
   takes a **file** path, everything else takes a **directory**.
3. **Manifest** — delete `op`, `[[check]]`, `expected`, `outcome`, `args`, `compare`,
   `tags`. Add `extra` (array of strings). Keep `concept`, `doc`, `input`/`inputs`, `root`,
   `control`, `error_contains`, `error_contains_any`, `min_kicad`, `skip_reason`,
   `[known_divergence]`. A manifest still containing `[[check]]` should be a clear
   authoring error, not silently honoured.
4. **Engine** — dispatch the comparison on the answer's name/extension, not on a field
   ([`VALIDATION.md`](VALIDATION.md) §9.2). **Reinstate** a directory-tree comparator for
   `gerbers/` and `drill/` only — the one [DL-0024] deleted — plus the five gerber/Excellon
   normalizers (G1–G3, D1–D2, [`VALIDATION.md`](VALIDATION.md) §7.3), and no others. Keep
   the SVG normalizer and CRLF→LF. In ecosystem mode, `gerbers/`/`drill/` report `INFO`,
   never `FAIL`.
5. **Scratch copies must preserve the input filename** — gerber filenames and the
   `%TF.ProjectId` GUID are both derived from it (verified, DESIGN §4). A scratch copy
   named `input.kicad_pcb` would silently rewrite every gerber answer.
6. **`.gitattributes`** — add `expected/**/gerbers/**` and `expected/**/drill/**` as LF
   text ([DL-0016]). Also delete the two `board.kicad_prl` side-effect files if a bare
   `kicad-cli` run ever leaves them next to a fixture.
7. **Housekeeping** — `runner/README.md` and `scripts/regen.sh` still describe the old
   modes and say "golden"/"model"; update both with the code.

### Case migration — the exact `case.toml` and answers for each of the 7 current cases

The 11 → 7 case consolidation already happened on disk. What follows is what each of the
**7 committed cases** becomes. Every gerber/drill file count below was measured against
`kicad-cli` 10.0.5 in Docker, not estimated.

---

**1. `suites/board-parse/happy/0001-minimal-two-layer-board/`**

```toml
concept = "A minimal two-layer board (no footprints, standard layer table) parses into a mostly-empty but well-formed summary."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

Generate `expected/10.0.5/`:
- `summary.json` — **rename** of the existing `model.json`; regenerate rather than `git mv`.
- `render-F_Cu.svg` — **new**. This board draws nothing on F.Cu; the answer is a valid
  726-byte SVG with an empty `<g>`, and that is correct and worth recording.
- `gerbers/` — **new, 21 files, 12 317 bytes.** No `pcbplotparams` block in this board, so
  KiCad's built-in default set applies: 20 layer files + `board-job.gbrjob`.
- `drill/board.drl` — **new**, header-only (no `T` tool lines) because the board has no
  holes.

**2. `suites/board-parse/happy/0002-populated-board/`**

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

Generate `expected/10.0.5/`:
- `summary.json` — rename of `model.json`.
- `render-F_Cu.svg` — **already committed and byte-unchanged**; the pinned layer is still
  `F.Cu` ([`VALIDATION.md`](VALIDATION.md) §6.2), so this decision costs no regeneration.
  Delete the `args = ["--layers", "F.Cu"]` line that used to produce it.
- `gerbers/` — **new, 7 files, 5 573 bytes**: `board-F_Cu.gtl`, `board-B_Cu.gbl`,
  `board-Edge_Cuts.gm1`, `board-Margin.gbr`, `board-F_Courtyard.gbr`,
  `board-B_Courtyard.gbr`, `board-job.gbrjob`. Fewer than case 1 because this board
  carries `(pcbplotparams (layerselection 0x…_55555555_5755f5ff))`.
- `drill/board.drl` — **new**, two tools (`T1C0.400` via, `T2C0.800` pads), three hits.

**3. `suites/board-parse/failure/0001-unterminated-sexpr/`** — the DIV-0001 XFAIL case

```toml
concept = "A board whose (version ...) form is unterminated is rejected with a parse-position error."
doc     = "sexpr-intro"
input   = "board.kicad_pcb"
control = "control.kicad_pcb"
error_contains = "Expecting"

# KNOWN ORACLE DIVERGENCE (DL-0018, docs/DIVERGENCES.md): kicad-cli 10.0.5 prints the
# correct "Expecting" message and then segfaults instead of exiting gracefully. Declared
# as a strict xfail: today's CRASH reports XFAIL; if a future KiCad rejects this cleanly
# the case XPASSes and FAILS the build until the ledger and this marker are updated.
[known_divergence]
kind     = "crash"
reason   = "kicad-cli 10.0.5 segfaults (SIGSEGV) after printing the correct 'Expecting' parse-position message on this truncated board, instead of exiting with a graceful non-zero code -- see docs/DIVERGENCES.md."
tracking = "TODO: file upstream"
```

**No `expected/`.** Delete the `[[check]]` block, `op = "parse-pcb"` and
`outcome = "error"`; `error_contains` moves to the top level and the
`[known_divergence]` table is byte-identical. **The case's meaning is unchanged** — same
control, same substring, same strict-xfail kind. Note the TOML ordering constraint: the
scalar keys must precede `[known_divergence]`.

**4. `suites/schematic-parse/happy/0001-empty-root-sheet/`**

```toml
concept = "An empty root schematic sheet (title block only, no symbols) parses into an empty-but-well-formed summary."
doc     = "sexpr-schematic"
input   = "sheet.kicad_sch"
```

Generate `expected/10.0.5/`: `summary.json` (rename), and `render.svg` — **new**. This
**reverses** the earlier decision to drop the empty-sheet SVG as "asserting nothing".
There is no per-case opt-out any more, and that is the accepted price of a manifest with
no knobs ([DL-0025]). The file is small and the case stays honest either way.

**5. `suites/schematic-parse/happy/0002-two-nets-one-shared-pin/`**

```toml
concept = "Two 2-pin parts sharing both endpoints wire up into exactly two nets, one per shared pin."
doc     = "sexpr-schematic#connectivity"
input   = "sheet.kicad_sch"
extra   = ["summary-kicadxml"]

# Hand-authored fixture (DL-0011): a minimal custom symbol "T2" with two pins at local
# (0,0) and (0,2.54), each with pin length 0 so the electrical connection point equals the
# placed instance's transformed pin coordinate exactly.
```

Generate `expected/10.0.5/`: `summary.json` (rename), `render.svg` (**already committed,
unchanged**). The `extra` adds **no file**: it rebuilds the summary from `kicadxml` and
compares it to the *same* `summary.json`. That equality is the cross-format-fairness
proof, and it is exactly what the old `format = "kicadxml"` second check did — the
concept survives verbatim, with the second sentence moved out of `concept` into one word.

**6. `suites/schematic-parse/failure/0001-unterminated-sexpr/`**

```toml
concept = "A schematic with an unterminated s-expression is rejected by the parser."
doc     = "sexpr-intro"
input   = "sheet.kicad_sch"
control = "control.kicad_sch"
error_contains = "Failed to load schematic"   # the ONLY message KiCad's sch loader emits
```

No `expected/`. Same transformation as case 3, without a divergence marker.

**7. `suites/drc/happy/0001-clean-board/`** — the worked example of an extra

```toml
concept = "A minimal two-layer board with a valid Edge.Cuts outline reports zero DRC violations."
doc     = "cli:pcb-drc"
input   = "board.kicad_pcb"
extra   = ["drc"]
```

Generate `expected/10.0.5/`:
- `drc.json` — the existing answer, unchanged in content.
- `summary.json`, `render-F_Cu.svg`, `gerbers/` (**21 files, 12 451 bytes** — this board
  also has no `pcbplotparams`), `drill/board.drl` (header-only, no holes) — **all new**,
  because a board case gets the standard board answers whether or not that is its headline.
  This board is **not** a duplicate of case 1's despite the similar shape: verified
  distinct (`3cc3dc94…` vs `0f97dc3c…`), and its gerber set differs by 134 bytes.

---

**Resulting case set — 7 cases, 7 distinct inputs, 20 compared answers:**

| Case | Answers compared |
|---|---|
| `board-parse/happy/0001-minimal-two-layer-board` | 4 — summary, render, gerbers/, drill/ |
| `board-parse/happy/0002-populated-board` | 4 — summary, render, gerbers/, drill/ |
| `board-parse/failure/0001-unterminated-sexpr` | 1 — rejection (XFAIL: oracle segfault) |
| `schematic-parse/happy/0001-empty-root-sheet` | 2 — summary, render |
| `schematic-parse/happy/0002-two-nets-one-shared-pin` | 3 — summary, render, summary-from-kicadxml |
| `schematic-parse/failure/0001-unterminated-sexpr` | 1 — rejection |
| `drc/happy/0001-clean-board` | 5 — summary, render, gerbers/, drill/, drc |
| **total** | **20** |

That sums to 20; the table is the authority and the prose repeats it rather than
recomputing it. (The previous revision of this file said "9 checks" in prose while its own
per-case table summed to 10 — a build agent caught it. The count above is stated once, in
one place, and is derived from the seven rows above it.)

Two of those 20 are directory answers holding many files: **49 files** land under
`expected/**/gerbers/` across the three board cases (21 + 7 + 21), plus 3 `.drl` files,
totalling about **30 kB** of new committed text.

Down from 11 cases and 16 checks (pre-M0.5) to 7 cases and 20 answers — *more* checking
from *fewer* fixtures, which is the whole point of deriving the answer set from the input
type.

### Exit criteria

`python3 -m runner suites/` green in the 10.0.5 Docker job; every answer regenerated **in
Docker** ([DL-0016]); each of the three perturbations in
[`VALIDATION.md`](VALIDATION.md) §4.7 demonstrated red; **at least one gerber perturbation
demonstrated red** (move a track by one quantum and watch `gerbers/board-F_Cu.gtl` change)
— the new comparison must be shown falsifiable like every other; the run-twice determinism
test green with the five fab normalizers enabled and **red with any one of them disabled**
(DESIGN §4a); no `golden`, `compare`, `expect`, `outcome`, `op`, `args` or `[[check]]` left
in any manifest or in the runner.

---

## M1 — Parse suites (schematic + board)

Deepen the parse surface — the highest-value format-documentation work.

- `board-parse` / `schematic-parse` `failure/`: a broad set (unterminated s-expr, unknown
  token, bad layer count, missing required field, malformed UUID), each citing its `doc`
  section and carrying a positive control ([DL-0013]). Schematic failures assert
  `Failed to load schematic`; PCB failures may assert the `Expecting` position, and any
  oracle crash is `CRASH` + a ledger entry, never a green pass.
- `happy/`: more boards and sheets whose `summary.json` covers format tokens the current two
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

A library's standard answers are **its drawings and nothing else** — `kicad-cli` 10.0.5
offers no structured library export ([`VALIDATION.md`](VALIDATION.md) §4.5). So:

- `symbol-lib` / `footprint-lib` `happy/`: one case per interesting geometry (pin types,
  pad shapes, courtyards). Each records `render/` holding KiCad's own filenames —
  `<Symbol>_unit<N>.svg` for symbols, `<Footprint>.svg` for footprints, both verified.
- `failure/`: rejection cases, honouring the `-o` gotchas (DESIGN §2) — `fp` needs a
  `.pretty` **directory**, never a lone `.kicad_mod`.

**Exit criteria:** library render cases green; library parse failures carry controls; the
`render/` directory-answer path exercised by a library with more than one symbol.

---

## M4 — Fabrication output: make it fair across implementations

**Option 1 is done, in M0.5.** [DL-0026] restored byte-recorded gerber and drill answers on
**every board case** — not in a `gerber/` suite, but as part of the standard board answers,
so fab coverage scales with the board fixture count instead of needing its own cases. The
five normalizers were re-derived from the binary and four inherited ones were deleted as
having no evidence ([`VALIDATION.md`](VALIDATION.md) §7.3). The gap text that used to sit
in `README.md`, `VALIDATION.md` §7, `DESIGN.md` §9 and this section is gone.

**What is left is the fairness problem, and it is the harder half.** A byte answer is a
KiCad-version-regression signal only: a clean-room tool emitting valid RS-274X with
different-but-equivalent apertures, a different coordinate format, or regions instead of
strokes fails every one of those files while being perfectly conformant. So today
`gerbers/` and `drill/` report `INFO`, never `FAIL`, in ecosystem mode — real coverage
against KiCad, no coverage against anyone else.

**Remaining work — option 2: rasterize and compare images.** Plot each gerber to a raster
with a pinned renderer and compare pixels, the same way the cross-implementation `render`
path works ([DL-0021]). Fair across implementations and decomposition-blind; costs a
gerber rasterizer in the CI image and a per-case threshold that must be shown load-bearing
(perturb the geometry by one quantum, watch it go red, or delete the threshold). A
structural RS-274X reduction remains ruled out ([DL-0020]).

Also here: the `gerber/` and `drill/` suites, now free of routine duty, get cases that are
specifically *about* fab output — an aperture macro, an oval/slotted hole, a board with
blind/buried vias whose drill file has multiple layer spans.

**Exit criteria:** a second adapter's gerber output is compared *and can fail*, on a
threshold demonstrated load-bearing; `gerber/`/`drill/` hold cases about fab-specific
geometry.

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
easier after the standard-answers rework — a second implementation emits `summary.json` **directly**
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
