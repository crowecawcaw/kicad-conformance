"""The capability-verb table (DESIGN.md §2), shared by the reference adapter's
`capabilities` response and the coverage proxy (§7a) so the two never drift apart.

Each entry: the verb name, the kicad-cli subcommand(s) it maps to, and a short mapping
note (matches the DESIGN §2 table). This is *documentation-as-data* — the adapter and
the coverage report both read it instead of each keeping their own copy.

**Verbs are an adapter-internal vocabulary only, since [DL-0025].** A `case.toml` names
none of these — the runner derives which verbs to run from the input's file suffix
(`runner/engine.py`'s `battery_for`/`answer_for_extra`/`LOADER_VERB`). This table is the
adapter contract a second implementation still answers, not a menu a case picks from.

DL-0022/DL-0023/DL-0024 retired `upgrade`/`bom` and the four `export-svg-*` verbs
(collapsed into one `render`, dispatching on the input suffix); `export-pos`/
`export-stats`/`export-ipcd356` lost their `export-` prefix; `model` (DL-0022) was
renamed `summary` (DL-0028); `export-gerbers`/`export-drill` are no longer exit-only —
they produce byte-compared directory answers again (DL-0026).
"""
from __future__ import annotations

VERB_TABLE: dict[str, dict[str, str]] = {
    "version": {
        "cli": "version --format plain",
        "note": "adapter/oracle identity",
    },
    "parse-sch": {
        "cli": "sch upgrade --force",
        "note": "loads + canonicalizes on a scratch copy; exit polarity only -- the loader a schematic-parse failure/ case runs",
    },
    "parse-pcb": {
        "cli": "pcb upgrade --force",
        "note": "loads + canonicalizes on a scratch copy; exit polarity only -- the loader a board-parse failure/ case runs",
    },
    "parse-sym": {
        "cli": "sym upgrade --force -o <out>",
        "note": "library-file upgrade; exit polarity only",
    },
    "parse-fp": {
        "cli": "fp upgrade --force -o <out> <in .pretty dir>",
        "note": "footprint LIBRARY (.pretty dir) upgrade, never a lone .kicad_mod; exit polarity only",
    },
    "summary": {
        "cli": "composes pcb export stats+pos+ipcd356 (board) or sch export netlist (schematic)",
        "note": "one merged summary.json (VALIDATION §4, DL-0028); the standard answer for every happy board/schematic case",
    },
    "erc": {
        "cli": "sch erc --format json --severity-all -o <out>/erc.json",
        "note": "normalized violation set (opt-in projection, `extra = [\"erc\"]`)",
    },
    "drc": {
        "cli": "pcb drc --format json --units mm --severity-all -o <out>/drc.json",
        "note": "normalized violation set (opt-in projection, `extra = [\"drc\"]`)",
    },
    "netlist": {
        "cli": "sch export netlist --format kicadsexpr|kicadxml -o <out>/netlist.net",
        "note": "net -> node membership (opt-in projection, VALIDATION §5)",
    },
    "pos": {
        "cli": "pcb export pos --format csv --side both --units mm -o <out>/pos.csv",
        "note": "placement rows (opt-in projection); printed-quantum tolerance, VALIDATION §4.1/§5",
    },
    "stats": {
        "cli": "pcb export stats --format json -o <out>/stats.json",
        "note": "inventory report (opt-in projection): drop metadata + computed float geometry, VALIDATION §4.1",
    },
    "ipcd356": {
        "cli": "pcb export ipcd356 -o <out>/board.d356",
        "note": "board net->pad membership graph (opt-in projection), VALIDATION §4.1/§5",
    },
    "render": {
        "cli": "pcb|sch|sym|fp export svg (dispatches on input suffix; pcb is pinned to --layers F.Cu)",
        "note": "normalized-SVG byte-exact, VALIDATION §6; the standard answer for board/schematic/library",
    },
    "export-gerbers": {
        "cli": "pcb export gerbers -o <out>/ (no --layers, no --no-protel-ext: KiCad's own layer set)",
        "note": "directory of gerbers, compared byte-for-byte after normalization; the standard answer for every board (VALIDATION §7, DL-0026)",
    },
    "export-drill": {
        "cli": "pcb export drill -o <out>/ (no map, no report, no --excellon-separate-th)",
        "note": "directory holding one .drl, compared byte-for-byte after normalization; the standard answer for every board (VALIDATION §7, DL-0026)",
    },
    "export-step": {
        "cli": "pcb export step",
        "note": "DEFERRED (DL-0012) -- reserved, unused",
    },
}

# Verbs the reference kicad adapter actually implements (export-step is reserved per
# DL-0012 and deliberately excluded so it skip-and-counts rather than pretending to run).
IMPLEMENTED_VERBS = tuple(v for v in VERB_TABLE if v != "export-step")
