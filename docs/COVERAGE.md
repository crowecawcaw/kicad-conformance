# KiCad line coverage of this suite — measurement and gap report

**What this answers:** not "what do we claim to test" but "which lines of KiCad does
`kicad-cli` actually execute when the conformance suite runs". Every uncovered function
in a subsystem we claim to cover is a missing test case, named.

**Measurement date:** 2026-08-03 (UTC). Suite run started `2026-08-03T23:12:13Z`;
report collected `2026-08-03T23:43:57Z`.
**Oracle:** KiCad **10.0.5**, built from source at commit
`18fb9289ff0efdca53c0352ed81a0973f0a6b58c`, gcov-instrumented.
**Corpus at measurement time:** **77 cases**, **199 checks recorded in this run**
(178 PASS + 20 FAIL + 1 XFAIL — the run is not green against this build; see §2).
**Suite wall clock:** 4 min 46 s instrumented; `1850 .gcda` profiles produced.

Every number below states the command that printed it. Nothing here is estimated.

> **Read this document together with [`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md).** This
> one measures which lines *ran*. That one specifies how the suite proves a line's
> behaviour is *asserted* — i.e. that a change in it would change a recorded answer — and
> the gap report that lists the code we execute while asserting nothing about it. §6.1
> below is the limit it exists to fix; the two asserted columns it adds to §3's table land
> when it is implemented ([DL-0030], [DL-0031]).

---

## 1. Reproducing the measurement

```bash
# 1. Build the instrumented image (~25 min compile on this workstation, cached deps).
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
| `coverage.info` | lcov tracefile (see the lcov caveat in §6) |

`tools/coverage/README.md` documents the build itself. Three things about the tooling
were **broken and are now fixed** — see §7, because the first two silently produced a
plausible-looking non-answer.

**Reproduction check.** The collection step was run a second time, from a freshly built
image against the same retained counter volume, and `focus.json` came back
byte-identical (`diff` reported no difference). The §3 table is therefore a reproduced
figure, not a single observation.

### Function-level detail

`focus.json` is per *file*. The per-*function* lists in §4 came from `gcov` directly:

```bash
docker run --rm -v kicad-coverage-raw:/coverage/raw \
  kicad-conformance/kicad-coverage:10.0.5 bash -c '
    cp -a /coverage/raw/src/build/. /src/build/
    gcda=$(find /src/build -name "pcb_io_kicad_sexpr_parser.cpp.gcda" | head -1)
    gcov -f -m -o "$(dirname "$gcda")" "$gcda"'
```

---

## 2. The instrumented run — what happened

Command: `tools/coverage/run-suite.sh --fresh` (log: `tools/coverage/out/run.log`).

```
cases: 77 total, 57 clean, 20 with a failing check, 0 skipped
  FAIL: 20
  PASS: 178
  XFAIL: 1
```

`1850 .gcda` profiles were produced (`grafting 1850 .gcda files from
/coverage/raw/src/build`). The suite is **green against `kicad/kicad:10.0.5`**; the 20
failures are artifacts of the source build and were each investigated. **No case,
answer, or runner file was modified.** Two distinct causes:

### 2a. 14 failures — the Debug build prints no parse-error message

Affected: all 10 `board-parse/rejects-*` cases and all 4 `schematic-parse/rejects-*`
cases. Each reports `stderr did not contain '<message>'`.

The exit code is *identical* on both builds; only the message vanishes. Verified by
running the same fixture through both images:

```bash
# stock kicad/kicad:10.0.5
kicad-cli pcb export stats --format json -o /tmp/s.json /tmp/b.kicad_pcb
#   RC=3, stderr: Failed to load board: need a number for 'X coordinate' in
#                 '/tmp/b.kicad_pcb', line 87, offset 10.

# instrumented build, same bytes
/opt/kicad-cov/bin/kicad-cli pcb export stats --format json -o /tmp/s.json /tmp/b.kicad_pcb
#   RC=3, stderr: (no 'Failed to load board' line at all)
```

The positive controls still passed (`positive control 'control.kicad_pcb' exited OK, as
required (DL-0013)`), so the checks were live, not vacuous. This is
`tools/coverage/README.md` Limitation #4 — "it is a different binary" — biting exactly
where it was predicted to. **Not a conformance finding. Trust the release image.**

### 2b. 6 failures — a real KiCad 10.0.5 uninitialized read in the IPC-D-356 exporter

Affected: `board-parse/{populated-board, blind-and-buried-vias, micro-via,
four-layer-stackup, via-remove-unused-layers}` and `drc/…/zone-keepout-rule-area` —
i.e. **every board case that contains a via**. All report `summary: adapter did not exit
OK (returncode=1)`, from the adapter's IPC-D-356 reducer:

```
ValueError: unrecognized IPC-D-356 record:
'317NET-1            VIA        MD0157PA00X+011811Y-007874X0236Y0000R000S-16843009'
```

Same board, both images, `kicad-cli pcb export ipcd356`:

| build | VIA record tail |
|---|---|
| `kicad/kicad:10.0.5` | `…R000S3` |
| instrumented | `…R000S-16843009` |

`-16843009` is `0xFEFEFEFF` — the `-ftrivial-auto-var-init=pattern` fill byte. The cause
is in KiCad, not in the coverage build: in
`pcbnew/exporters/export_d356.cpp`, the **pad** path initialises the field before masking
(`rk.soldermask = 3;`, line 151) but the **via** path never does — it goes straight to

```cpp
if( via->IsTented( F_Mask ) ) rk.soldermask |= 1;   // line 231
if( via->IsTented( B_Mask ) ) rk.soldermask |= 2;   // line 233
```

on a `D356_RECORD rk;` whose `int soldermask;` (`export_d356.h:40`) has no initialiser.
The release build happens to read `3`; any build that fills uninitialised locals with a
pattern reads garbage. **This is a genuine defect in KiCad 10.0.5 and a candidate
`docs/DIVERGENCES.md` entry** — the recorded `S3` answers are luck, not specification.

---

## 3. Per-subsystem numbers

Source: `tools/coverage/out/report/focus.json`, printed by the `=== coverage by
subsystem (line %) ===` block of `tools/coverage/run-suite.sh --fresh` on 2026-08-03.

| bucket | files | lines | covered | line % |
|---|---:|---:|---:|---:|
| `cli/jobs` | 89 | 6079 | 2668 | **43.9%** |
| `connectivity` | 16 | 2910 | 1017 | **35.0%** |
| `geometry` | 93 | 11739 | 3449 | **29.4%** |
| `export/plot` | 50 | 13902 | 2106 | **15.2%** |
| `drc` | 114 | 16591 | 2450 | **14.8%** |
| `netlist` | 34 | 5616 | 557 | **9.9%** |
| `io/common` | 40 | 7118 | 601 | **8.4%** |
| `io/schematic` | 55 | 25705 | 1599 | **6.2%** |
| `io/board` | 106 | 41261 | 2198 | **5.3%** |
| `erc` | 9 | 1797 | 90 | **5.0%** |
| `gui` | 1166 | 158620 | 527 | **0.3%** |
| `other` | 1204 | 211172 | 26100 | **12.4%** |

**The global figure is 8.6% (43362/502510 lines), and it is not a finding.** Most of
KiCad is GUI that a CLI run cannot reach; the `gui` row at 0.3% is the proof, and it
alone is 32% of the denominator. Do not quote 8.6% as a quality number.

### 3a. Three of those buckets are misleading as printed

The bucket patterns in `collect.sh` are directory prefixes, so they sweep in code that
is either GUI or another vendor's format. Corrected figures (all from the same
`coverage.json`, recomputed with an explicit exclude):

