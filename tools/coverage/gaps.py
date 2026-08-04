#!/usr/bin/env python3
"""Two views of a gcovr --json-summary that `focus.json` cannot give:

  corrected   the four subsystem buckets docs/COVERAGE.md §3a says are misleading as
              printed, recomputed with the exclusions that make them answer the
              question actually being asked (rule-editor GUI out of `drc`,
              third-party importers out of `io/board`/`io/schematic`, 3D out of
              `export/plot`).
  dead        every file with line_covered == 0, largest first, after dropping the
              areas docs/COVERAGE.md §4a already classified as unreachable from a
              CLI run. This is the round-N target list's raw material -- it is NOT
              a to-do list until each entry is triaged.

    python3 tools/coverage/gaps.py coverage.json corrected
    python3 tools/coverage/gaps.py coverage.json dead [--top N] [--min-lines N]
"""
from __future__ import annotations

import argparse
import json
import sys

# docs/COVERAGE.md §4a: legitimately unreachable from a `kicad-cli` run. Excluded from
# the `dead` list so it names only code a case could plausibly reach. Anything removed
# here must be justified in COVERAGE.md §4a, not just here.
UNREACHABLE = (
    "/dialogs/", "/widgets/", "/tools/", "common/gal", "preview_items", "3d-viewer",
    "pcbnew/drc/rule_editor/",
    "pcbnew/exporters/step/", "pcbnew/exporters/u3d/", "exporter_vrml", "export_idf",
    "pcbnew/router/",
    "pcbnew/pcb_io/altium", "pcbnew/pcb_io/cadstar", "pcbnew/pcb_io/eagle",
    "pcbnew/pcb_io/pads", "pcbnew/pcb_io/allegro", "pcbnew/pcb_io/easyeda",
    "pcbnew/pcb_io/fabmaster", "pcbnew/pcb_io/ipc2581", "pcbnew/pcb_io/odbpp",
    "pcbnew/pcb_io/pcad", "pcbnew/pcb_io/geda",
    "eeschema/sch_io/altium", "eeschema/sch_io/cadstar", "eeschema/sch_io/eagle",
    "eeschema/sch_io/ltspice", "eeschema/sch_io/easyeda", "eeschema/sch_io/database",
    "eeschema/sch_io/http_lib", "eeschema/sch_io/pads",
    "pcbnew/drc/drc_interactive_courtyard_clearance",
)

CORRECTED = [
    ("drc",          ("pcbnew/drc",),        ("pcbnew/drc/rule_editor/",)),
    ("io/board",     ("pcbnew/pcb_io",),
     tuple(p for p in UNREACHABLE if p.startswith("pcbnew/pcb_io"))),
    ("io/schematic", ("eeschema/sch_io",),
     tuple(p for p in UNREACHABLE if p.startswith("eeschema/sch_io"))),
    ("export/plot",  ("pcbnew/exporters", "common/plotters", "eeschema/printing"),
     ("pcbnew/exporters/step/", "pcbnew/exporters/u3d/", "exporter_vrml", "export_idf")),
]


def files(path):
    for f in json.load(open(path)).get("files", []):
        yield (f["filename"].replace("\\", "/"),
               f.get("line_covered", 0), f.get("line_total", 0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("mode", choices=["corrected", "dead"])
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--min-lines", type=int, default=40)
    args = ap.parse_args()

    rows = list(files(args.json))

    if args.mode == "corrected":
        print(f"{'bucket':<14}{'as printed':>22}{'corrected':>24}")
        for name, incl, excl in CORRECTED:
            raw = [r for r in rows if any(p in r[0] for p in incl)]
            cor = [r for r in raw if not any(p in r[0] for p in excl)]
            for label, sel in (("raw", raw), ("cor", cor)):
                c = sum(r[1] for r in sel)
                t = sum(r[2] for r in sel)
                pct = 100.0 * c / t if t else 0.0
                if label == "raw":
                    line = f"{name:<14}{c:>8}/{t:<7}{pct:>5.1f}%"
                else:
                    print(line + f"{c:>10}/{t:<7}{pct:>5.1f}%   (-{len(raw) - len(cor)} files)")
        return 0

    dead = [r for r in rows
            if r[1] == 0 and r[2] >= args.min_lines
            and not any(p in r[0] for p in UNREACHABLE)]
    dead.sort(key=lambda r: -r[2])
    print(f"{'lines':>7}  file        (line_covered == 0, CLI-reachable areas only)")
    for path, _c, t in dead[:args.top]:
        print(f"{t:>7}  {path}")
    print(f"  -- {len(dead)} files, {sum(r[2] for r in dead)} uncovered lines total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
