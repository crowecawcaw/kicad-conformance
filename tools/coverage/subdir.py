#!/usr/bin/env python3
"""Sum line coverage over an arbitrary path substring, for one or more gcovr
--json-summary files, so a before/after can be quoted for a directory that is not one
of collect.sh's buckets (e.g. `pcbnew/pcb_io/kicad_sexpr/` -- KiCad's OWN board
parser, as opposed to the `io/board` bucket that also sweeps in every third-party
importer).

    python3 tools/coverage/subdir.py SUBSTR[,SUBSTR...] coverage.json [coverage.json...]
"""
from __future__ import annotations

import json
import sys


def agg(path: str, sub: str) -> tuple[int, int, int]:
    data = json.load(open(path))
    cov = tot = n = 0
    for f in data.get("files", []):
        if sub in f["filename"].replace("\\", "/"):
            cov += f.get("line_covered", 0)
            tot += f.get("line_total", 0)
            n += 1
    return cov, tot, n


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    subs = sys.argv[1].split(",")
    reports = sys.argv[2:]
    for sub in subs:
        print(f"--- {sub}")
        for r in reports:
            cov, tot, n = agg(r, sub)
            pct = 100.0 * cov / tot if tot else 0.0
            print(f"    {r:<52} {cov:>6}/{tot:<6} {pct:>5.1f}%  ({n} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