| what | as printed | corrected | why the raw number misleads |
|---|---:|---:|---|
| `drc` | 14.8% | **22.2%** | `pcbnew/drc/rule_editor/` is 67 files / 5555 lines of wxWidgets dialog panels, all 0%. It is the DRC *rule editor GUI*, not the rule engine. |
| `io/board` | 5.3% | **30.7%** | 102 of the 106 files are importers for Altium/Cadstar/Eagle/PADS/Allegro/IPC-2581/EasyEDA — reachable only via `pcb import`, which no case uses. KiCad's own `pcbnew/pcb_io/kicad_sexpr/` is 2147/6987. |
| `io/schematic` | 6.2% | **33.1%** | same shape: `eeschema/sch_io/kicad_sexpr/` is 1558/4714. |
| `export/plot` | 15.2% | **23.6%** | excluding 3D exporters (`step/`, `u3d/`, VRML, IDF) — out of scope per [DL-0012]. |

Commands that produced the corrected column are `python3` one-liners over
`tools/coverage/out/report/coverage.json` summing `line_covered`/`line_total` with the
stated substring filters; the raw column is `focus.json`.

### 3b. `cli/jobs` 43.9% is the most misleading number in the table

Every `kicad-cli` subcommand is a **static object** (`kicad/kicad_cli.cpp:124-176`), so
its constructor — which is where all the argparse wiring lives, i.e. most of the file —
runs on *every* invocation regardless of which subcommand was asked for. Coverage of
`command_*.cpp` therefore says nothing about whether the command ran.

