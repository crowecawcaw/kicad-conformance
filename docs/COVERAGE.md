# KiCad line coverage of this suite — measurement and gap report

**What this answers:** not "what do we claim to test" but "which lines of KiCad does
`kicad-cli` actually execute when the conformance suite runs". Every uncovered function
in a subsystem we claim to cover is a missing test case, named.

**This document now holds two measurement rounds.** Round 1's figures are kept as the
*before* column of every table rather than overwritten, because the point of round 2 is
the delta.

| | round 1 | round 2 | round 2b |
|---|---|---|---|
| date (UTC) | 2026-08-03 | 2026-08-04 | 2026-08-04 |
| corpus | **77 cases / 199 checks** | **133 cases / 424 checks** | **133 cases / 424 checks** |
| oracle image | `10.0.5` @ `18fb9289ff0efdca53c0352ed81a0973f0a6b58c`, gcov-instrumented | same image | same source, **rebuilt with `cvpcb_kiface`** (§2c) |
| run window, suite + collect | `23:12:13Z` → `23:43:57Z` | `06:18:29Z` → `06:51:31Z` | `07:23:19Z` → `07:56:16Z` |
| suite phase | 4 min 46 s | **≤ 11 min 44 s** | **≤ 11 min 54 s** |
| `.gcda` profiles | 1850 | 1850 | 1886 |
| result | 178 PASS / 20 FAIL / 1 XFAIL | 379 PASS / 25 FAIL / **19 CRASH** / 1 XFAIL | **398 PASS / 25 FAIL / 0 CRASH** / 1 XFAIL |
| global line coverage | **8.6%** (43362/502510) | **9.5%** (47986/502510) | **9.8%** (49550/504805) |

The round-2 suite-phase figures are upper bounds, not stopwatch readings: the runs were
backgrounded and the log polled at 15-21 s granularity, so all that was *watched* is that
the `SUMMARY` block was present at `06:30:13Z` and `07:35:13Z`. The `START`/`END` window is
exact (`date -u` either side of `run-suite.sh`); the split between suite and collect
inside it is not. Round 1's 4 min 46 s came from two `kicad-cli` stderr timestamps in the
log and is exact.

**Round 2b is the authoritative round-2 measurement.** Round 2 is retained because it
shares round 1's exact denominator (502510 lines) and is therefore the strictly
apples-to-apples delta; 2b adds cvpcb's 2287 lines to the denominator in exchange for
being the only run in which `sch erc` works at all. Read them together: outside `erc`, no
bucket moves by more than 11 covered lines between R2 and R2b except `gui` (+7) and
`other` (+635), which is cvpcb's own code entering the tree.

Every measured number below states the command that printed it. The only estimates in
this document are the `lines` column of §5 — sums of the per-file figures named beside
each entry — and the `~` totals in §5(c).

