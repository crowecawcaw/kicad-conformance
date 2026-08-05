# Engine coverage — defining the denominator

**The question this answers.** *"I want to aspire to complete coverage of the KiCad
engine and no coverage of the GUI. I'm not sure how to measure that."*

[`COVERAGE.md`](COVERAGE.md) measures which KiCad lines the suite executes. It cannot
answer the question above, because it has no defensible **denominator**:

* its global figure (**9.8%**, 49550/504805) divides by all of KiCad, ~32% of which is
  GUI that a `kicad-cli` process cannot enter. The document says so itself: *"the global
  figure is not a finding"*.
* its per-subsystem buckets divide by hand-drawn directory prefixes. Round 2 already
  found four of them wrong by 8–25 points once the GUI was excluded by hand
  ([`COVERAGE.md`](COVERAGE.md) §3a), and `cli/jobs`' 43.9% is inflated because every
  subcommand's argparse constructor runs on every invocation.

"Complete engine coverage" is meaningless until we can say precisely which lines are in
scope. This document defines that set, by a rule someone else can re-run and audit.

> Every number below states the command and the UTC timestamp that printed it. Nothing
> here is an estimate.

---

## 1. The definition

> **The engine is the transitive closure of the CLI entry points over the symbol
> reference graph of the built objects, minus an explicitly declared and separately
> verified set of GUI barriers.**