The honest measure is `doPerform`, the method that does the work. Of the **30**
`kicad-cli` commands that have one, **16 are at 0.00% — never invoked by the suite**:

```
0.00% of 119  command_pcb_export_3d        0.00% of  41  command_pcb_export_ps
0.00% of  58  command_pcb_render           0.00% of  37  command_pcb_export_ipc2581
0.00% of  52  command_pcb_export_pdf       0.00% of  37  command_pcb_import
0.00% of  47  command_pcb_export_dxf       0.00% of  37  command_sch_erc
0.00% of  30  command_sch_export_bom       0.00% of  28  command_pcb_export_odb
0.00% of  23  command_jobset_run           0.00% of  15  command_pcb_export_gencad
0.00% of  12  command_sym_upgrade          0.00% of  11  command_sch_export_pythonbom
0.00% of   8  command_fp_upgrade           0.00% of   3  command_pcb_export_hpgl
```

Contrast with the 14 that do run: `command_pcb_export_gerbers` 90.0%,
`command_sym_export_svg` 86.7%, `command_pcb_export_svg` 83.7%, `command_pcb_drc`
69.1%, `command_sch_export_netlist` 48.4%.

`command_sym_upgrade` and `command_fp_upgrade` at 0% is a **suite gap, not a scope
decision**: the adapter maps `parse-sym`/`parse-fp` to `sym upgrade`/`fp upgrade`, but
`suites/symbol-lib/` and `suites/footprint-lib/` contain **no `rejects-*` cases**, so
those verbs are never invoked.

---

## 4. What is uncovered, and why

### 4a. Legitimately unreachable — classified and set aside

Not gaps. Do not write cases for these.

| area | lines | why unreachable |
|---|---:|---|
| `gui` bucket (dialogs, widgets, tools, GAL, 3d-viewer) | 158620 | no GUI in a CLI run |
| `pcbnew/drc/rule_editor/**` | 5555 | DRC rule-editor dialog |
| `pcbnew/exporters/step/**`, `u3d/**`, `exporter_vrml.cpp`, `export_idf.cpp` | ~4980 | 3D/STEP, out of scope by [DL-0012] |
| `pcbnew/drc/drc_interactive_courtyard_clearance.cpp` | 131 | interactive-move-only provider |
| `pcbnew/pcb_io/{altium,cadstar,eagle,pads,allegro,easyeda,fabmaster,ipc2581,odbpp,pcad,geda}` | ~34000 | third-party importers; reachable only via `pcb import` (a *stated* roadmap item, "Later/conditional") |
| `eeschema/sch_io/{altium,cadstar,eagle,ltspice,easyeda,database,http_lib,pads}` | ~21000 | same |
| wxPython scripting | — | compiled out (`KICAD_SCRIPTING_WXPYTHON=OFF`) |

### 4b. Uncovered code the suite plausibly SHOULD reach

**Board s-expression parser** — `pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp`
is 1903/4991 (38.1%). These member functions were **never entered even once**, each
because no fixture contains the construct (line counts are gcov's, from the `gcov -f -m`
command in §1):

