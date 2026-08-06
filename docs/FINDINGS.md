# Findings

This file logs KiCad 10.0.5 defects and documentation gaps discovered while recording
this repo's conformance corpus. Each entry is one finding with a reproduction command;
`DIV-NNNN` ids are referenced from `case.toml`'s `xfail` key and must not be renumbered.

---

## DIV-0001 — `pcb upgrade --force` segfaults on a truncated board instead of exiting gracefully

- **Repro:** `kicad-cli pcb upgrade --force <board.kicad_pcb with unterminated outer sexpr>`
- **Observed vs expected:** prints the correct `Failed to load board: Expecting '(' in '…', line 2, offset 1.` then SIGSEGV; expected a clean bounded `REJECT` exit.
- **Case:** still a real 10.0.5 bug, but no longer exercised by any case. `suites/board-parse/rejects-unterminated-sexpr` used to probe this path directly; it now rejects gracefully via the default `pcb export stats` probe and genuinely PASSes. (not yet filed upstream)

## DIV-0002 — `pcb export ipcd356` reads an uninitialised `int` on every via record

- **Repro:** `kicad-cli pcb export ipcd356 -o b.d356 board.kicad_pcb` (source: `pcbnew/exporters/export_d356.cpp`, via loop never sets `D356_RECORD::soldermask` before OR-ing into it, unlike the pad loop)
- **Observed vs expected:** release build prints `S3` on every via record, including boards with no pads at all — a stable but uninitialised stack read, not a computed value; an `-ftrivial-auto-var-init=pattern` build reads `S-16843009` instead.
- **Case:** none asserts `S<n>` directly; affects any board with vias, e.g. `suites/board-parse/{populated-board, blind-and-buried-vias, micro-via, four-layer-stackup, via-remove-unused-layers, zone-keepout-rule-area, board-netclasses}` (not yet filed upstream)

## DIV-0003 — `pcb drc`'s `unconnected_items` pairing is nondeterministic across identical runs

- **Repro:** `kicad-cli pcb drc --format json --units mm --severity-all board.kicad_pcb`, repeated, on a board with 3+ mutually-unconnected same-net endpoints
- **Observed vs expected:** which two items get grouped into one `unconnected_items` entry flips (~1 run in 5-7) while `violations`/`schematic_parity` stay identical; likely hash-map/set iteration order sensitive to ASLR. Not fixable by sorting — it's a membership difference, not an ordering one.
- **Case:** none committed (every `suites/drc/*` case uses an unambiguous single unconnected pair); needs a purpose-built minimal reproducer (not yet filed upstream)

## DIV-0004 — `pcb upgrade --force` silently deletes an inline `(net_class ...)` board block

- **Repro:** `kicad-cli pcb upgrade --force board.kicad_pcb` on a board with `(net_class Tight ...)` giving `NET_A` a tighter clearance than the board default
- **Observed vs expected:** exits 0, drops the `(net_class ...)` block entirely (`grep -c net_class` → 0); re-run DRC drops from 3 to 2 violations and the `clearance` finding for `NET_A` disappears. Expected: unchanged DRC result. Current kicad-cli only writes netclass data into `.kicad_pro`; the inline board block is read-only/back-compat and never re-emitted.
- **Case:** `suites/board-parse/board-netclasses`, `extra = ["drc", "roundtrip"]`, `xfail = "DIV-0004"` — still exercised. (not yet filed upstream)

## DIV-0005 — `pcb upgrade --force` silently deletes a thru-hole pad's `(drill 0)`

- **Repro:** `kicad-cli pcb upgrade --force board.kicad_pcb` on a `thru_hole` pad with explicit `(drill 0)`
- **Observed vs expected:** exits 0, the `(drill 0)` token is gone; DRC on the re-serialized board reports 2 *different* findings (`drill_out_of_range`, `padstack_invalid`) instead of the original `through_hole_pad_without_hole` — fabricates new findings rather than merely dropping one, and `stats.json`'s pad count also shifts.
- **Case:** `suites/drc/through-hole-pad-without-hole`, `xfail = "DIV-0005"` — still exercised. (not yet filed upstream)

## DIV-0006 — `sch upgrade --force` silently deletes `(bus_alias ...)` blocks, with no other observable trace