Concretely, a KiCad source line is **in the engine denominator** iff the function that
owns it (gcov's own `function_name` attribution) is reachable in that closure.

Three classes come out of it, and every one of the 504805 lines gets exactly one:

| class | meaning |
|---|---|
| `engine` | reachable from a CLI entry point. **This is the denominator.** |
| `deferred` | reachable *only* through a CLI verb this project has decided not to exercise (3D/STEP per [DL-0012]; third-party `pcb import` per `ROADMAP.md` "Later/conditional"). Carved out by cutting the entry point, not by naming directories. |
| `out` | not reachable at all. Out of scope **by construction, not by opinion.** |

The rest of this section is the four things that make that sentence precise.

### 1.1 The graph

`tools/coverage/engine_elf.py` reads the 1924 relocatable objects of the pinned
instrumented build directly (ELF64 parsing in Python — ~20x faster than parsing
`readelf` text on a 3 GB object tree) and emits a deduplicated node/edge list:

* **node** = a mangled symbol name (file-local symbols are namespaced by their object,
  since `static` functions in different TUs share a name)
* **edge** `u → v` = *some relocation inside u's byte range points at v*

At `-O0` GCC emits one `call` per source-level call with a relocation against the callee,
so the relocation set of a function's byte range is a superset of its direct callees.
Inline and template functions live in their own `.text._Z...` comdat section with exactly
one symbol, so they are attributed individually rather than smeared over the whole TU.

Two things had to be added on top of relocations, both because their absence produced a
silently wrong answer (§6.3):

* **intra-section direct calls, recovered by disassembly.** A `call` from one *local*
  symbol to another *local* symbol in the same section is resolved by the assembler and
  leaves **no relocation**. That is exactly how GCC emits
  `_GLOBAL__sub_I_<tu>` → `_Z41__static_initialization_and_destruction_0v`.
  `objdump -d -j .text` recovers them; call sites that carry a relocation are skipped
  (their displacement is a zero placeholder). **190561 edges**, 11% of the graph.
* **alias unification.** GCC emits a constructor three times — `C1` (complete), `C2`
  (base) and `C5` (the unified body they alias) — as three symbols at *one* address;
  destructors get `D0`/`D1`/`D2`/`D5`. Callers relocate against `C1`; gcov attributes the
  lines to whichever name owns the body. Symbols sharing an address are the same code, so
  they are linked both ways. **371431 alias groups.**

### 1.2 Virtual dispatch — RTA, deliberately over-approximating

A vtable (`_ZTV*`) is an ordinary node whose slots are relocations against the virtual
methods. So *"constructor runs → vtable referenced → the class's virtuals are reachable"*
falls out of plain transitive closure. That is **Rapid Type Analysis**. It
over-approximates: a class instantiated anywhere makes *all* of its virtuals in scope,
even ones no reachable call site can dispatch to.

Over-approximation is the safe direction for a coverage denominator — it can only make
coverage look worse than it is. It is also **necessary**: KiCad dispatches its DRC test
providers, its IO plugins and its plotters entirely through virtuals, and a direct-call
graph would miss the entire rule engine.

### 1.3 The roots — `tools/coverage/engine-roots.json`

A root is a place control enters KiCad from outside. The file is declarative, carries a
prose rationale per entry, and is the only thing a reviewer has to agree with.

| group | what | why it must be a root |
|---|---|---|
| `cli.main` | `main` | process entry |
| `cli.doPerform` | the 30 `CLI::*_COMMAND::doPerform(KIWAY&)` + the base | the COMMAND objects are file-scope statics (`kicad/kicad_cli.cpp:124-176`) and `main` dispatches through a virtual, so they do not fall out of `main` |
| `kiface.entry` | `PCB/SCH/CV::IFACE::{OnKifaceStart, OnKifaceEnd, Reset, IfaceOrAddress, HandleJob, PreloadLibraries, CancelPreload, ProjectChanged, SaveFileAs}` | `kicad-cli` `dlopen`s the three kifaces and calls in through the KIFACE vtable (`common/kiway.cpp:759`, `common/project.cpp:433`, `eeschema/erc/erc.cpp:1816`). A static graph crosses neither a `dlopen` boundary nor an indirect vtable call. |
| `static.init` | every `_GLOBAL__sub_I_*` | these genuinely run on every invocation, **and they are load-bearing**: KiCad registers its DRC test providers, IO plugins and property descriptors from file-scope statics, so the only static path to `DRC_TEST_PROVIDER_COPPER_CLEARANCE::Run` runs through one |

**The rule for choosing the kiface roots** is mechanical, not curated: *root every KIFACE
virtual whose signature is free of GUI types.* Applied to the 12-entry interface in
`include/kiway.h:155`, that excludes exactly `CreateKiWindow(wxWindow*, …)`,
`HandleJobConfig(JOB*, wxWindow*)` and `GetActions(std::vector<TOOL_ACTION*>&)`.

`static.init` was originally a *separate* closure so that static-constructor inflation
could be reported apart. That was wrong and the measurement said so: it put demonstrably
executed rule-engine code in the out-of-scope bucket. The inflation problem is answered by
**measuring** the floor instead (§1.5).

### 1.4 The GUI barriers — the part that has to be checked, not trusted

Declaring an entry point out of scope is not enough; it has to be **enforced**, because
RTA re-imports it through the back door. The three `IFACE` objects are file-scope statics,
so their constructors are static-init-reachable, and a constructor references its class's
vtable — which put `IFACE::CreateKiWindow` back in scope and dragged the entire editor
frame / tool / dialog tree with it. Measured: enforcing the barrier is the difference
between **199550 and 18469** static-init-only lines.

So `excluded` groups are **barriers in every closure**. There are three, totalling
**20577 symbols**:

1. `CreateKiWindow` / `HandleJobConfig` — the two window-shaped KIFACE virtuals.
2. **The signature rule**, applied to every function rather than just the KIFACE vtable:
   any function whose mangled name contains `16wxTopLevelWindow`, `8wxDialog`, `7wxFrame`,
   `8wxBitmap`, `wxGLCanvas` or `[0-9]wx[A-Za-z]*Event`. `kicad-cli` never constructs a
   top-level window and never runs an event loop. Matching on the Itanium-mangled
   `<length><name>` form makes this an exact token match, not a substring guess.
   **`8wxWindow` is deliberately absent** and KiCad's own `TOOL_EVENT` is not matched —
   both because §6.1 falsified them.
3. `TOOL_INTERACTIVE::setTransitions()` — where each interactive tool binds its
   `TOOL_EVENT` handlers. A CLI run has no `TOOL_DISPATCHER`, so no transition is ever
   taken, but every tool's constructor calls it, making it the single RTA hub that pulled
   in the editor tool set. It is the edge that put `EDA_3D_CANVAS::DisplayStatus` 13 hops
   from a CLI root.

Barriers 2 and 3 were adopted **because their cost was measured, not because they sounded
right**: together they removed 25330 lines from the denominator and cost **16 executed
lines** (§6.2). That trade is stated so it can be re-litigated.

### 1.5 The free floor — the honest answer to static-constructor inflation

`kicad-cli version` does no work, but it runs every static constructor in `kicad-cli`,
including all 30 subcommands' argparse setup. Those lines are "covered" in every run and
tell you nothing; this is what makes `cli/jobs` read 43.9% in
[`COVERAGE.md`](COVERAGE.md) §3.

`tools/coverage/engine-scope.sh floor` runs exactly that invocation into an isolated
`GCOV_PREFIX` and records what it executes. **Measured 2026-08-05: 4335 engine lines.**
The report subtracts it from both sides to give an *earned* figure. This is a
measurement, not a model, and it is the reason no static analysis of "which lines are
just registration" was attempted.

---

## 2. Reproducing it

```bash
tools/coverage/engine-scope.sh          # all five stages
tools/coverage/engine-scope.sh graph    # 1. ELF reference graph          ~3 min
tools/coverage/engine-scope.sh close    # 2. reachability closure         ~35 s
tools/coverage/engine-scope.sh lines    # 3. gcov line attribution        ~50 s
tools/coverage/engine-scope.sh floor    # 4. the free floor               ~60 s
tools/coverage/engine-scope.sh report   # 5. join + print                 ~60 s

tools/coverage/engine-scope.sh why  _ZN12PCB_IO_MGR10PluginFindE…   # audit one verdict
tools/coverage/engine-scope.sh grep 'doPerform'                     # author a root
```

Everything runs inside the pinned image `kicad-conformance/kicad-coverage:10.0.5`, so the
answer is a function of *(image, `engine-roots.json`)* and nothing else. Intermediates
live in the `kicad-engine-scope` Docker volume, not a Windows bind mount, for the same
>10x reason `run-suite.sh` gives. Stage 3 reads the **same `kicad-coverage-raw` volume**
the suite writes, so this re-uses round 2b's counters — no suite re-run was needed and the
comparison to [`COVERAGE.md`](COVERAGE.md) is exact.

**When KiCad is bumped**, re-run `graph` and `close`. `engine-roots.json` is 10 patterns
over stable symbol names; `close` fails loudly if a root pattern stops matching
(`roots_declared_but_isolated`), which is how a renamed entry point surfaces as an error
rather than as a quietly smaller denominator.

Artifacts, `tools/coverage/out/engine/`:

| file | what |
|---|---|
| `engine-denominator.tsv.gz` | **the denominator itself** — one row per source line: `file, line, class, covered, function, entry_point` |
| `engine-coverage.json` | per-file/per-bucket/per-entry-point rollup, top gaps |
| `engine-report.txt` | the tables in §3 |
| `closure-stats.json` | root resolution, barrier counts, GUI-cut violations |
| `elf-stats.json` | graph construction counters and soundness gates |

---

## 3. The first measurement

Command: `tools/coverage/engine_scope.py close` + `engine_lines.py report`, against the
round-2b counters (**133 cases / 424 checks**, 1886 `.gcda`, the run
[`COVERAGE.md`](COVERAGE.md) reports as 9.8%). Printed **2026-08-05T06:08:24Z**.

```
class            lines   covered      pct
engine          204964     46755    22.8%
deferred         11953         0     0.0%
out             287888      2795     1.0%
ALL             504805     49550     9.8%
```

| | |
|---|---:|
| **ENGINE LINE COVERAGE** | **46755 / 204964 = 22.8%** |
| free floor (measured) | 4335 engine lines |
| **earned** (floor removed from both sides) | 42420 / 200629 = **21.1%** |
| **ENGINE FUNCTION-ENTRY coverage** | **5573 / 17407 = 32.0%** |
| engine files never entered at all | **649 / 1294** |
| of the denominator, reachable only via a file-scope static | 25178 lines (5941 covered) |
| global, for comparison | 49550 / 504805 = 9.82% |

**The `ALL` row reproduces `COVERAGE.md`'s published 49550/504805 exactly.** That is not a
coincidence to be glossed over — it is the cross-check that this pipeline's file filtering
and line counting match `collect.sh`'s gcovr invocation line for line, using a completely
different code path (raw `gcov --json-format` rather than gcovr). A mismatch here would
have meant the engine figure was measuring a different tree.

### 3.1 Why 9.8% → 22.8%

The global figure divides by 504805. The engine figure divides by 204964. **299841 lines
(59% of KiCad) are provably unreachable from any CLI entry point** — and the proof is a
re-runnable closure, not a directory pattern. The numerator moves too, but barely: of the
49550 executed lines, 46755 are in scope and **2795 (5.6%) are not**, which is the
measured error of the rule (§6.2), not a property of KiCad.

### 3.2 By subsystem

Same bucket patterns as `collect.sh`'s `focus.json`, so these read directly against
[`COVERAGE.md`](COVERAGE.md) §3.

| bucket | all lines | engine | engine covered | engine % | `COVERAGE.md` § 3 % | out |
|---|---:|---:|---:|---:|---:|---:|
| `other` | 212798 | 119022 | 27114 | 22.8% | 13.3% | 93488 |
| `io/schematic` | 25705 | 24144 | 2001 | **8.3%** | 7.8% | 1561 |
| `geometry` | 11739 | 10325 | 4104 | 39.8% | 35.7% | 1409 |
| `export/plot` | 13902 | 9628 | 2087 | 21.7% | 15.2% | 1505 |
| `gui` | 159283 | **9464** | 519 | 5.5% | 0.3% | 141597 |
| `drc` | 16591 | 7643 | 2584 | 33.8% | 22.2% (33.4% corrected) | 8948 |
| `io/board` | 41261 | 7214 | 2661 | 36.9% | 6.5% | 34047 |
| `io/common` | 7118 | 5677 | 591 | 10.4% | 8.8% | 1441 |
| `cli/jobs` | 6079 | 5095 | 2800 | 55.0% | 46.5% | 315 |
| `netlist` | 5622 | 3571 | 563 | 15.8% | 10.1% | 2051 |
| `erc` | 1797 | 1593 | 983 | **61.7%** | 56.0% | 204 |
| `connectivity` | 2910 | 1588 | 748 | 47.1% | 36.6% | 1322 |

Two rows are worth reading twice.

* **`drc` reproduces round 2's hand-derived correction without being told to.**
  [`COVERAGE.md`](COVERAGE.md) §3a had to recompute `drc` by hand as
  "22.2% → 33.4% once `pcbnew/drc/rule_editor/` is excluded". The closure gets **33.8%**
  from first principles, having never heard of `rule_editor`. That is the strongest single
  piece of evidence that the rule agrees with an independently-derived human judgement.
* **`io/board` 6.5% → 36.9%** for the same reason `COVERAGE.md` §3a gives (all but 4 of
  its 106 files are third-party importers), except that here the 34047 excluded lines are
  a closure result rather than a `subdir.py` invocation aimed at a hand-picked path.

### 3.3 By entry point

The report attributes each in-scope line to the CLI entry point that reaches it. **This
column is weak and should be read as a hint, not a partition**, for a structural reason:
`IFACE::HandleJob` dispatches on job type, so a single kiface root reaches every job
handler in the binary. 178549 of the 204964 engine lines attribute to `kiface.entry`.
The per-subcommand rows below are only the `kicad-cli`-side argument plumbing:

```
   lines  covered    pct  entry point
  178549    37794  21.2%  kiface.entry
   24994     5908  23.6%  static.init
    7514     2000  26.6%  JOBSET_RUN_COMMAND
     221        2   0.9%  PCB_EXPORT_3D_COMMAND
     200      143  71.5%  FP_EXPORT_SVG_COMMAND
     194      142  73.2%  PCB_DRC_COMMAND
     178        0   0.0%  PCB_RENDER_COMMAND
     173      154  89.0%  VERSION_COMMAND
```

### 3.4 The largest genuinely-in-scope gaps

From `engine-report.txt`, ranked by uncovered engine lines. Annotations are mine.

| uncov | engine | % | file | note |
|---:|---:|---:|---|---|
| 5090 | 5090 | 0.0% | `common/bitmap_info.cpp` | **a false positive — see §7.1.** The icon table. |
| 2980 | 2980 | 0.0% | `eeschema/sch_io/altium/sch_io_altium.cpp` | schematic importers are genuinely CLI-reachable, unlike the board ones |
| 2907 | 4984 | 41.7% | `pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp` | **the single most valuable target**: KiCad's own board parser |
| 2036 | 2036 | 0.0% | `eeschema/sim/sim_model_ngspice_data_hsim.cpp` | ngspice model tables |
| 1996 | 1996 | 0.0% | `eeschema/sch_io/geda/sch_io_geda.cpp` | |
| 1985 | 2458 | 19.2% | `pcbnew/footprint.cpp` | |
| 1957 | 1957 | 0.0% | `eeschema/sch_io/eagle/sch_io_eagle.cpp` | |
| 1778 | 1784 | 0.3% | `pcbnew/specctra_import_export/specctra.cpp` | DSN/SES; no CLI verb exercises it |
| 1754 | 1754 | 0.0% | `eeschema/sch_io/cadstar/cadstar_sch_archive_loader.cpp` | |
| 1729 | 1729 | 0.0% | `eeschema/sim/kibis/ibis_parser.cpp` | |
| 1663 | 1663 | 0.0% | `pcbnew/drc/drc_creepage_utils.cpp` | **[`COVERAGE.md`](COVERAGE.md) §5 a3** — needs one `.kicad_dru` fixture |
| 1511 | 1511 | 0.0% | `common/io/cadstar/cadstar_archive_parser.cpp` | |
| 1474 | 3008 | 51.0% | `eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp` | KiCad's own schematic parser |
| 1444 | 1935 | 25.4% | `pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp` | **the writer** — [DL-0040]'s territory |
| 1437 | 1621 | 11.3% | `libs/kimath/src/geometry/shape_line_chain.cpp` | |
| 1433 | 1433 | 0.0% | `common/plotters/PDF_plotter.cpp` | **[`COVERAGE.md`](COVERAGE.md) §5 a4** — one `extra = ["pdf"]` line |

**This list independently reproduces `COVERAGE.md` §5's hand-built backlog** —
`drc_creepage_utils` (a3), `PDF_plotter` (a4), the two `kicad_sexpr` parsers (a10/a11),
the sexpr *writer* (b1) — while dropping the entries that turned out to be unreachable.
It also surfaces three the hand-built list missed: `specctra.cpp`/`specctra.h` (3042 lines
of DSN/SES round-trip, 0.3%), `ibis_parser.cpp`, and the ngspice model tables.

---

## 4. Alternatives considered and rejected

| approach | verdict |
|---|---|
| **Static reachability from CLI entry points** | **Chosen.** See §5 for the toolchain survey that ruled out the obvious ways of getting it. |
| **GUI-dependency exclusion** (a TU/function is GUI if it references wxWidgets GUI types) | **Rejected as the primary rule; adopted as a barrier inside the closure (§1.4).** As a primary rule it fails both ways. It over-includes: `pcbnew/board.cpp` and `libs/kimath` mention no wx GUI type but are still 60–70% unreachable from a CLI run, so the complement is far too big to be "the engine". It under-includes: `common/bitmap_info.cpp`'s 5090-line icon table mentions no GUI type in the function that owns those lines. It also has no way to express `deferred` (3D/STEP is not GUI, it is just out of scope for now). Its real strength is cutting RTA edges, which is what it is used for. |
| **Linkage / section based** ("what actually got linked, filtered by CLI reference") | **Rejected: the build already does this and it is not enough.** The Dockerfile builds only `kicad-cli` plus the three kifaces it `dlopen`s, and the six GUI app targets are not compiled at all. That is a real filter and it is why the tree is 504805 lines rather than the whole of KiCad — but `_pcbnew.kiface` is a 531 MB shared object containing *every* pcbnew dialog, tool and canvas, because the kiface is the application. Linkage cannot separate them. Measured: the `gui` bucket is 159283 lines, 32% of the linked tree; the closure keeps 9464 of them. |
| **Curated path classification done rigorously** | **Rejected as the definition, retained as a cross-check.** This is what `collect.sh`'s buckets and §5(c) of [`COVERAGE.md`](COVERAGE.md) already are, and round 2 measured them wrong by 8–25 points. The deeper problem is that it cannot be wrong in a way anyone notices: there is no experiment that falsifies "`pcbnew/router/**` is out of scope". The closure can be falsified — §6.2 does it 2795 times. Where the curated list and the closure agree (`rule_editor`, third-party board importers, 3D), that agreement is now evidence rather than assumption. |

### 4.1 The hybrid actually shipped

closure (§1.1–1.3) + declared-and-enforced GUI barriers (§1.4) + a measured floor (§1.5)
+ an entry-point cut for deferred scope. Each ingredient earns its place by a measured
delta, listed in §6.

---

## 5. What the GCC toolchain can and cannot give you

Investigated on the pinned image before writing any of the above.

| mechanism | verdict |
|---|---|
| `-fcallgraph-info` | Would give a per-function callgraph directly from the compiler, and is the *right* answer in principle. **Requires a full rebuild** — measured 24–30 min for this image, and changing any flag invalidates the whole BuildKit compile layer. Rejected on reproducibility grounds: a denominator you cannot re-derive without a half-hour rebuild on a machine whose Docker has crashed under load is a denominator nobody will re-derive. Worth revisiting if the image is ever rebuilt for another reason. |
| `-fdump-ipa-cgraph` | Same rebuild cost; output is a per-TU dump keyed to GCC-internal node numbering, needing more parsing than the ELF for a similar result. Also unhelpful at `-O0`, where IPA barely runs. |
| LTO + linker map | LTO would defeat the purpose: it changes the code being measured, and gcov attribution at `-O0` is what makes line numbers match the source a human reads. Rejected. |
| **`nm` over `.o`/`.so`, closure from CLI entry symbols** | The obvious cheap approximation, and **too coarse**: `nm` gives defined-vs-undefined per object, so the closure is at *translation-unit* granularity. Any one reachable function drags in the whole TU. |
| **Relocation-level parsing of the `.o` tree** | **Chosen.** No rebuild, function-level granularity for free (comdat sections give template/inline functions their own symbol), and the vtable slots needed for RTA are just relocations in `.data.rel.ro`. 1924 objects in **168.5 s**. Its two blind spots — assembler-resolved local calls, and constructor aliases — are both real and both fixed (§1.1); each was found by measurement, not by reading. |

### 5.1 Soundness gates in the extractor

`elf-stats.json`, printed 2026-08-05T05:55 (`engine_elf.py`):

```
objects 1924   defs 3029900   failed 0   edges 1916316
disasm_objects 1922   disasm_edges 190561   disasm_timeouts 0
alias_groups 371431
site_anon_in_text 0        <-- the gate that matters
```

`site_anon_in_text` counts relocation sites in a `.text` section not covered by any
function symbol. Each one would be a **lost call edge**, i.e. an under-approximated
denominator — the dangerous direction. At `-O0` every instruction lives inside a sized
function symbol, so this must be zero, and it is. `failed` is fatal: a `.o` the parser
cannot read is a silently missing chunk of the graph, which is precisely the failure mode
this project has been burned by twice.

---

## 6. Validation

An unvalidated classifier is exactly the kind of plausible-but-unchecked number this
project has been burned by twice — an uninstrumented build that looked fine, and a missing
kiface that made ERC read 15/1209 while the report looked complete. Both directions are
checked, both mechanically, both falsifiable.

### 6.1 Positive — the rule says `engine`; can a CLI run actually reach it?

`tools/coverage/engine_validate.py positive` runs five `kicad-cli` verbs the committed
suite does **not** exercise, into an isolated `GCOV_PREFIX` (the shared
`kicad-coverage-raw` volume is never touched), and reports which lines went from
*never executed by the suite* to *executed*.

Probe, printed **2026-08-05T06:11Z**: `pcb export pdf`, `pcb export dxf`,
`pcb drc --refill-zones`, `sch export bom`, `pcb export gencad` on
`suites/board-parse/board-barcode/board.kicad_pcb` and
`suites/erc/bus-to-net-conflict/sheet.kicad_sch`. 1850 `.gcda` written.

```
=== A. lines NEWLY executed by the probe run, classified ENGINE ===
    143  pcbnew/exporters/export_gencad_writer.cpp
    105  pcbnew/zone_filler.cpp
     96  eeschema/fields_data_model.cpp
     92  eeschema/eeschema_jobs_handler.cpp
     58  pcbnew/pcbnew_jobs_handler.cpp
     49  common/jobs/job_export_sch_bom.cpp
     44  common/tool/tool_manager.cpp
     39  kicad/cli/command_pcb_export_pdf.cpp
total newly-executed ENGINE lines: 1065 in 46 files

=== A'. …classified OUT (each one falsifies the rule) ===
total newly-executed OUT lines: 58 in 5 files
```

**1065 lines that the committed suite has never executed, that five plain `kicad-cli`
invocations do execute, and that the rule had already classified as engine** — led by
`zone_filler.cpp` and `export_gencad_writer.cpp`, i.e. exactly the files
[`COVERAGE.md`](COVERAGE.md) §5 nominates as the top targets (a1, b4). The engine claim
for the files the backlog is built on is confirmed by execution, not by argument.

Note `common/tool/tool_manager.cpp` (44 lines): tool-framework code under the `gui`
bucket that a CLI run demonstrably executes. That is why the barrier in §1.4 cuts
`setTransitions` and the wx event family rather than anything named `TOOL`.

**This check falsified part of the rule, and the rule changed.** `8wxWindow` was in the
signature barrier until this probe was re-run against the final closure and showed 95
lines of `zone_filler.cpp` moving from `engine` to `out`: KiCad's
`ZONE_FILLER_TOOL::FillAllZones( wxWindow* aCaller, … )` takes a `wxWindow*` that the CLI
passes as `nullptr`. **A `wxWindow*` parameter is very often an optional parent, not proof
of GUI-ness.** It was removed from the barrier; `wxDialog`, `wxFrame`, `wxTopLevelWindow`
and the event family have no such escape hatch and stayed. Effect of that one correction:
denominator 201533 → 204964, newly-executed-OUT 194 → **58**.

`engine-scope.sh why` shows why the barrier was wrong, in six lines — a plain CLI job
handler reaching the zone filler through *two* `wxWindow*`-taking functions:

```
$ tools/coverage/engine-scope.sh why _ZN11ZONE_FILLER4Fill
### _ZN11ZONE_FILLER4Fill  (5 hops from a work root)
   PCB::IFACE::OnKifaceStart(PGM_BASE*, int, KIWAY*)
   std::make_unique<PCBNEW_JOBS_HANDLER>(KIWAY*&)
   PCBNEW_JOBS_HANDLER::PCBNEW_JOBS_HANDLER(KIWAY*)
   PCBNEW_JOBS_HANDLER::JobExportPs(JOB*)
   ZONE_FILLER_TOOL::FillAllZones(wxWindow*, PROGRESS_REPORTER*, bool)
   ZONE_FILLER::Fill(std::vector<ZONE*> const&, bool, wxWindow*)
```

### 6.2 Negative — the rule says `out`; did the suite execute it anyway?

Every line classified `out` that the suite executed is a **counter-example**: the closure
is missing an edge. This is the honest soundness measure, and it is a number, not a claim.

`engine_validate.py negative`, 2026-08-05T06:08:24Z:

```
2795 lines (5.6% of all 49550 executed lines) are counter-examples to the closure.
```

The history of that number is the history of this tool, and each drop was a real defect:

| | executed-but-out | what was wrong |
|---|---:|---|
| first closure | **17118** (34.5%) | anonymous `.rodata` acting as a TU-level hub, *and* static-init edges missing |
| + anonymous data made a pure sink | 11763 (23.7%) | assembler-resolved local calls still missing |
| + disassembly for intra-`.text` calls, alias unification, init merged into engine | **2779** (5.6%) | |
| + GUI signature and `setTransitions` barriers | **2795** (5.6%) | **+16 lines: the measured cost of the barriers** |

The residual 2795 is dominated by `drc` (1107) and `other` (1131) and is concentrated in a
handful of files — `drc_test_provider_copper_clearance.cpp` (212),
`board_design_settings.cpp`, `pcbexpr_functions.cpp`. These are indirect calls the graph
cannot see: `std::function` targets reached through anonymous data, and the property
system's function-pointer tables. **They are a known, quantified 5.6% hole in the `out`
class, not a claim of soundness.**

The barriers of §1.4 were adopted on this evidence: together they removed **25330 lines**
from the denominator for a measured cost of **16 executed lines**. That is the trade, it is
stated so it can be re-litigated, and `engine_validate.py negative` is how anyone
re-litigates it.

### 6.3 Three defects this validation caught that inspection did not

Recorded because each produced a plausible-looking wrong answer, in this project's
tradition of §8 of [`COVERAGE.md`](COVERAGE.md).

1. **Anonymous `.rodata` as a hub.** Falling back to a per-section pseudo-node for
   relocation sites not covered by a symbol made every string literal in a TU a hub
   joining its unrelated functions. `engine-scope.sh why` printed the path: *`kicad-cli
   version` → `wxString::ImplStr` → `#.rodata` → `PNS::BuildHullForPrimitiveShape`* — the
   interactive router, two hops from the version subcommand. Fixed by making anonymous
   data a pure sink. Node count 2358015 → 512205.
2. **Assembler-resolved local calls.** `_GLOBAL__sub_I_<tu>` → `__static_initialization_
   and_destruction_0v` carries **no relocation**, so the static-init closure was exactly
   2× its root count — one hop and stop. Consequence: every DRC test provider, all
   registered from file-scope statics, fell out of the denominator while the suite was
   visibly executing them (`drc_test_provider_copper_clearance.cpp` read `engine=0`,
   `out_covered=344`). Found by §6.2, not by inspection.
3. **Constructor aliases.** `C1`/`C2`/`C5` at one address; callers relocate against one
   name, gcov attributes lines to another. Cost before the fix: `BOARD_DESIGN_SETTINGS`
   (329 lines), `EESCHEMA_SETTINGS` (308), every `*_DESC` property registration and every
   CLI `COMMAND` constructor sat in `out` while executing.

### 6.4 The GUI cut, checked as a claim

`engine-roots.json` carries an `assert_unreachable` block: 12 patterns naming things the
rule *claims* cannot be reached. `close` re-checks them every run and prints violations.
2026-08-05T06:02:14Z, against the engine closure:

| claim | hits |
|---|---:|
| `IFACE::CreateKiWindow` | **0** |
| `IFACE::HandleJobConfig` | **0** |
| `KIWAY::Player` | **0** |
| `PCB_EDIT_FRAME` / `SCH_EDIT_FRAME` / `EDA_DRAW_FRAME` constructors | **0** |
| `EDA_3D_CANVAS` | **0** in the work closure (3 static event-table *objects* via static init) |
| `PCB_SELECTION_TOOL` | 3 |
| dialog constructors | 14 |
| `PNS::` (the interactive router) | 6 in the work closure, 846 via static init |

**Twenty-three leaked symbols in a 320376-node closure.** The traced cause of the
`PCB_SELECTION_TOOL` ones is honest over-approximation rather than a bug:
`BOARD_COMMIT::Push` — genuinely called by the DRC path — contains a runtime-guarded
`if( frame ) { selTool->… }` branch, and static reachability cannot see the guard.

---

## 7. Honest limits

### 7.1 The largest known false positive

`common/bitmap_info.cpp` is **5090 engine lines at 0%, the top entry in §3.4, and it is
wrong.** It is KiCad's icon table. `engine-scope.sh why` gives the path:

```
_GLOBAL__sub_I_panel_setup_pinmap.cpp
_Z41__static_initialization_and_destruction_0v
PANEL_SETUP_PINMAP::changeErrorLevel(wxCommandEvent&)
PANEL_SETUP_PINMAP::setDRCMatrixButtonState(wxWindow*, PIN_ERROR)
KiBitmapBundle(BITMAPS, int)  →  GetBitmapStore()  →  BITMAP_STORE::BITMAP_STORE()
BITMAP_STORE::buildBitmapInfoCache()  →  BuildBitmapInfo(...)
```

The barriers cut `changeErrorLevel` and `setDRCMatrixButtonState`, but an address-taken
reference inside the TU's static-init region reaches past them. `BuildBitmapInfo` itself
has no GUI type in its signature, so the signature rule does not catch it either. It is
**2.5% of the denominator**; removing it would move engine coverage from 22.8% to 23.4%.
Left in, named, and traceable, rather than special-cased — a one-line barrier would fix it
and should be added only with the §6.2 cost measured.

### 7.2 The rest

1. **The denominator over-approximates by construction.** RTA puts every virtual of every
   instantiated class in scope. 100% is therefore not attainable even in principle.
2. **The `out` class has a measured 5.6% hole** (§6.2). Anything reached only through a
   `std::function` stored in anonymous data, or through the property system's pointer
   tables, can be misclassified.
3. **Per-entry-point attribution is a hint, not a partition** (§3.3).
4. **`deferred` is 11953 lines and is a policy choice, not a fact.** It is what
   disappears when `PCB_EXPORT_3D` / `PCB_RENDER` / `PCB_IMPORT` and their kiface-side
   handlers are cut. Change the policy, change the number; the cut list is six regexes in
   `engine-roots.json`.
5. **Covered still ≠ tested.** Everything in [`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md)
   applies unchanged. 22.8% is an upper bound on an upper bound.
6. **It is still a different binary** — every caveat in `tools/coverage/README.md`
   §"Limitations" carries over.
7. **Line counts are gcov's**, so template expansions inflate large-header files.

### 7.3 What "complete" can and cannot mean

**It cannot mean 100% of engine lines.** Beyond §7.2's over-approximation, a large part of
the denominator is error and defensive paths that need I/O failures, malformed input at
every nesting level, and platform branches. No conformance suite gets there, and chasing it
would produce cases that test KiCad's error strings rather than the file formats.

**It can mean two things that are achievable and that this tool now measures:**

* **Function-entry coverage — every in-scope function entered at least once.**
  Today: **5573 / 17407 = 32.0%**. This is the right headline for the owner's aspiration.
  A function never entered is a feature never exercised; a function entered but only 40%
  covered is usually error handling.
* **No in-scope file at zero.** Today: **649 of 1294 engine files have never executed a
  single line.** That is a countable, closable backlog, and it is the number I would put
  on a wall.

**What would represent success.** Engine line coverage in the **45–55%** band, engine
function-entry coverage **above 70%**, and **fewer than 100 engine files at zero** — with
[`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md) Tier 1 landed so those are asserted lines
and not merely executed ones. The §3.4 backlog plus [`COVERAGE.md`](COVERAGE.md) §5's
a1–a6 is roughly 15000 lines of it, which alone would take 22.8% to ~30%. Complete engine
coverage is an asymptote; **"every in-scope function has been entered by at least one
falsifiable case" is a finish line**, and it is 5573 of 17407 done.

---

## 8. Relationship to the other coverage documents

* [`COVERAGE.md`](COVERAGE.md) — *which lines ran*. Unchanged and still the record of the
  suite runs; this document reuses its round-2b counters and reproduces its global figure.
* [`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md) — *which lines are asserted*. Orthogonal:
  it narrows the numerator, this narrows the denominator. Both are needed before any
  percentage here is a quality statement.
* **This document** — *which lines are in scope at all*.