| function | lines | the construct nothing exercises |
|---|---:|---|
| `parsePadstack(PAD*)` | 208 | `(padstack …)` per-layer pad geometry |
| `parsePCB_TABLE` | 106 | `(table …)` |
| `parsePCB_BARCODE` | 99 | `(barcode …)` — **new in KiCad 10** |
| `parseGENERATOR` | 94 | `(generated …)` generator objects (tuning patterns) |
| `parseTITLE_BLOCK` | 74 | `(title_block …)` |
| `parseFootprintVariant` | 65 | component variants |
| `parse3DModel` | 65 | `(model …)` in a footprint |
| `parseDefaults` | 61 | `(setup (defaults …))` |
| `parseNETCLASS` | 60 | `(net_class …)` |
| `parsePCB_REFERENCE_IMAGE` | 56 | `(image …)` |
| `parseViastack` | 51 | per-layer via padstack |
| `parseGROUP` | 48 | `(group …)` |
| `parseTEARDROP_PARAMETERS` | 47 | `(teardrops …)` |
| `parsePCB_TARGET` | 42 | `(target …)` alignment target |
| `parsePostMachining` / `parseFootprintStackup` | 31 / 31 | back-drilling; per-footprint stackup |
| `parseRenderCache` / `parsePCB_POINT` / `parseVariants` | 30 each | `(render_cache …)`, `(pts …)` point lists, `(variants …)` |
| `parseZoneLayerProperty` / `parseZoneDefaults` | 22 / 13 | per-layer zone overrides |
| `parseLayersForCuItemWithSoldermask` | 15 | `(layers F.Cu F.Mask)` on a copper item |

**Schematic s-expression parser** — `eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp`
is 1355/3032 (44.7%). Never entered:

| function | lines | construct |
|---|---:|---|
| `parseSymbolArc` | 104 | an arc inside a symbol definition |
| `parseSchTextBoxContent` | 95 | `(text_box …)` on a sheet |
| `parseSchTable` | 90 | `(table …)` |
| `parseSymbolTextBox` | 86 | text box inside a symbol |
| `parseTITLE_BLOCK` | 69 | `(title_block …)` |
| `parseSchSymbolInstances` | 51 | `(symbol_instances …)` |
| `parseSymbolBezier` | 47 | bezier in a symbol |
| `parseImage` | 44 | `(image …)` embedded bitmap |
| `parseSchRuleArea` | 42 | `(rule_area …)` |
| `parseGroup` | 42 | `(group …)` |
| `parseSchPolyLine` | 41 | `(polyline …)` on a sheet |
| `parseSymbolText` | 33 | text inside a symbol |
| `parseBusAlias` | 28 | `(bus_alias …)` |
| `parseBodyStyles` | 13 | DeMorgan alternate body style |

**Independently cross-checked.** Grepping the fixtures for those tokens agrees exactly
with gcov — `title_block`, `net_class`, `group`, `barcode`, `padstack`, `teardrops`,
`target`, `image`, `table`, `model`, `bus_alias`, `rule_area` each appear in **0**
fixture files:

```bash
for tok in title_block net_class group barcode padstack teardrops target image table \
           model bus_alias rule_area; do
  echo "$tok: $(grep -rl "($tok" suites/ --include=*.kicad_pcb --include=*.kicad_sch \
        --include=*.kicad_sym --include=*.kicad_mod | wc -l) fixture files"
done
```

**ERC — 90/1797 (5.0%), effectively unmeasured.** `eeschema/erc/erc.cpp` is **13/1209 =
1.1%**; `erc_item.cpp` 0/130 and `erc_report.cpp` 0/115 are flat zero. `sch erc` is
never invoked by any case (`command_sch_erc.cpp::doPerform` 0.00% of 37). All 22
`ERC_TESTER::Test*` methods are dead. This is the single largest gap in the suite and
M2's stated exit criterion.

**DRC — 2445/11036 (22.2%) excluding the rule editor.** `drc_engine.cpp` is 41.1%, so
the engine runs; what is missing is violations to find and rules to parse:

| file | covered | what would reach it |
|---|---:|---|
| `drc_rule_parser.cpp` | **0/505** | a custom `.kicad_dru` rule file — nothing parses one |
| `drc_creepage_utils.cpp` | **0/1712** | a `creepage` constraint (needs a custom rule) |
| `drc_test_provider_physical_clearance.cpp` | 14/415 (3.4%) | a `physical_clearance` rule |
| `drc_test_provider_library_parity.cpp` | 20/508 (3.9%) | a board whose footprints link to a library |
| `drc_test_provider_schematic_parity.cpp` | 9/198 (4.5%) | `pcb drc --schematic-parity` with a netlist |
| `drc_test_provider_diff_pair_coupling.cpp` | 24/384 (6.2%) | a differential pair + coupling rule |
| `drc_test_provider_track_angle.cpp` | 9/83 (10.8%) | a `track_angle` constraint |
| `drc_test_provider_zone_connections.cpp` | 20/160 (12.5%) | a zone/pad connection violation |
| `drc_test_provider_text_dims.cpp` | 21/141 (14.9%) | a `text_height`/`text_thickness` violation |
| `drc_test_provider_copper_clearance.cpp` | 120/733 (16.4%) | an actual clearance violation (only clean boards today) |
| `drc_test_provider_courtyard_clearance.cpp` | 33/193 (17.1%) | two overlapping courtyards |
| `drc_item.cpp` | 23/119 (19.3%) | more distinct violation *types* — this file is the message table |