- **Repro:** `kicad-cli sch upgrade --force sheet.kicad_sch` on a schematic with a `(bus_alias "MYBUS" (members "A" "B"))` block
- **Observed vs expected:** exits 0, block gone (`grep -c bus_alias` → 0); `sch export netlist` and `sch erc --severity-all` are byte-identical with or without the alias — no compensating signal anywhere shows the loss.
- **Case:** documented but no longer mechanically tested. `suites/schematic-parse/schematic-bus-alias` used to carry `extra = ["roundtrip"]` and `xfail = "DIV-0006"`, backed by a bespoke `(bus_alias ...)` census read out of the schematic's raw s-expression text; the round-trip invariant is now built from plain kicad-cli exports only, which never observe the alias, so the case dropped `roundtrip`/`xfail` rather than XPASS and fail the build. It still records `netlist.net` + `render.svg` and still carries its perturbation. (not yet filed upstream)

## DIV-0007 — `pcb drc`'s `violations[]` order is nondeterministic across identical runs

- **Repro:** `kicad-cli pcb drc --format json --units mm --severity-all -o d.json board.kicad_pcb`, repeated, on a board with two through-hole pads on different nets at the same coordinate
- **Observed vs expected:** the two `solder_mask_bridge` findings — `Front solder mask aperture bridges items with different nets` and `Rear …` — are both always present, but which one is serialized first flips between runs: 12 identical runs gave Front-first 8 times and Rear-first 4. The pads' geometry is symmetric across both mask layers, so nothing breaks the tie. Expected: a stable serialization order for a stable input. Distinct from DIV-0003, which is a *membership* difference in `unconnected_items` and therefore not fixable by sorting; this one is pure ordering.
- **Case:** `suites/drc/holes-co-located`. Absorbed by the runner rather than xfailed — `runner/normalize.py` now sorts the DRC/ERC finding arrays (`violations`, `unconnected_items`, `schematic_parity`, `items`) before comparison, so order carries no weight. An implementation under test likewise must not be judged on it. (not yet filed upstream)

---

## Doc gaps

