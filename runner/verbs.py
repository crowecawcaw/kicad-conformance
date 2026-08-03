"""The capability-verb table (DESIGN.md §2), shared by the reference adapter's
`capabilities` response and the coverage proxy (§7a) so the two never drift apart.

Each entry: the verb name, the kicad-cli subcommand(s) it maps to, and a short mapping
note (matches the DESIGN §2 table). This is *documentation-as-data* — the adapter and
the coverage report both read it instead of each keeping their own copy.

DL-0022/DL-0023/DL-0024 retired `upgrade`/`bom` and the four `export-svg-*` verbs
(collapsed into one `render`, dispatching on the input suffix); `export-pos`/
`export-stats`/`export-ipcd356` lost their `export-` prefix; `model` is new.
"""
from __future__ import annotations

VERB_TABLE: dict[str, dict[str, str]] = {
    "version": {
        "cli": "version --format plain",
        "note": "adapter/oracle identity",
    },
    "parse-sch": {
        "cli": "sch upgrade --force",
        "note": "loads + canonicalizes on a scratch copy; exit polarity only (DL-0024)",
    },
    "parse-pcb": {
        "cli": "pcb upgrade --force",
        "note": "loads + canonicalizes on a scratch copy; exit polarity only (DL-0024)",
    },
    "parse-sym": {
        "cli": "sym upgrade --force -o <out>",
        "note": "library-file upgrade; exit polarity only",
    },
    "parse-fp": {
        "cli": "fp upgrade --force -o <out> <in .pretty dir>",
        "note": "footprint LIBRARY (.pretty dir) upgrade, never a lone .kicad_mod; exit polarity only",
    },
    "model": {
        "cli": "composes pcb export stats+pos+ipcd356 (board) or sch export netlist (schematic)",
        "note": "one merged model.json (VALIDATION §4); the default check for a happy board/schematic case",
    },
    "erc": {
        "cli": "sch erc --format json --severity-all -o <out>/erc.json",
        "note": "normalized violation set",
    },
    "drc": {
        "cli": "pcb drc --format json --units mm --severity-all -o <out>/drc.json",
        "note": "normalized violation set",
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
        "cli": "pcb|sch|sym|fp export svg (dispatches on input suffix; --layers from args for pcb)",
        "note": "normalized-SVG byte-exact, VALIDATION §6",
    },
    "export-gerbers": {
        "cli": "pcb export gerbers --layers <pinned> --no-protel-ext -o <out>/",
        "note": "exit only -- no comparator exists (VALIDATION §7.1, DL-0024)",
    },
    "export-drill": {
        "cli": "pcb export drill --generate-report --report-path <r> -o <out>/",
        "note": "exit only -- no comparator exists (VALIDATION §7.2, DL-0024)",
    },
    "export-step": {
        "cli": "pcb export step",
        "note": "DEFERRED (DL-0012) -- reserved, unused",
    },
}

# Verbs the reference kicad adapter actually implements (export-step is reserved per
# DL-0012 and deliberately excluded so it skip-and-counts rather than pretending to run).
IMPLEMENTED_VERBS = tuple(v for v in VERB_TABLE if v != "export-step")