**Plotters and exporters.** `common/plotters` is 1080/4933 (21.9%): `SVG_plotter` 70.0%
and `GERBER_plotter` 61.2% are exercised, but `PDF_plotter.cpp` **0/1433**,
`DXF_plotter.cpp` **0/651**, `pdf_outline_font.cpp` **0/327** and `pdf_stroke_font.cpp`
**0/298** are untouched, and `PS_plotter.cpp` is 81/427 (19.0% — only the parts GERBER
inherits). In `pcbnew/exporters`, `gendrill_gerber_writer.cpp` **0/315** and
`gerber_placefile_writer.cpp` **0/165** are entirely unreached because the adapter only
ever asks for the default Excellon drill and CSV position formats.

**Netlist formats.** `netlist_exporter_xml.cpp` is 508/723 (70.3%) — well covered,
because every schematic case's `summary` goes through it. Every other format is zero:
`spice` 0/423, `allegro` 0/398, `cadstar` 0/133, `pads` 0/119, `orcadpcb2` 0/68.

---

## 5. Prioritized gap list

Grouped so parallel agents can pick up a whole group without colliding. Each entry names
the KiCad code it would newly execute and a one-line `concept` for the `case.toml`.

### Group A — ERC (largest single gap; M2 exit criterion)

| # | proposed case | newly exercises | concept |
|---|---|---|---|
| A1 | `suites/erc/pin-conflict-two-outputs` | `eeschema/erc/erc.cpp:ERC_TESTER::TestPinToPin` (1017), `erc_item.cpp`, `erc_report.cpp` | Two output pins driving the same net are reported as a `pin_to_pin` ERC violation. |
| A2 | `suites/erc/unconnected-pin` | `ERC_TESTER::TestNoConnectPins` (926) + `erc_report.cpp` JSON writer | A pin left unconnected, and one covered by a no-connect flag, differ in ERC severity. |
| A3 | `suites/erc/duplicate-sheet-names` | `ERC_TESTER::TestDuplicateSheetNames` (143) | Two sibling sheets with the same name are an ERC error. |
| A4 | `suites/erc/similar-labels` | `ERC_TESTER::TestSimilarLabels` (1579) | Labels differing only in case are reported as similar-label warnings. |
| A5 | `suites/erc/missing-units` | `ERC_TESTER::TestMissingUnits` (605), `TestMultUnitPinConflicts` (1288) | A multi-unit symbol with one unit unplaced is reported as a missing unit. |
| A6 | `suites/erc/off-grid-endpoints` | `ERC_TESTER::TestOffGridEndpoints` (1972) | A wire endpoint off the connectivity grid is reported. |
| A7 | `suites/erc/lib-symbol-mismatch` | `ERC_TESTER::TestLibSymbolIssues` (1694) | A cached `lib_symbols` entry that differs from the placed symbol is an ERC issue. |

All of A also lights up `kicad/cli/command_sch_erc.cpp::doPerform` (0.00% of 37) and
`erc_settings.cpp`'s severity plumbing. A1 alone is the highest value per unit effort.

### Group B — DRC rules and real violations