- **net ordinals** — docs require a top-level `(net ORDINAL "NAME")` section that KiCad 10 never writes; on read, resolution is inconsistent per element (`segment`/`via` resolve ordinals, `zone` accepts and silently discards them, `pad` rejects with `Expecting net name`). Repro: `kicad-cli pcb export stats --format json` against hand-authored per-element fixtures.
- **format version codes** — docs give no actual `(version …)` values; 10.0.5 writes `20260206` (`.kicad_pcb`/`.kicad_mod`), `20260306` (`.kicad_sch`), `20251024` (`.kicad_sym`), all tagged `(generator_version "10.0")`. Repro: `kicad-cli pcb upgrade --force old.kicad_pcb; grep version`.
- **`suppress_zeroes` spelling** — docs say `suppress_zeros`; the real token is `suppress_zeroes`, and the documented spelling makes the board fail to load with `Unknown format token: 'symbol'`.
- **layer ordinals are advisory** — numbers in `(layers …)` are ignored on read and re-derived on write (copper first, then non-copper, then user layers; `In N = 2+2N`), a scheme absent from the docs, whose own example still shows pre-6 numbering (`F.Cu = 15`).
- **user-layer count** — docs say 9 (`User.1`–`User.9`); the real limit is 45 (`User.46` → `Failed to load board: ... not in fixed layer hash`).
- **`(layers …)` structural rules** — undocumented: copper entries must be listed first (counting stops at the first non-copper entry) and the copper count must be even and ≥2, else `Failed to load board: N is not a valid layer count`.
- **copper vs non-copper rename asymmetry** — a copper layer's name field is positional and silently reassigned/swapped (declaring back-first silently swaps F.Cu/B.Cu); a fixed non-copper layer with an unrecognized name hard-rejects. The real rename path is the optional 4th field, e.g. `(0 "F.Cu" signal "MyCopper")`.
- **`(net_class …)` doc shape is the only accepted one** — the sexpr-pcb doc's legacy example (unquoted name, description string, `via_dia`/`via_drill`) is still accepted at top level in 10.0.5, but a modernized block (quoted name, `via_diameter`) is rejected (`Expecting 'symbol'` at top level, `Unexpected net_class` inside `(setup …)`). Exercised by `suites/board-parse/board-netclasses`.
- **`(padstack …)` grammar unpublished** — `(mode front_inner_back)`/`(mode custom)` and per-layer overrides have no spec on any dev-docs page. Exercised by `suites/board-parse/pad-padstack-per-layer`.
- **barcode type normalization** — `pcb upgrade --force` rewrites a hand-authored `(barcode (type qrcode) …)` to canonical `(type qr)` and adds `(hide no)`/`(knockout no)` defaults, non-destructively. Exercised by `suites/board-parse/board-barcode`.
- **`Rescue` layer** — an accepted layer name that is silently dropped from `(layers …)` on `pcb upgrade --force`; not in the canonical-layer table at all.
- **UUID validation** — docs require v4 UUIDs; nothing validates them, but a syntactically unparseable one is silently regenerated on save with no warning, breaking sheet-instance-path matching in schematics.
- **future-version rejection has two code paths** — `(version 99999999)` fails date parsing; a plausible future date like `(version 20990101)` instead gives a helpful "upgrade KiCad to version 10.0 or later" message.
- **drill offset direction is backwards in the docs** — docs say `pad (drill (offset X Y))` moves the hole; it actually leaves the hole at the pad's `(at)` and moves the copper pad shape instead. Highest-severity entry: produces physically wrong boards that no parse check catches.
- **undocumented board tokens** — `generator_version`, `embedded_fonts`, `tenting`, `legacy_teardrops`, `unlocked` appear in 19/19 shipped demo boards; also `thermal_bridge_angle`, `generated` (length-tuning), `covering`/`plugging`/`capping`/`filling`, `component_class`, etc. Conversely, documented `pcbplotparams` tokens (`svguseinch`, `excludeedgelayer`, `viasonmask`, `plotreference`, …) no longer appear in output.
- **schematic/symbol-lib version rejection gives no detail** — a too-new board version names the date and remedy; a too-new schematic just says `Failed to load schematic` (exit 3); a too-new symbol lib says `Unable to load library` (exit 2, different exit class).
- **schematic Y-axis is flipped** — for an unrotated/unmirrored instance, `sheet_x = inst_x + local_x` but `sheet_y = inst_y − local_y`; the docs describe both coordinate systems and never state the relation.
- **local labels are sheet-scoped too** — hierarchical and plain local labels both produce path-prefixed net names (`/sub/NAME`); only global labels are bare.
- **repeated sheet instances collapse references** — when one subsheet file is instantiated twice, `sch export netlist --format kicadxml` assigns every instance the reference from the *first* `(path …)` entry in the symbol's `(instances)` list (nets stay correct per instance).
- **`bus_alias` dropped on round-trip, and inert while present** — see DIV-0006; the alias has zero effect on ERC/netlist even before round-tripping.
- **undocumented schematic tokens** — docs list only `polyline`/`text` as graphic items and restrict `fields_autoplaced` to `global_label`/`sheet`; real demos widely use `dnp`, `exclude_from_sim`, `rectangle`, `circle`, `arc`, `mirror`, `text_box`, `image`, `bus_alias`, `rule_area`, `table`, `netclass_flag`, and more.
- **symbol property `(id N)`** — docs describe it as required and unique; the current format has no such field — it's silently discarded on `sym upgrade --force`, and duplicate ids load without error.
- **`(pin_numbers hide)` syntax is stale** — docs show the flat form; every stock library (413/413 blocks) uses the nested `(pin_numbers (hide yes))` instead.
- **5th mandatory symbol property** — docs list 4 mandatory properties (Reference/Value/Footprint/Datasheet); the writer unconditionally emits a 5th (Description) but the reader requires none of the five.
- **undocumented symbol/footprint tokens** — `show_name`, `do_not_autoplace`, `exclude_from_sim`, `in_pos_files`, `duplicate_pin_numbers_are_jumpers`, `embedded_fonts`, `generator_version` present in all 223 stock libraries; footprint `zone_connect` value `3` is documented in the zone section but omitted from the footprint/pad `zone_connect` lists.
- **default DRC/ERC report filename and location are both wrong** — docs say the report is written beside the input as `<input>.rpt`/`.json`; it's actually written to the CWD as `<input-basename>-drc.json` / `-erc.json`.
- **`--save-board` help text overstates enforcement** — `--help` says "must be used with --refill-zones" (reads as enforced); online docs correctly say the save is simply skipped. Without `--refill-zones` it exits 0, prints nothing, and does not save.
- **`--save-board` output is not byte-stable** — refilling the *same* board twelve times with `kicad-cli pcb drc --refill-zones --save-board` produced twelve distinct files whenever the input had a footprint written without its mandatory `Reference`/`Value`/… property blocks: each save adds them back with a freshly-minted uuid. A board whose footprints already carry those blocks saved byte-identically all twelve times. The zone geometry was identical in all 48 runs either way; this is a property-uuid effect, not a fill effect. Exercised by `suites/zone-fill/*` (whose recorded answer is a projection of the fills, so it is blind to this).
- **zone fill applies pad clearance with a constant 500 nm outward epsilon, but thermal gap exactly** — a 2x2 mm pad at (12,12) inside a foreign-net zone with `(connect_pads (clearance 0.5))` refills to a void spanning `10.4995 .. 13.5005`, i.e. 0.5005 mm from each pad edge, not 0.5; raising the clearance to 0.8 gives `10.1995 .. 13.8005`, so the excess is a fixed 0.0005 mm and not a proportional one. The same board's `(thermal_gap 0.5)` around a 2 mm circular same-net pad gives a void of exactly `8.5 .. 11.5` — no epsilon at all. An implementation that applies the nominal clearance, or that applies the same epsilon to both, differs from KiCad by one integer-nanometre unit on every clearance boundary. Repro: `suites/zone-fill/refill-clears-foreign-net-pad` vs its `perturb/clearance-widened/` overlay.
- **malformed `-D` gives zero diagnostic** — `kicad-cli pcb drc -D JUSTAKEY ...` (no `=value`, or an empty value) exits 1 with 0 bytes on stdout and stderr.
- **exit codes 1/2/3 are undocumented; only 5 appears, and only online** — 1 = argument/option validation, 2 = exists but unusable as a library, 3 = input missing or won't load, 5 = `--exit-code-violations` with violations found.
- **`sym export svg` silently no-ops on the wrong file type** — given a `.kicad_pcb`/`.kicad_sch`, exits 0, writes nothing, prints nothing on either stream.
- **doubled quotes in board parse diagnostics; schematic parse gives none** — board `Expecting` messages double the apostrophes around the token (`Expecting ''('' `); schematic/library loaders give no positional detail at all.
- **`.kicad_prl` written beside the input by every `pcb` subcommand** — even on nonzero exit; no `sch` subcommand does this.
- **KiCad-9 deprecation banner on stdout with ANSI escapes** — `pcb export svg`/`pcb export dxf` print a colored deprecation banner to stdout whenever neither `--mode-single` nor `--mode-multi` is given; `NO_COLOR`/`TERM=dumb` do not suppress it.
- **`.kicad_dru`/`.kicad_pro` silently change DRC results, undocumented in the CLI reference** — `kicad-cli pcb drc` picks up a sibling `<board-stem>.kicad_dru` and `<board-stem>.kicad_pro`, changing violations, severities, and hence `--exit-code-violations`'s exit code.
- **ERC JSON scales every dimension by 1/100** — `sch erc --format json` positions and description-embedded lengths are exactly 100x too small in every unit; `--format report` and `pcb drc --format json` are both correct.
- **`via_diameter` cannot be silenced** — emitted as a violation `type` but is not a valid `board.design_settings.rule_severities` key; setting it to `"ignore"` in `.kicad_pro` has no effect, unlike `via_dangling`/`lib_footprint_issues` which work.
- **dead severity-identifier keys in KiCad's own shipped demo projects** — `overlapping_pads`, `zone_has_empty_net`, `bus_label_syntax`, `conflicting_netclasses`, `global_label_dangling`, `overlapping_rule_areas` appear in shipped demo `.kicad_pro` files but are silently ignored. `hole_near_hole`, also present in demos, is by contrast a live legacy alias for `hole_to_hole`.
- **KiCad 10 build dependencies exceed Debian's KiCad-9 metadata** — `apt-get build-dep kicad` on trixie is insufficient; also need `libspnav-dev`, `libwxgtk-webview3.2-dev`, `libpoppler-cpp-dev`/`libpoppler-glib-dev`/`libpoppler-private-dev`, and `libpixman-1-dev`.
