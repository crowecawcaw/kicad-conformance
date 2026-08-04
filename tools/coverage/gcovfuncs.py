#!/usr/bin/env python3
"""Parse `gcov -f -m` output on stdin into a sorted per-function coverage table.

Used by `funcs.sh`, which runs it inside the coverage image. Kept as a file rather
than an inline heredoc because the quoting of an awk/python one-liner nested inside
`docker run bash -c` is exactly the kind of thing that silently produces an empty
table and looks like "no coverage".

    gcov -f -m -o DIR FILE.gcda | python3 gcovfuncs.py [--zero-only] [--min-lines N]
"""
from __future__ import annotations

import argparse
import re
import sys

FUNC = re.compile(r"^Function '(.*)'$")
LINES = re.compile(r"^Lines executed:([0-9.]+)% of (\d+)$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zero-only", action="store_true",
                    help="print only functions that were never entered")
    ap.add_argument("--min-lines", type=int, default=0)
    args = ap.parse_args()

    rows, name = [], None
    for line in sys.stdin:
        line = line.rstrip("\n")
        m = FUNC.match(line)
        if m:
            name = m.group(1)
            continue
        m = LINES.match(line)
        if m and name is not None:
            rows.append((float(m.group(1)), int(m.group(2)), name))
            name = None

    rows = [r for r in rows if r[1] >= args.min_lines]
    if args.zero_only:
        rows = [r for r in rows if r[0] == 0.0]
    rows.sort(key=lambda r: (r[0], -r[1]))

    dead = sum(1 for r in rows if r[0] == 0.0)
    for pct, n, name in rows:
        print(f"{pct:7.2f}% of {n:5d}  {name}")
    print(f"  -- {len(rows)} functions listed, {dead} never entered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