| # | proposed case | newly exercises | concept |
|---|---|---|---|
| B1 | `suites/drc/custom-rule-clearance` (board + `.kicad_dru`) | `pcbnew/drc/drc_rule_parser.cpp` **0/505** (whole file), `drc_rule.cpp`, `drc_rule_condition.cpp` | A custom DRC rule in `.kicad_dru` overrides the netclass clearance for one net. |
| B2 | `suites/drc/clearance-violation` | `drc_test_provider_copper_clearance.cpp` (120/733) violation-reporting paths, `drc_item.cpp` | Two tracks closer than the board's minimum clearance produce a `clearance` violation naming both items. |
| B3 | `suites/drc/courtyard-overlap` | `drc_test_provider_courtyard_clearance.cpp` (33/193) | Two footprints whose courtyards overlap produce a `courtyards_overlap` violation. |
| B4 | `suites/drc/unconnected-net` | `drc_test_provider_connectivity.cpp` (63/161), `ratsnest` | A net with two pads and no track produces an `unconnected_items` violation. |
| B5 | `suites/drc/hole-violations` | `drc_test_provider_hole_size.cpp` (35/109), `drc_test_provider_hole_to_hole.cpp` (50/135) | An undersized drill and two holes closer than the hole-to-hole minimum are each reported. |
| B6 | `suites/drc/text-dims-violation` | `drc_test_provider_text_dims.cpp` (21/141) | Silkscreen text below the minimum height/thickness is reported. |
| B7 | `suites/drc/physical-clearance-rule` | `drc_test_provider_physical_clearance.cpp` **14/415** | A `physical_clearance` custom rule is evaluated against a mechanical item. |
| B8 | `suites/drc/creepage-rule` | `drc_creepage_utils.cpp` **0/1712** — the single largest uncovered non-GUI file in `drc` | A `creepage` constraint across a slot produces a creepage violation. |
| B9 | `suites/drc/refill-zones` (uses `pcb drc --refill-zones`) | `pcbnew/zone_filler.cpp` **0/1991 — the largest uncovered non-GUI file in the whole tree that our suite plausibly should reach.** All four zone fixtures ship a pre-computed `(filled_polygon …)`, so `ZONE_FILLER::Fill` is never entered; `--refill-zones` exists (`command_pcb_drc.cpp:41 ARG_ZONE_FILL`) and no case passes it | Refilling zones before DRC reproduces the stored fill, and a stale fill is corrected. |
| B10 | `suites/drc/schematic-parity` | `drc_test_provider_schematic_parity.cpp` **9/198** | A board whose netlist disagrees with the schematic reports parity errors. |
| B11 | `suites/drc/diff-pair-coupling` | `drc_test_provider_diff_pair_coupling.cpp` **24/384**, `drc_test_provider_matched_length.cpp` (42/242) | A differential pair violating its coupling/skew rule is reported. |

B9 needs an adapter capability the adapter does not currently expose (the `drc` verb
passes no `--refill-zones`); note that as part of the case's design, and do not change
`adapters/` without a decision entry.

### Group C — board-format constructs the parser never sees

Each is a small hand-authored `.kicad_pcb` under `suites/board-parse/`.

| # | proposed case | newly exercises | concept |
|---|---|---|---|
| C1 | `board-title-block` | `parsePCB_TITLE_BLOCK` (74) — and the schematic twin C-S1 | A `(title_block …)` carries title, date, revision and company through to plot output. |
| C2 | `pad-padstack-per-layer` | `parsePadstack` **208** (largest uncovered parser function), `parseViastack` (51) | A pad with a `(padstack)` block has different geometry on front, inner and back layers. |
| C3 | `board-netclasses` | `parseNETCLASS` (60), `parseDefaults` (61), `parseDefaultTextDims` (24) | A `(net_class …)` in `(setup)` assigns per-net clearance and track width. |
| C4 | `footprint-3d-model` | `parse3DModel` (65) | A footprint's `(model …)` reference survives a parse/round-trip with its offset, scale and rotation. |
| C5 | `board-groups` | `parseGROUP` (48) and `resolveGroups` | A `(group …)` binds several items by UUID and survives a round trip. |
| C6 | `via-teardrops` | `parseTEARDROP_PARAMETERS` (47) | `(teardrops …)` parameters on a via are parsed and preserved. |
| C7 | `board-barcode` | `parsePCB_BARCODE` **99** — a **KiCad-10-only** token, exactly what a conformance suite for 10.0.5 should pin | A `(barcode …)` item is parsed and plots on the silkscreen. |
| C8 | `board-table` | `parsePCB_TABLE` (106) | A `(table …)` of cells parses with its row/column geometry. |
| C9 | `board-target-and-image` | `parsePCB_TARGET` (42), `parsePCB_REFERENCE_IMAGE` (56) | A `(target …)` alignment mark and an embedded `(image …)` are parsed. |
| C10 | `zone-per-layer-properties` | `parseZoneLayerProperty` (22), `parseZoneDefaults` (13) | A multi-layer zone overrides its fill properties on one layer only. |
| C11 | `pad-with-soldermask-layer` | `parseLayersForCuItemWithSoldermask` (15) | A copper item that also names a mask layer parses both. |

