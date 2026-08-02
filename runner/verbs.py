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
        "note": "golden-file",
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
