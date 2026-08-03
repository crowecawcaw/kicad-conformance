"""The capability-verb table (DESIGN.md §2), shared by the reference adapter's
`capabilities` response and the coverage proxy (§7a) so the two never drift apart.

Each entry: the verb name, the kicad-cli subcommand(s) it maps to, and a short mapping
note (matches the DESIGN §2 table). This is *documentation-as-data* — the adapter and
the coverage report both read it instead of each keeping their own copy.
"""
from __future__ import annotations

VERB_TABLE: dict[str, dict[str, str]] = {
    "version": {
        "cli": "version --format plain",
        "note": "adapter/oracle identity",
    },
    "parse-sch": {
        "cli": "sch upgrade --force",
        "note": "loads + canonicalizes; exit polarity is what `parse-*` cares about",
    },
    "parse-pcb": {
        "cli": "pcb upgrade --force",
        "note": "loads + canonicalizes; exit polarity is what `parse-*` cares about",
    },
    "parse-sym": {
        "cli": "sym upgrade --force -o <out>",
        "note": "library-file upgrade",
    },
    "parse-fp": {
        "cli": "fp upgrade --force -o <out> <in .pretty dir>",
        "note": "footprint LIBRARY (.pretty dir) upgrade, never a lone .kicad_mod",
    },
    "upgrade": {
        "cli": "(same subcommand as the matching parse-*, golden-compared)",
        "note": "canonical re-save, compared byte-exact after normalization",
    },
    "erc": {
        "cli": "sch erc --format json --severity-all -o <out>/erc.json",
        "note": "structured violation set",
    },
    "drc": {
        "cli": "pcb drc --format json --units mm --severity-all -o <out>/drc.json",
        "note": "structured violation set",
    },
    "netlist": {
        "cli": "sch export netlist --format kicadsexpr -o <out>/netlist.net",
        "note": "structured net -> node membership",
    },
    "export-gerbers": {
        "cli": "pcb export gerbers --layers <pinned> --no-protel-ext -o <out>/",
        "note": "golden-dir; layer set is a per-case parameter (DESIGN §2b)",
    },
    "export-drill": {
        "cli": "pcb export drill --generate-report --report-path <r> -o <out>/",
        "note": "golden-dir (Excellon + report)",
    },
    "export-pos": {
        "cli": "pcb export pos --format csv --side both --units mm -o <out>/pos.csv",
        "note": "L2-reducible (structured, printed-quantum tolerance, VALIDATION §3.4); "
                "the older golden-file compare mode remains available for KiCad-regression",
    },
    "export-stats": {
        "cli": "pcb export stats --format json -o <out>/stats.json",
        "note": "structured (VALIDATION §3.3): drop metadata, field/string compare",
    },
    "export-ipcd356": {
        "cli": "pcb export ipcd356 -o <out>/board.d356",
        "note": "structured (VALIDATION §3.5): board net->pad membership graph",
    },
    "export-svg-pcb": {
        "cli": "pcb export svg --layers <L> --page-size-mode 2 --exclude-drawing-sheet "
               "--black-and-white -o <out>/render.svg",
        "note": "image (L3, VALIDATION §4): normalized-SVG byte-exact; <L> is a per-case "
                "`args` parameter like the gerber layer set (DESIGN §2b)",
    },
    "export-svg-sch": {
        "cli": "sch export svg --no-background-color -o <out>/ (writes <stem>.svg)",
        "note": "image (L3)",
    },
    "export-svg-sym": {
        "cli": "sym export svg --black-and-white -o <out>/ (writes <sym>.svg)",
        "note": "image (L3) -- deferred case-authoring to M5 (library SVG); verb implemented now",
    },
    "export-svg-fp": {
        "cli": "fp export svg --black-and-white -o <out>/ (writes <fp>.svg; input is a "
               ".pretty dir, never a lone .kicad_mod)",
        "note": "image (L3) -- deferred case-authoring to M5 (library SVG); verb implemented now",
    },
    "export-step": {
        "cli": "pcb export step",
        "note": "DEFERRED (DL-0012) -- reserved, unused",
    },
    "bom": {
        "cli": "sch export bom -o <out>/bom.csv",
        "note": "golden-file",
    },
}

# Verbs the reference kicad adapter actually implements (export-step is reserved per
# DL-0012 and deliberately excluded so it skip-and-counts rather than pretending to run).
IMPLEMENTED_VERBS = tuple(v for v in VERB_TABLE if v != "export-step")