### Group D — schematic/symbol constructs the parser never sees

| # | proposed case | newly exercises | concept |
|---|---|---|---|
| D1 | `symbol-lib/symbol-arc-and-bezier` | `parseSymbolArc` **104**, `parseSymbolBezier` (47) | A symbol body drawn with an arc and a bezier renders both. |
| D2 | `symbol-lib/symbol-text-and-textbox` | `parseSymbolText` (33), `parseSymbolTextBox` (86) | Free text and a text box inside a symbol definition render with their effects. |
| D3 | `schematic-parse/sheet-text-box` | `parseSchTextBoxContent` (95) | A `(text_box …)` on a sheet wraps its content to the box width. |
| D4 | `schematic-parse/sch-polyline-and-image` | `parseSchPolyLine` (41), `parseImage` (44) | A `(polyline …)` and an embedded `(image …)` on a sheet. |
| D5 | `schematic-parse/bus-alias` | `parseBusAlias` (28) | A `(bus_alias …)` expands to its member nets in the netlist. |
| D6 | `schematic-parse/sch-rule-area` | `parseSchRuleArea` (42) | A `(rule_area …)` on a sheet is parsed and carried into ERC scope. |
| D7 | `symbol-lib/demorgan-body-style` | `parseBodyStyles` (13) | A symbol with a DeMorgan alternate body style renders both styles. |
| D8 | `schematic-parse/sch-table-and-group` | `parseSchTable` (90), `parseGroup` (42) | A `(table …)` and a `(group …)` on a sheet parse and round-trip. |
| D9 | `schematic-parse/sch-title-block` | `parseTITLE_BLOCK` (69) | A schematic `(title_block …)` reaches the plotted drawing sheet. |

### Group E — output formats and CLI surface

| # | proposed case | newly exercises | concept |
|---|---|---|---|
| E1 | rejection cases under `suites/symbol-lib/` and `suites/footprint-lib/` | `command_sym_upgrade.cpp::doPerform` **0.00%**, `command_fp_upgrade.cpp::doPerform` **0.00%** — the `parse-sym`/`parse-fp` verbs are implemented and never invoked | A malformed `.kicad_sym` / `.kicad_mod` is rejected with its parse position. |
| E2 | a `pdf` answer for one board and one schematic | `common/plotters/PDF_plotter.cpp` **0/1433**, `pdf_outline_font.cpp` **0/327**, `pdf_stroke_font.cpp` **0/298**, `command_pcb_export_pdf.cpp::doPerform` | A board and a sheet plot to PDF with embedded fonts. |
| E3 | a `dxf` answer for one board | `common/plotters/DXF_plotter.cpp` **0/651**, `command_pcb_export_dxf.cpp::doPerform` | A board plots to DXF with its layer mapping. |
| E4 | drill output in Gerber X2 format | `pcbnew/exporters/gendrill_gerber_writer.cpp` **0/315** | `pcb export drill --format gerber` emits an X2 drill file for the same holes as Excellon. |
| E5 | position output in Gerber format | `pcbnew/exporters/gerber_placefile_writer.cpp` **0/165** | `pcb export pos --format gerber` emits a component-placement job file. |
| E6 | netlist format matrix | `netlist_exporter_spice.cpp` **0/423**, `_allegro` **0/398**, `_cadstar` **0/133**, `_pads` **0/119**, `_orcadpcb2` **0/68** | The same schematic exports to each interchange netlist format. |
| E7 | `sch export bom` | `command_sch_export_bom.cpp::doPerform` **0.00% of 30**, `common/jobs/job_export_sch_bom` | A BOM export groups multi-unit symbols into one line. |

**Suggested order.** A1–A2 and B1–B2 first (they unlock the two whole subsystems at
5.0% and 22.2%); then Group C/D, which are cheap hand-authored fixtures with the
highest lines-per-case ratio; then Group E, which mostly costs new recorded answers
rather than new fixtures.

---

## 6. What this measurement cannot tell you

