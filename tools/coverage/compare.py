#!/usr/bin/env python3
"""Compare two gcovr --json-summary files (round N-1 vs round N) per file and per
subsystem bucket, so a coverage round can state a BEFORE and an AFTER for every
number it quotes rather than overwriting the old one.

    python3 tools/coverage/compare.py OLD_coverage.json NEW_coverage.json [--top N]
                                      [--file SUBSTR ...] [--bucket]

Buckets are the same ordered prefix list `collect.sh` uses; keep the two in sync.
Nothing here re-derives coverage -- it only reads what gcovr already printed.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys

# MUST match collect.sh's BUCKETS (first match wins; `gui` stays last).
BUCKETS = [
    ("io/board",     ("pcbnew/pcb_io",)),
    ("io/schematic", ("eeschema/sch_io",)),
    ("io/common",    ("common/io", "libs/sexpr", "common/drawing_sheet")),
    ("drc",          ("pcbnew/drc",)),
    ("erc",          ("eeschema/erc",)),
    ("netlist",      ("eeschema/netlist_exporters", "common/netlist_reader",
                      "pcbnew/netlist_reader")),
    ("connectivity", ("pcbnew/connectivity", "pcbnew/ratsnest")),
    ("export/plot",  ("pcbnew/exporters", "common/plotters", "eeschema/printing")),
    ("cli/jobs",     ("kicad/cli", "common/jobs", "jobs_handler")),
    ("geometry",     ("libs/kimath", "libs/core")),
    ("gui",          ("/dialogs/", "/widgets/", "/tools/", "common/gal",
                      "preview_items", "3d-viewer")),
]


def bucket_of(path: str) -> str:
    p = path.replace("\\", "/")
    for name, pats in BUCKETS:
        if any(pat in p for pat in pats):
            return name
    return "other"


def load(path: str) -> tuple[dict, dict]:
    data = json.load(open(path))
    files = {
        f["filename"].replace("\\", "/"): (f.get("line_covered", 0), f.get("line_total", 0))
        for f in data.get("files", [])
    }
    overall = {
        "line_percent": data.get("line_percent"),
        "line_total": data.get("line_total"),
        "line_covered": data.get("line_covered"),
        "branch_percent": data.get("branch_percent"),
    }
    return files, overall


def pct(cov: int, tot: int) -> float:
    return (100.0 * cov / tot) if tot else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--top", type=int, default=40,
                    help="show the N files with the largest covered-line delta")
    ap.add_argument("--file", action="append", default=[],
                    help="report this path substring explicitly, however small its delta")
    args = ap.parse_args()

    old_f, old_o = load(args.old)
    new_f, new_o = load(args.new)

    print("=== GLOBAL ===")
    print(f"  before: {old_o['line_covered']}/{old_o['line_total']} = {old_o['line_percent']}%"
          f"   branch {old_o['branch_percent']}%")
    print(f"  after : {new_o['line_covered']}/{new_o['line_total']} = {new_o['line_percent']}%"
          f"   branch {new_o['branch_percent']}%")

    ob = collections.defaultdict(lambda: [0, 0, 0])   # covered, total, files
    nb = collections.defaultdict(lambda: [0, 0, 0])
    for src, dst in ((old_f, ob), (new_f, nb)):
        for path, (cov, tot) in src.items():
            b = dst[bucket_of(path)]
            b[0] += cov
            b[1] += tot
            b[2] += 1

    print("\n=== BY SUBSYSTEM (line %) ===")
    print(f"{'bucket':<15}{'lines':>8}{'cov(before)':>12}{'%':>7}"
          f"{'cov(after)':>12}{'%':>7}{'delta lines':>13}{'delta pp':>10}")
    for name in sorted(set(ob) | set(nb), key=lambda n: -(nb[n][0] - ob[n][0])):
        oc, ot, _ = ob[name]
        nc, nt, _ = nb[name]
        print(f"{name:<15}{nt:>8}{oc:>12}{pct(oc, ot):>6.1f}%"
              f"{nc:>12}{pct(nc, nt):>6.1f}%{nc - oc:>+13}{pct(nc, nt) - pct(oc, ot):>+9.1f}")

    deltas = []
    for path in sorted(set(old_f) | set(new_f)):
        oc, ot = old_f.get(path, (0, 0))
        nc, nt = new_f.get(path, (0, 0))
        deltas.append((nc - oc, path, oc, nc, nt or ot))

    print(f"\n=== TOP {args.top} FILES BY NEWLY-COVERED LINES ===")
    print(f"{'delta':>7}{'before':>9}{'after':>9}{'total':>8}  file")
    for d, path, oc, nc, tot in sorted(deltas, reverse=True)[:args.top]:
        if d <= 0:
            break
        print(f"{d:>+7}{oc:>9}{nc:>9}{tot:>8}  {path}")

    regressions = [x for x in deltas if x[0] < 0]
    print(f"\n=== FILES THAT LOST COVERAGE ({len(regressions)}) ===")
    for d, path, oc, nc, tot in sorted(regressions)[:20]:
        print(f"{d:>+7}{oc:>9}{nc:>9}{tot:>8}  {path}")

    if args.file:
        print("\n=== NAMED FILES (predictions) ===")
        print(f"{'before':>9}{'after':>9}{'total':>8}  file")
        for want in args.file:
            hits = [x for x in deltas if want in x[1]]
            if not hits:
                print(f"{'--':>9}{'--':>9}{'--':>8}  {want}   (NO SUCH FILE in either report)")
            for d, path, oc, nc, tot in sorted(hits, key=lambda x: x[1]):
                print(f"{oc:>9}{nc:>9}{tot:>8}  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
