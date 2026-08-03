# Roadmap

Milestones for building out kicad-conformance. Realistic, incremental, oracle-first.
Context: [`DESIGN.md`](DESIGN.md), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md),
[`DECISIONS.md`](DECISIONS.md). Primary oracle throughout: **KiCad 10.0.5** ([DL-0001]).

Each milestone is "done" only when its cases are **green against real `kicad-cli` 10.0.5
in the Docker CI job** — a case that hasn't run against KiCad is not evidence.

---

## M0 / M0.5 — Harness + standard-answers rework ✅ done

The runner (Python 3.11 stdlib), the reference `kicad-cli` adapter, the OK/REJECT/CRASH
verdict + positive-control machinery ([DL-0013]), the known-oracle-divergence strict
xfail ([DL-0018]), and the standard-answers manifest shape ([DL-0025]–[DL-0028]: a case
declares no verb, the input's file suffix chooses a fixed answer set, gerbers/drill are
byte-compared on every board case) are all implemented and green in CI. 8 cases across
`board-parse`, `schematic-parse` and `drc`. See `README.md` for the current count and
`git log` for how this shipped — a completed milestone belongs in history, not a live
migration plan.

---

## M1 — Parse suites (schematic + board)

Deepen the parse surface — the highest-value format-documentation work. Rejection cases:
a broad set (unterminated s-expr, unknown token, bad layer count, missing required
field, malformed UUID), each citing its `doc` section and carrying a positive control
([DL-0013]). Happy cases: more boards and sheets whose `summary.json` covers format
tokens the current fixtures don't reach — zones, multiple footprint types, NPTH holes,
blind/buried vias, multi-unit symbols, deeper hierarchical-sheet variants (building on
`suites/schematic-parse/hierarchical-sheet/`). Fixture provenance per [DL-0011]:
hand-author small/rejection inputs, seed-and-`upgrade` larger happy ones, all GUI-free.

**Exit criteria:** the parse suites cover the documented top-level tokens of `.kicad_sch`
and `.kicad_pcb`, happy + representative rejection cases.

---

## M2 — ERC, more DRC, netlist specifics

`erc`: `sch erc --format json --severity-all` → a normalized violation set (an ERC
finding is data, not a tool failure). `drc`: one case per rule class (clearance,
unconnected, courtyard, silk-over-pad, …) — name-and-exclude any irreducibly
nondeterministic fixture with a `skip_reason`. `netlist`: cases genuinely about the
netlist *interchange format* rather than about a schematic's meaning — bus and label
resolution, power symbols (multi-sheet hierarchy itself is no longer a gap here: M0.5
already proved it via `suites/schematic-parse/hierarchical-sheet/`).

**Exit criteria:** ERC + representative DRC rule classes green; mismatch bucketing
(names-only / count / membership) reported.

---

## M3 — Symbol and footprint libraries

A library's standard answer is **its drawings and nothing else** — `kicad-cli` 10.0.5
offers no structured library export (`DESIGN.md` §3b.4). `symbol-lib`/`footprint-lib`
happy cases: one case per interesting geometry (pin types, pad shapes, courtyards), each
recording `render/` holding KiCad's own filenames. Rejection cases: honour the `-o`
gotchas (`DESIGN.md` §2) — `fp` needs a `.pretty` **directory**, never a lone
`.kicad_mod`.

**Exit criteria:** library render cases green; library parse rejections carry controls;
the `render/` directory-answer path exercised by a library with more than one symbol.

---

## M4 — Fabrication output: make it fair across implementations

Byte-recorded gerber and drill answers already cover every board case ([DL-0026]) — that
part is done. What is left is the **fairness problem**: a byte answer is a
KiCad-version-regression signal only (`DESIGN.md` §3d/§8); a clean-room tool emitting
valid-but-differently-formatted RS-274X fails every one of those files while being
perfectly conformant, so `gerbers/`/`drill/` report `INFO`, never `FAIL`, in ecosystem
mode today.

**Remaining work — rasterize and compare images.** Plot each gerber to a raster with a
pinned renderer and compare pixels, the same way the cross-implementation render path
works ([DL-0021]). Fair across implementations and decomposition-blind; costs a gerber
rasterizer in the CI image and a per-case threshold that must be shown load-bearing. A
structural RS-274X reduction remains ruled out ([DL-0020]). Also here: cases specifically
*about* fab output once a `gerber`/`drill` suite exists — an aperture macro, an
oval/slotted hole, a board with blind/buried vias whose drill file has multiple layer
spans.

**Exit criteria:** a second adapter's gerber output is compared *and can fail*, on a
threshold demonstrated load-bearing.

---

## M5 — Coverage infrastructure

The gap-finding development loop ([DL-0006]).

- Build instrumented KiCad from source (Debug `-O0 --coverage`,
  `KICAD_BUILD_QA_TESTS=OFF`), run the whole suite once, merge with `lcov`/`gcovr`
  (GCC-version-matched), filter `/usr` + `thirdparty/` + `qa/`, emit HTML + a summary.
- CI: scheduled weekly / per-KiCad-bump, self-hosted runner, never on the PR path.
- Process: turn uncovered KiCad modules into a new-case backlog feeding M1–M4.

**Exit criteria:** one full coverage run produces a gap report; ≥1 new case authored from
an identified gap.

---

## M6 — Second adapter (ecosystem) + divergence ledger

Prove goal #2: the same corpus drives a non-KiCad implementation. This is materially
easier after the standard-answers rework — a second implementation emits `summary.json`
**directly** and never has to imitate KiCad's stats/pos/ipcd356 export formats.

- Implement a second adapter (candidate: the local `pcb` Rust engine) against the verb
  protocol (`DESIGN.md` §2).
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
- **Library summary** — if a future KiCad adds a structured symbol/footprint export, a
  pin inventory / pad inventory summary slots into `DESIGN.md` §3b.4.
- **`import`** (`pcb import` from Altium/Eagle/… → `.kicad_pcb`) as a parse-target suite.
- **KiCad 11** — when `kicad/kicad:11.0.0` publishes (~Q1 2027): add the matrix entry,
  `--regenerate` to populate `expected/11.0.0/`, promote from non-gating when stable.
- **`corpus/` broad regression** — a scheduled run of a large real-world corpus for
  round-trip/DRC stability beyond the curated cases ([DL-0009]; `corpus/` does not exist
  yet).

---

## Standing rules (apply from M0)

- **Record the full invocation + oracle version + date beside every headline number.**
  "A figure without a corpus size is stale by construction." Re-run a subagent's headline
  measurement yourself, ideally by a different method.
- **A test that cannot fail is not evidence** — break what a new case covers and watch it
  go red before trusting its green.
- **Never background a long sweep** — run in the foreground, in chunks.
- **Never record a number you did not just watch a command print.**