1. **Covered ≠ tested.** gcov records that a line *executed*, not that the suite
   *asserted* anything about its effect. `netlist_exporter_xml.cpp` at 70.3% means the
   XML exporter ran, not that its output is checked field by field.
   **This is the gap [`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md) closes**: it defines
   *asserted* as a third state between unexecuted and executed, makes each case's
   falsifiability a committed artifact the runner re-checks ([DL-0030]), and specifies the
   per-line gap report — "this KiCad code is executed by the suite and nothing asserts it"
   — that this section's numbers cannot produce ([DL-0031]). Until that lands, **every
   percentage in §3 is an upper bound**.
2. **The global 8.6% is not a quality score** — see §3.
3. **Bucket percentages are only as good as their directory patterns** — §3a shows three
   of them off by 8-25 percentage points against the question actually being asked.
   Always confirm against `coverage.json` before quoting a bucket.
4. **It is a different binary.** Debug, `-O0`, no wxPython, no stock symbol/footprint
   libraries (hence the `LIBRARY_MANAGER::LoadGlobalTables` assert spam on every
   invocation), and `-ftrivial-auto-var-init=pattern` instead of the release fill. §2
   shows both ways that changed observable behaviour. **If this image and
   `kicad/kicad:10.0.5` disagree on a result, the release image wins.**
5. **Branch percentages are indicative only** (5.8% global). Every call that can throw
   creates hidden branches; `--exclude-throw-branches` suppresses only the worst.
6. **The lcov tracefile is not in this run's artifacts.** lcov 2.0 in this image falls
   back to the pure-Perl `JSON::PP` backend (it says so in `lcov-stderr.txt`) and was
   still running after 15 minutes on a tree gcovr finished in ~5. `collect.sh` now
   bounds it (`COVERAGE_LCOV_TIMEOUT`, default 900s) so it cannot hold `focus.json`
   hostage; set `COVERAGE_SKIP_LCOV=1` to skip it, or install `JSON::XS` in the image if
   you need `coverage.info`.
7. **Function line counts in §4 are gcov's**, which counts every line the compiler
   attributes to the function (including template expansions), so they are a good
   ranking signal and a poor absolute one.

---

## 7. Tooling defects found and fixed while producing this report

Recorded because two of them silently produced a *plausible-looking* wrong answer, which
is the failure mode this project cares most about.

1. **The instrumented image was not instrumented.** `tools/coverage/Dockerfile` passed
   the coverage flags as `-DCMAKE_CXX_FLAGS_DEBUG=…`, but KiCad's own
   `CMakeLists.txt:441-446` does an unconditional non-cache
   `set( CMAKE_CXX_FLAGS_DEBUG "-g3 -ggdb3" )`, which clobbers it. `CMakeCache.txt`
   still *showed* the coverage flags, so the build looked correct; `build.ninja` carried
   `-g3 -ggdb3` and no `-fprofile-arcs`, and the resulting 26.7 GB image contained
   **zero `.gcno` files** — gcov would have reported nothing forever. Fixed by moving
   the flags to `CMAKE_C_FLAGS`/`CMAKE_CXX_FLAGS` (which KiCad only appends to) plus
   `KICAD_BUILD_SMALL_DEBUG_FILES=ON`, and by adding two build-time assertions that fail
   the build rather than the report: `grep -q profile-arcs build.ninja` after configure,
   and `.gcno` count > 1000 after compile (it now emits **1912**).
2. **`collect.sh` aborted on CMake's compiler-identification probe.** gcovr 7.2 treats
   the orphaned `CMakeFiles/*/CompilerIdCXX/*.gcno` as a fatal `no_working_dir_found`
   and refused to write any report. Fixed by removing that throwaway artifact before the
   walk and adding `--gcov-ignore-errors=no_working_dir_found`.
3. **Raw counters on a Windows bind mount made the run ~10x slower.** libgcov dumps
   ~1900 small `.gcda` files at *every* process exit, and a board case is six exits.
   Measured: `kicad-cli version` took **3.39 s** writing to a bind-mounted host directory
   versus **0.32 s** writing inside the VM. `run-suite.sh` now keeps the counters in a
   named Docker volume (`--raw-volume`, `--fresh`). On the bind mount the run managed 10
   of 77 cases in ~20 minutes before it was abandoned (a ~2.5 h projection); on the
   volume the whole 77-case suite took a **measured 4 min 46 s** (`run.log`: first
   `kicad-cli` line `23:12:18`, last `23:17:04`). The report still lands on the host.