> **Read this document together with [`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md).** This
> one measures which lines *ran*. That one specifies how the suite proves a line's
> behaviour is *asserted* — i.e. that a change in it would change a recorded answer.
> §6 below is now a standing section of this report, not a footnote: it names the places
> where round 2 lit up code that **no recorded answer could notice changing**.

---

## 1. Reproducing the measurement

```bash
# 1. Build the instrumented image. Cold compile ~25 min (2026-08-03). Changing
#    KICAD_TARGETS invalidates the whole compile layer even with ccache warm: measured
#    29 min 55 s on 2026-08-04 (06:52:51Z -> 07:22:46Z, "build succeeded on attempt 1"),
#    of which 4 min 36 s was image export/unpack.
tools/coverage/build.sh --jobs 8

# 2. Run the whole suite against it and collect the report in one step.
#    --fresh discards any previously accumulated counters.
tools/coverage/run-suite.sh --fresh
```

Artifacts land in `tools/coverage/out/report/`:

| File | What it is |
|---|---|
| `html/index.html` | browsable, line-by-line |
| `focus.json` | per-subsystem rollup — **the numbers in §3** |
| `coverage.json` | gcovr per-file JSON summary |
| `summary.txt` | plain-text per-file table |
| `coverage.info` | lcov tracefile (see the lcov caveat in §7) |

Round 2 also archived, so the next round has a *before* to diff against:
`tools/coverage/out/round1/` (round 1's `coverage.json`, `focus.json`, `summary.txt`,
`run.log`), `tools/coverage/out/round2-coverage.json`,
`tools/coverage/out/round2b-coverage.json`, `tools/coverage/out/round2b-focus.json`,
and the three run logs (`round2-run.log`, `round2b-build.log`, `round2b-run.log`).

### The four analysis tools (added in round 2)

`focus.json` answers one question; these answer the other three, and each exists because
a round-1 number had to be re-derived by hand.

```bash
# per-file and per-bucket BEFORE/AFTER between two gcovr summaries
python3 tools/coverage/compare.py OLD.json NEW.json --top 45 --file zone_filler --file PDF_plotter

# per-FUNCTION coverage of named files, straight from the raw counter volume
tools/coverage/funcs.sh erc.cpp pcb_io_kicad_sexpr_parser.cpp
tools/coverage/funcs.sh --zero-only --min-lines 12 sch_io_kicad_sexpr_parser.cpp

# the §3a corrected buckets, and the dead-file list §5 is triaged from
python3 tools/coverage/gaps.py coverage.json corrected
python3 tools/coverage/gaps.py coverage.json dead --min-lines 60

# coverage summed over any path substring (e.g. KiCad's OWN parser, not the io/board bucket)
python3 tools/coverage/subdir.py "pcbnew/pcb_io/kicad_sexpr/" round1/coverage.json round2b-coverage.json
```

`compare.py`, `gaps.py` and `subdir.py` are pure JSON readers and run anywhere with a
python3 (this workstation has none, so they were run as
`docker run --rm -v "$PWD":/work -w /work kicad/kicad:10.0.5 python3 …`). `funcs.sh`
needs the coverage image and the `kicad-coverage-raw` volume.

---

## 2. The instrumented runs — what happened

### 2a. Round 2 (`tools/coverage/run-suite.sh --fresh`, log `out/round2-run.log`)

```
cases: 133 total, 89 clean, 44 with a failing check, 0 skipped
  CRASH: 19
  FAIL: 25
  PASS: 379
  XFAIL: 1
```

### 2b. Round 2b, after the image fix (log `out/round2b-run.log`)

```
cases: 133 total, 108 clean, 25 with a failing check, 0 skipped
  FAIL: 25
  PASS: 398
  XFAIL: 1
```

The suite is **green against `kicad/kicad:10.0.5`**. **No case, answer, adapter or runner
file was modified in either round.** The remaining 25 failures are the two causes round 1
already classified, at their new case counts:

| cause | round 1 | round 2 / 2b | what it is |
|---|---:|---:|---|
| Debug build prints no parse-error message (`stderr did not contain …`) | 14 | **16** | all `rejects-*` cases; exit code identical on both builds. §2a of round 1. Positive controls still pass, so the checks are live, not vacuous. |
| IPC-D-356 uninitialised `soldermask` read ([DIV-0002]) | 6 | **9** | every board case containing a via. `-ftrivial-auto-var-init=pattern` exposes it as `S-16843009` = `0xFEFEFEFF`. A genuine KiCad 10.0.5 defect; the release build's `S3` is luck. |

Neither is a conformance finding. **Trust the release image.**

**A free determinism observation.** Rounds 2 and 2b are two independent full-suite runs
against two separately-linked binaries. Outside the 19 ERC cases that the image fix
repaired, **every case produced the same verdict in both**, and the failing set was
identical in composition (the same 16 `rejects-*` messages, the same 9 via cases). No new
nondeterminism was observed. That is not a substitute for `--determinism-check`, and it
says nothing about the ratsnest `unconnected_items` ambiguity flagged in [DL-0038] — no
committed case has a fixture that triggers it.

### 2c. Round 2's 19 CRASHes were an image defect, and they hid the largest result in this report

Every one of the 19 `suites/erc/**` cases reported

```
  [CRASH]          erc
      erc: adapter did not exit OK (returncode=255)
      … Failed to load shared library '/opt/kicad-cov/bin/_cvpcb.kiface'
      … IO_ERROR: Failed to load kiface library '/opt/kicad-cov/bin/_cvpcb.kiface'.
      from kiway.cpp : KiFACE() line 284
```

Cause, read out of the source in the image rather than guessed:
`eeschema/eeschema_jobs_handler.cpp:1353` calls

```cpp
ercTester.RunTests( drawingSheet.get(), nullptr, m_kiway->KiFACE( KIWAY::FACE_CVPCB ), … );
```

**unconditionally** — cvpcb owns the footprint-link tester that
`ERC_TESTER::TestFootprintLinkIssues` reaches through
`aCvPcb->IfaceOrAddress( KIFACE_TEST_FOOTPRINT_LINK )` (`erc.cpp:1816-1831`). The
Dockerfile's reduced target set (`kicad-cli pcbnew_kiface eeschema_kiface`) never built
`_cvpcb.kiface`, so `KIWAY::KiFACE()` threw before a single ERC test ran, and
`eeschema/erc/**` read 5.5% no matter how many ERC cases the suite had.

This is the third distinct way "it is a different binary" has bitten, and the nastiest,
because a `CRASH` verdict reads as a *suite* problem. **Fixed in
`tools/coverage/Dockerfile`**: `cvpcb_kiface` added to `KICAD_TARGETS` (measured cost:
the ninja edge count went 2009 → **2032**, i.e. 23 edges, 22 of them
`Building CXX object cvpcb/…`), installed alongside the other two, and a `test -x`
assertion that fails the build rather than the report. After the
rebuild all 19 ERC cases **PASS** — which is also an independent cross-check that the
instrumented build and the release image agree on ERC output byte for byte, since the
committed `erc.json` answers were recorded against the release image.

---

## 3. Per-subsystem numbers — before and after

Source: the `=== coverage by subsystem (line %) ===` block printed by
`tools/coverage/run-suite.sh --fresh` on each date (`focus.json`).

| bucket | lines | R1 covered | R1 % | R2 covered | R2 % | R2b covered | R2b % | Δ lines (R1→R2b) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `erc` | 1797 | 90 | 5.0% | 99 | 5.5% | **1006** | **56.0%** | **+916** |
| `drc` | 16591 | 2450 | 14.8% | 3691 | **22.2%** | 3691 | 22.2% | **+1241** |
| `geometry` | 11739 | 3449 | 29.4% | 4195 | 35.7% | 4196 | 35.7% | +747 |
| `io/board` | 41261 | 2198 | 5.3% | 2674 | 6.5% | 2674 | 6.5% | +476 |
| `io/schematic` | 25705 | 1599 | 6.2% | 2005 | 7.8% | 2005 | 7.8% | +406 |
| `cli/jobs` | 6079 | 2668 | 43.9% | 2818 | 46.4% | 2829 | 46.5% | +161 |
| `connectivity` | 2910 | 1017 | 35.0% | 1066 | 36.6% | 1066 | 36.6% | +49 |
| `io/common` | 7118 | 601 | 8.4% | 628 | 8.8% | 629 | 8.8% | +28 |
| `netlist` | 5616→5622 | 557 | 9.9% | 563 | 10.0% | 565 | 10.1% | +8 |
| `export/plot` | 13902 | 2106 | 15.2% | 2110 | 15.2% | 2110 | 15.2% | **+4** |
| `gui` | 158620→159283 | 527 | 0.3% | 527 | 0.3% | 534 | 0.3% | +7 |
| `other` | 211172→212798 | 26100 | 12.4% | 27610 | 13.1% | 28245 | 13.3% | +2145 |
| **global** | 502510→504805 | 43362 | **8.6%** | 47986 | **9.5%** | 49550 | **9.8%** | +6188 |

The `→` denominators are the cvpcb sources entering the tree in 2b (2287 lines, 17
covered — cvpcb is a GUI app whose kiface we load only for one ERC entry point).
**No file lost coverage between rounds** (`compare.py` `=== FILES THAT LOST COVERAGE (0) ===`).

**The global figure is still not a finding.** `gui` at 0.3% is 32% of the denominator.
Do not quote 9.8% as a quality number.

### 3a. The same four buckets, corrected

`collect.sh`'s bucket patterns are directory prefixes and sweep in GUI code and other
vendors' formats. Recomputed with `python3 tools/coverage/gaps.py <coverage.json> corrected`
(and `subdir.py` for the two `kicad_sexpr` rows, which is the comparison round 1 actually
made):

| what | R1 | R2b | why the raw bucket misleads |
|---|---:|---:|---|
| `drc` excluding `pcbnew/drc/rule_editor/` | 2445/11036 **22.2%** | 3686/11036 **33.4%** | the rule-editor dialog is 67 files / 5555 lines, all 0% |
| `pcbnew/pcb_io/kicad_sexpr/` (KiCad's own board format) | 2147/6987 **30.7%** | 2609/6987 **37.3%** | all but 4 of `io/board`'s 106 files are third-party importers or the legacy reader |
| `eeschema/sch_io/kicad_sexpr/` | 1558/4714 **33.1%** | 1944/4714 **41.2%** | same shape |
| `export/plot` excluding 3D (`step/`, `u3d/`, VRML, IDF) | 2106/8922 **23.6%** | 2110/8922 **23.6%** | 3D out of scope per [DL-0012] |

The `export/plot` row moving by **4 lines in 56 new cases** is the single sharpest signal
in this report — see §4.2.

### 3b. `cli/jobs` — the honest measure is `doPerform`

Every `kicad-cli` subcommand is a static object (`kicad/kicad_cli.cpp:124-176`), so its
argparse constructor runs on *every* invocation. `doPerform` is the method that does the
work. Round 1: **16 of 30 at 0.00%**. Round 2b: **13 of 30**, from
`tools/coverage/funcs.sh` over every `command_*.gcda`:

| newly alive in round 2 | R1 | R2b |
|---|---:|---:|
| `SCH_ERC_COMMAND::doPerform` | 0.00% of 37 | **62.16%** |
| `SYM_UPGRADE_COMMAND::doPerform` | 0.00% of 12 | **83.33%** |
| `FP_UPGRADE_COMMAND::doPerform` | 0.00% of 8 | **100.00%** |

Still `0.00%` — and each one is a line item in §5:

```
0.00% of 119  PCB_EXPORT_3D          0.00% of  41  PCB_EXPORT_PS
0.00% of  58  PCB_RENDER             0.00% of  37  PCB_EXPORT_IPC2581
0.00% of  52  PCB_EXPORT_PDF         0.00% of  37  PCB_IMPORT
0.00% of  47  PCB_EXPORT_DXF         0.00% of  30  SCH_EXPORT_BOM
0.00% of  28  PCB_EXPORT_ODB         0.00% of  23  JOBSET_RUN
0.00% of  15  PCB_EXPORT_GENCAD      0.00% of  11  SCH_EXPORT_PYTHONBOM
0.00% of   3  PCB_EXPORT_HPGL
```

Note that `PCB_EXPORT_PDF` and `PCB_EXPORT_DXF` are still zero even though the adapter
implements `export-pdf`/`export-dxf` — that is §4.2.

---

## 4. Did round 1's predictions hold?

Round 1 produced a gap list; the work since then landed 56 new cases **and** four new
harness capabilities ([DL-0034], [DL-0036], [DL-0037], [DL-0038]). Predicting what each
should move, then checking, separates "the capability does not work" from "the capability
works and nothing uses it". They are different bugs with different fixes.

### 4.1 Held — 56 cases moved exactly what they were supposed to

| prediction | before (R1) | after | verdict |
|---|---:|---:|---|
| 19 ERC cases → `eeschema/erc/**` | 90/1797 **5.0%** | 1006/1797 **56.0%** | **held** (only visible after §2c) |
| … `erc/erc.cpp` | 13/1209 **1.1%** | **695/1209 57.5%** | held |
| … `erc_report.cpp` | 0/115 | **62/115** | held |
| … `erc_item.cpp` | 0/130 | **28/130** | held |
| … `erc_settings.cpp` | 62/239 | **140/239** | held |
| … `ERC_TESTER::Test*` methods entered | **0 of 21** | **18 of 21** | held |
| … `connection_graph.cpp` (ERC's substrate) | 976/2369 | **1411/2369** | held |
| 30 DRC cases → `drc` (corrected) | 2445/11036 **22.2%** | 3686/11036 **33.4%** | held |
| `parseNETCLASS` (`board-netclasses`) | 0.00% of 60 | **73.33%** | held |
| `parsePadstack` (`pad-padstack-per-layer`) | 0.00% of 208 | **23.08%** | held |
| `parsePCB_BARCODE` (`board-barcode`) | 0.00% of 99 | **74.75%** | held |
| … and `pcbnew/pcb_barcode.cpp` | 45/542 | **193/542** | held |
| `parseSymbolArc` (`symbol-arc-and-bezier`) | 0.00% of 104 | **47.12%** | held |
| `parseSymbolBezier` (same case) | 0.00% of 47 | **82.98%** | held |
| `parseSchTextBoxContent` (`sheet-text-box`) | 0.00% of 95 | **70.53%** | held |
| `command_sym_upgrade::doPerform` (`symbol-lib/rejects-*`) | 0.00% of 12 | **83.33%** | held |
| `command_fp_upgrade::doPerform` (`footprint-lib/rejects-*`) | 0.00% of 8 | **100.00%** | held |

Per-provider DRC movement (`compare.py`, round 1 → round 2b), the 30 new cases doing
their job:

```
courtyard_clearance   33/193 ->  147/193      hole_to_hole     50/135 -> 109/135
copper_clearance     120/733 ->  344/733      hole_size        35/109 ->  85/109
solder_mask          125/471 ->  244/471      text_dims        21/141 ->  71/141
annular_width         57/188 ->  123/188      silk_clearance   54/127 -> 108/127
edge_clearance        88/222 ->  149/222      drc_item.cpp     23/119 ->  55/119
misc                 141/306 ->  204/306      text_mirroring   22/47  ->  42/47
zone_connections      20/160 ->   49/160      connectivity     63/161 ->  86/161
```

### 4.2 Failed — four capabilities exist and **no case invokes them**

This is one finding, not four. `runner/manifest.py`'s `EXTRA_NAMES` accepts
`refill-zones`, `pdf`, `dxf` and `parity`; `adapters/kicad.py` implements
`drc-refill-zones`, `export-pdf`, `export-dxf`, `drc-parity`; each was verified working by
its own decision entry. But:

```bash
$ grep -rh "^extra" suites/ --include=case.toml | sort | uniq -c | sort -rn
     33 extra   = ["drc"]
     19 extra   = ["erc"]
      1 extra   = ["summary-kicadxml"]
      1 extra   = ["ipcd356"]
$ find suites -name "*.kicad_dru" -o -name "*.kicad_pro" | wc -l
0
```

| prediction | before | after | why it failed |
|---|---:|---:|---|
| `refill-zones` → `pcbnew/zone_filler.cpp` | 0/1991 | **0/1991** | no case sets `extra = ["refill-zones"]` ([DL-0036] shipped the verb only) |
| `pdf` → `PDF_plotter.cpp` (+`pdf_outline_font` 0/327, `pdf_stroke_font` 0/298) | 0/1433 | **0/1433** | no case sets `extra = ["pdf"]` |
| `dxf` → `DXF_plotter.cpp` | 0/651 | **0/651** | no case sets `extra = ["dxf"]` |
| `.kicad_dru` sibling → `drc_rule_parser.cpp` | 0/505 | **0/505** | **predicted to fail, and it did**: [DL-0034] shipped the copying, zero `.kicad_dru` files exist |
| `parity` → `drc_test_provider_schematic_parity.cpp` | 9/198 | **9/198** | no case sets `extra = ["parity"]`, no `.kicad_sch` sibling next to any board |

`common/plotters/` is **1080/4933 in both rounds — byte-identical**, which is the
mechanical confirmation that not one plot-format line was newly reached in 56 cases.

**The distinction matters.** These are not "write a case that reaches new code" items like
§5's bucket (a) parser fixtures; they are "the road is built and nobody drove on it".
Each is one `extra = [...]` line plus a recorded answer, and between them they are
**~5000 lines of dead KiCad**, the largest single block left that a CLI run can reach.

---

## 5. Round-2 target list

Sorted by value, in three **honest** buckets. Bucket (c) is deliberately short; padding it
with GUI code would make the list look bigger and be worth less.

### (a) Reachable with the harness exactly as it stands — write a case

| # | case to write | newly executes | lines |
|---|---|---|---:|
| **a1** | `suites/drc/refill-zones-*` with `extra = ["refill-zones"]` | `pcbnew/zone_filler.cpp` **0/1991** — the largest CLI-reachable dead file in the tree | ~2000 |
| **a2** | a board + same-stem `board.kicad_dru` with a custom `clearance` rule | `drc_rule_parser.cpp` **0/505**, plus the rest of `drc_rule.cpp` / `drc_rule_condition.cpp` | ~530 |
| **a3** | a `creepage` custom rule (needs a2's fixture shape) | `drc_creepage_utils.cpp` **0/1712** + `drc_test_provider_creepage.cpp` 11/213 | ~1900 |
| **a4** | `extra = ["pdf"]` on one board and one schematic | `PDF_plotter.cpp` 0/1433, `pdf_outline_font.cpp` 0/327, `pdf_stroke_font.cpp` 0/298, `PCB_EXPORT_PDF_COMMAND::doPerform` 0/52, `job_export_pcb_pdf.cpp` 0/29 | ~2140 |
| **a5** | `extra = ["dxf"]` on one board | `DXF_plotter.cpp` 0/651, `PCB_EXPORT_DXF_COMMAND::doPerform` 0/47, `job_export_pcb_dxf.cpp` 0/20 | ~720 |
| **a6** | `extra = ["parity"]` board + same-stem `.kicad_sch` | `drc_test_provider_schematic_parity.cpp` 9/198 | ~190 |
| **a7** | a `physical_clearance` rule (needs a2) | `drc_test_provider_physical_clearance.cpp` **14/415** | ~400 |
| **a8** | a differential pair + coupling/skew rule (needs a2) | `drc_test_provider_diff_pair_coupling.cpp` 24/384, `drc_test_provider_matched_length.cpp` 42/242 | ~560 |
| **a9** | `track_angle` / `track_segment_length` rules (needs a2) | those two providers, 9/83 and 9/66 | ~130 |
| **a10** | board fixtures for the 20 still-dead `PCB_IO_KICAD_SEXPR_PARSER::parse*` (below) | `pcb_io_kicad_sexpr_parser.cpp` 2084/4991 | ~830 |
| **a11** | schematic/symbol fixtures for the 11 still-dead `SCH_IO_KICAD_SEXPR_PARSER::parse*` (below) | `sch_io_kicad_sexpr_parser.cpp` 1538/3032 | ~540 |
| **a12** | ERC cases for the 3 untouched testers | `TestFootprintFilters` 0/44, `TestFourWayJunction` 0/35, `TestSimModelIssues` 0/30 | ~110 |

**a1–a6 first.** Together they sum to **~7500 lines** sitting behind four
`extra = [...]` names (`refill-zones`, `pdf`, `dxf`, `parity`) and two sibling files
(`.kicad_dru`, `.kicad_sch`) — all in capabilities that already exist, were verified by
their own authors, and are already documented in `TEST_CASE_FORMAT.md` §§4/6. Nothing in
the suite is cheaper per line. a2 is a *prerequisite*: a3, a7, a8 and a9 are all
custom-rule cases, so proving the `.kicad_dru` convention once unblocks ~3000 further
lines behind it.

Still-dead parser functions (`tools/coverage/funcs.sh --zero-only --min-lines 12`, round 2b
— every one was also dead in round 1, and an independent `grep` of every fixture for the
matching token agrees):

```
board   parsePCB_TABLE 106  parseGENERATOR 94  parseTITLE_BLOCK 74  parse3DModel 65
        parseFootprintVariant 65  parseDefaults 61  parsePCB_REFERENCE_IMAGE 56
        parseViastack 51  parseGROUP 48  parseTEARDROP_PARAMETERS 47  parsePCB_TARGET 42
        parsePostMachining 31  parseFootprintStackup 31  parsePCB_POINT 30
        parseRenderCache 30  parseVariants 30  parseDefaultTextDims 24
        parseZoneLayerProperty 22  parseLayersForCuItemWithSoldermask 15
        parseZoneDefaults 13
sch     parseSchTable 90  parseSymbolTextBox 86  parseTITLE_BLOCK 69
        parseSchSymbolInstances 51  parseImage 44  parseGroup 42  parseSchRuleArea 42
        parseSchPolyLine 41  parseSymbolText 33  parseBusAlias 28  parseBodyStyles 13
```

### (b) Reachable only through a harness capability we do not have

Each needs a decision entry before a case can exist. Listed so nobody writes a case that
cannot work, and so the capability cost is visible next to its payoff.

| # | missing capability | would reach | lines |
|---|---|---|---:|
| **b1** | an answer that records KiCad's **re-serialized board** (`pcb upgrade` writes one; nothing compares it) | `PCB_IO_KICAD_SEXPR::format(...)` — **every overload is 0%**: `format(PCB_TRACK*)` 0/147, `format(ZONE*)` 0/132, `format(PCB_SHAPE*)` 0/80, `format(PCB_DIMENSION_BASE*)` 0/73, `format(PCB_BARCODE*)` 0/38, `formatTeardropParameters`, `formatRenderCache`, … The suite reads the format and never writes it. | ~800 |
| **b2** | a `netlist` format matrix (the verb hardcodes `kicadsexpr`/`kicadxml`) | `netlist_exporter_spice.cpp` 0/423, `_allegro` 0/398, `_cadstar` 0/133, `_pads` 0/119, `_orcadpcb2` 0/68, `netlist_generator.cpp` 0/101 | ~1240 |
| **b3** | a footprint library a board can link to (`fp-lib-table` + `.pretty` are not in `_BOARD_SIBLING_SUFFIXES`) | `drc_test_provider_library_parity.cpp` **42/508** | ~470 |
| **b4** | `pcb export gencad` verb | `export_gencad_writer.cpp` 0/662, command 0/15, `job_export_pcb_gencad.cpp` 0/22 | ~700 |
| **b5** | `--format gerber` on drill and pos (adapter hardcodes Excellon + CSV) | `gendrill_gerber_writer.cpp` 0/315, `gerber_placefile_writer.cpp` 0/165 | ~480 |
| **b6** | `sch export bom` verb | command 0/30, `job_export_sch_bom.cpp` 0/60 | ~90 |
| **b7** | `pcb export ipc2581` / `odb` verbs | commands 0/37 + 0/28, jobs 0/41 + 0/27 | ~130 |
| **b8** | `jobset run` verb | `common/jobs/jobset.cpp` 0/147, command 0/23, `jobs_output_archive.cpp` 0/50, `jobs_output_folder.cpp` 0/34 | ~250 |
| **b9** | legacy-format fixtures (`.brd` / `.sch`) fed to `pcb upgrade` / `sch upgrade` | `pcb_io_kicad_legacy.cpp` 0/1553, `sch_io_kicad_legacy*.cpp` 0/2317 | ~3900 |

b1 is the most interesting of these as a *conformance* matter: a file-format suite that
never checks the writer is pinning half a format. b9 is the largest, and is a genuine
scope question — legacy import is format conformance, but it is not what `DESIGN.md`
currently claims to cover.

### (c) Legitimately unreachable from a CLI run — do not write cases

| area | lines | why |
|---|---:|---|
| `gui` bucket (dialogs, widgets, tools, GAL, 3d-viewer) | 159283 | no GUI in a CLI run; measured 0.3% |
| third-party importers (`pcb_io/{altium,cadstar,eagle,pads,allegro,easyeda,fabmaster,ipc2581,odbpp,pcad,geda}`, `sch_io/{…}`) | ~55000 | reachable only via `pcb import`, a *stated* "Later/conditional" roadmap item |
| `pcbnew/drc/rule_editor/**` | 5555 | the DRC rule-editor dialog, not the rule engine |
| `pcbnew/router/**` — interactive router | 1224 in the `pns_diff_pair*` files alone (the whole directory is larger and also 0%) | nothing in `kicad-cli` invokes the router |
| `pcbnew/exporters/step/**`, `u3d/**`, `exporter_vrml`, `export_idf`, `pcb render` | ~5000 | 3D/STEP, out of scope by [DL-0012] |
| `eeschema/sim/**` (ngspice model tables, IBIS, simulator UI) | ~10000 | no CLI entry point to the simulator |
| `common/bitmap_info.cpp`, `kicad/pcm/**`, `common/git/**`, `common/dialog_shim.cpp` | ~8000 | icon table, plugin manager, VCS integration — all GUI-only |
| `pcbnew/drc/drc_interactive_courtyard_clearance.cpp` | 131 | interactive-move-only provider |
| wxPython scripting | — | compiled out (`KICAD_SCRIPTING_WXPYTHON=OFF`) |

---

## 6. Executed, but nothing asserts it

> *"Coverage is what we can assert and verify, not just run."*

[`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md) defines three states — **unexecuted**,
**executed-only**, **asserted** — and a perturbation mechanism that measures the second
boundary. That mechanism is **designed and unimplemented**, and the check is mechanical:

```bash
$ find suites -type d -name perturb | wc -l
0
```

**All 133 cases would report `UNASSERTED-CASE` today**, so every percentage in §3 remains
an upper bound, and the two asserted columns ([DL-0030], [DL-0031]) still cannot be filled.
What round 2 *can* say, by inspection, is where the executed-only band is widest:

1. **The IPC-D-356 via path — the known-defective one.** `pcbnew/exporters/export_d356.cpp`
   runs on **every board case** (`pcb export ipcd356` is part of the `summary` battery),
   and its uninitialised `soldermask` field ([DIV-0002]) is *the* thing round 1 found
   wrong. Neither `summary.json` (which keeps only `nets` and `placement`) nor the one
   case with `extra = ["ipcd356"]`
   (`board-parse/pad-properties-testpoint-castellated`, which keeps only `nets` and
   `testpoints`) records the `S…` field at all. The adapter's reducer *rejects* a
   malformed value — that is how the defect surfaced, as a `ValueError` on
   `S-16843009`, which the `S(?P<serial>\d+)` group cannot match because of the minus
   sign — but a **well-formed wrong** value (`S3` → `S1`) would match, be dropped, and
   never be noticed: `runner/reduce.py:451` says so in as many words ("the trailing
   `S<n>` serial and rotation are dropped entirely (never compared)"). Note also that the
   reducer calls that column a *serial*; KiCad's own `export_d356.h:40` calls it
   `int soldermask`. **The precise field round 1 identified as a KiCad bug is a field the
   suite discards and has mis-identified.** This is the smoke target
   ASSERTED_COVERAGE.md §7 names, and it is real.
2. **The gerber and drill answers are `[byte-only]`.** 899 `gerbers/` files and 69
   `drill/` files are recorded, but they report `INFO`, never `FAIL`, against a non-KiCad
   adapter ([DL-0015]/[DL-0026]). So `GERBER_plotter.cpp` (549/897) and
   `gendrill_excellon_writer.cpp` (218/392) are asserted **for KiCad regressions only** —
   nothing about them is asserted cross-implementation. That is roughly a third of
   `export/plot`'s covered lines.
3. **The dark capabilities cut the other way.** a1–a6 in §5 are not "executed but
   unasserted" — they are unexecuted. Worth saying plainly, because the fix ordering
   differs: an unasserted line needs a better answer, an unexecuted line needs a case.
   §4.2 is about the second.
4. **Where the suite is doing this well, it should be said.** The `drc.json` and
   `erc.json` answers are substantive violation sets (type, severity, description, item
   descriptions, positions), so the +1241 DRC and +916 ERC lines are backed by answers
   that would move. The `board-barcode` and `pad-padstack-per-layer` case files record a
   hand-verified falsifiability check in their `case.toml` comments. That is the manual
   version of the mechanism — **which is the point: it leaves no artifact and nothing
   re-runs it.**

**Recommendation for round 3:** implement ASSERTED_COVERAGE.md Tier 1 before adding many
more cases. The corpus grew 73% in one round; the fraction of it that is provably
falsifiable is still measured at zero.

---

## 7. What this measurement cannot tell you

1. **Covered ≠ tested.** See §6. Every percentage in §3 is an upper bound.
2. **The global 9.8% is not a quality score** — see §3.
3. **Bucket percentages are only as good as their directory patterns** — §3a shows four of
   them off by 8 to 33 percentage points against the question actually being asked.
   Always confirm against `coverage.json` (`gaps.py corrected`, `subdir.py`) before
   quoting a bucket.
4. **It is a different binary.** Debug, `-O0`, no wxPython, no stock symbol/footprint
   libraries (hence `LIBRARY_MANAGER::LoadGlobalTables` assert spam on every invocation),
   and `-ftrivial-auto-var-init=pattern`. §2 shows **three** ways that changed observable
   behaviour, one of them new this round and severe (§2c). **If this image and
   `kicad/kicad:10.0.5` disagree on a result, the release image wins.**
5. **Branch percentages are indicative only** (6.7% global in 2b, 5.8% in R1).
6. **The lcov tracefile is bounded** (`COVERAGE_LCOV_TIMEOUT`, default 900 s) because
   lcov 2.x falls back to pure-Perl `JSON::PP` in this image; it dominated the collect
   phase in both rounds (gcovr finished round 2b at 07:45, `focus.json` landed at 07:56).
   Set `COVERAGE_SKIP_LCOV=1` to skip it.
7. **Function line counts are gcov's**, which includes template expansions — a good
   ranking signal and a poor absolute one. Round 1 said "all 22 `ERC_TESTER::Test*`";
   the source has **21** (`grep -n "^[a-zA-Z_].* ERC_TESTER::Test" erc.cpp`), and 18 of
   the 21 are now entered.

---

## 8. Tooling defects found and fixed while producing these reports

Recorded because each silently produced a *plausible-looking* wrong answer.

1. **(R1) The instrumented image was not instrumented.** Coverage flags passed as
   `-DCMAKE_CXX_FLAGS_DEBUG=…` were clobbered by KiCad's unconditional non-cache
   `set( CMAKE_CXX_FLAGS_DEBUG "-g3 -ggdb3" )` (`CMakeLists.txt:441-446`). `CMakeCache.txt`
   still showed the flags; the image had **zero `.gcno`**. Fixed by moving them to
   `CMAKE_C_FLAGS`/`CMAKE_CXX_FLAGS` plus two build-time assertions (`profile-arcs` in
   `build.ninja`; `.gcno` count > 1000 — now **1934**).
2. **(R1) `collect.sh` aborted on CMake's compiler-identification probe.** Fixed by
   removing it before the walk plus `--gcov-ignore-errors=no_working_dir_found`.
3. **(R1) Raw counters on a Windows bind mount made the run ~10x slower** (3.39 s vs
   0.32 s per process exit). Counters now live in a named Docker volume.
4. **(R2) The image could not run `sch erc` at all.** The reduced target set omitted
   `cvpcb_kiface`, and `sch erc` loads it unconditionally — see §2c. Nineteen ERC cases
   reported `CRASH` and `eeschema/erc/**` reported 5.5% instead of 56.0%. Fixed in the
   Dockerfile (target + install + `test -x` assertion). **This is the round-2 analogue of
   defect 1: a coverage report that looked complete and was measuring nothing.**
5. **(R2) There was no before/after tooling.** Round 1's corrected figures had been
   re-derived by hand from `coverage.json` with ad-hoc python one-liners, which is not
   reproducible and not diffable. `compare.py`, `funcs.sh` + `gcovfuncs.py`, `gaps.py` and
   `subdir.py` now exist (§1); `gaps.py corrected` reproduces round 1's published `drc`
   22.2% and `export/plot` 23.6% exactly, which is how they were checked.
