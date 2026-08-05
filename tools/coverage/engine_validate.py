#!/usr/bin/env python3
"""
engine_validate.py -- check the engine/out classification in BOTH directions.

An unvalidated classifier is exactly the kind of plausible-but-unchecked number this
project has been burned by twice. Two checks, both falsifiable:

  A. POSITIVE. Run a handful of `kicad-cli` verbs the committed suite does not
     exercise, into an ISOLATED gcov prefix, and list the files the rule calls
     `engine` that went from 0 executed lines to some. If the rule says "a CLI run
     can reach this" and a CLI run does, the engine claim is supported.

  B. NEGATIVE. Any line the rule calls `out` that the suite EXECUTED is a
     counter-example: the closure is missing an edge. Reported per file and ranked,
     because the count -- not zero, but how far from zero -- is the honest measure
     of how sound the closure is.

Usage (inside the coverage image, with /scope mounted):
    python3 engine_validate.py positive --raw /scratch/raw --scope /scope/scope \\
        --graph /scope/graph --linemap /scope/lines/linemap.jsonl.gz
    python3 engine_validate.py negative --coverage OUT/engine-coverage.json \\
        --denominator OUT/engine-denominator.tsv.gz
"""

import argparse
import collections
import gzip
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine_lines import _gcov_one, load_scope, bucket_of  # noqa: E402


def cmd_positive(args):
    graft = os.path.join(args.raw.rstrip("/"), args.build_dir.lstrip("/"))
    gcdas = []
    for root, _d, files in os.walk(graft):
        for f in files:
            if f.endswith(".gcda"):
                gcdas.append(os.path.join(root, f))
    print(f"{len(gcdas)} .gcda written by the probe run")
    if not gcdas:
        print("FATAL: the probe run produced no counters at all.")
        return 3

    # Only look at the objects the probe actually touched.
    gcnos = [os.path.join(args.build_dir,
                          os.path.relpath(g, graft))[:-5] + ".gcno" for g in gcdas]
    subprocess.run(["cp", "-a", graft + "/.", args.build_dir + "/"], check=True)

    glob, loc, loc_any = load_scope(args.scope, args.graph)

    # baseline: which (file,line) did the committed suite already execute?
    base = {}
    with gzip.open(args.linemap, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            base[r["f"]] = {n for n, c, _f in r["l"] if c > 0}

    newly = collections.Counter()
    newly_out = collections.Counter()
    for g in gcnos:
        if not os.path.exists(g):
            continue
        _p, res, err = _gcov_one(g)
        if err:
            continue
        for rel, lines in res:
            b = base.get(rel, set())
            for n, (c, fns) in lines.items():
                if c <= 0 or n in b:
                    continue
                w = 0
                hit = False
                for fn in fns:
                    fl = glob.get(fn) or loc.get((rel, fn)) or loc_any.get(fn)
                    if fl:
                        hit = True
                        w = max(w, fl[0])
                if hit and w:
                    newly[rel] += 1
                else:
                    newly_out[rel] += 1

    print("\n=== A. lines NEWLY executed by the probe run, classified ENGINE ===")
    print(f"{'lines':>7}  file")
    for f, c in newly.most_common(args.top):
        print(f"{c:>7}  {f}")
    print(f"total newly-executed ENGINE lines: {sum(newly.values())} "
          f"in {len(newly)} files")
    print("\n=== A'. lines NEWLY executed by the probe run, classified OUT "
          "(each one falsifies the rule) ===")
    for f, c in newly_out.most_common(args.top):
        print(f"{c:>7}  {f}")
    print(f"total newly-executed OUT lines: {sum(newly_out.values())} "
          f"in {len(newly_out)} files")
    return 0


def cmd_negative(args):
    d = json.load(open(args.coverage))
    files = d["files"]
    tot_out_cov = sum(a["out_covered"] for a in files.values())
    tot_cov = sum(a["covered"] for a in files.values())
    print("=== B. EXECUTED but classified OUT-OF-SCOPE ===")
    print(f"{tot_out_cov} lines ({100.0*tot_out_cov/tot_cov:.1f}% of all "
          f"{tot_cov} executed lines) are counter-examples to the closure.\n")
    by_bucket = collections.Counter()
    for f, a in files.items():
        by_bucket[bucket_of(f)] += a["out_covered"]
    print("by subsystem bucket:")
    for b, c in by_bucket.most_common():
        print(f"  {c:>7}  {b}")
    print(f"\ntop files ({args.top}):")
    print(f"{'out_cov':>8}{'out':>8}{'eng':>8}  file")
    for f, a in sorted(files.items(), key=lambda kv: -kv[1]["out_covered"])[:args.top]:
        if a["out_covered"] == 0:
            break
        print(f"{a['out_covered']:>8}{a['out']:>8}{a['engine']:>8}  {f}")

    if args.denominator:
        fn_cnt = collections.Counter()
        with gzip.open(args.denominator, "rt") as fh:
            next(fh)
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 5 and p[2] == "out" and p[3] == "1":
                    fn_cnt[p[4]] += 1
        print(f"\ntop executed-but-out FUNCTIONS ({args.top}):")
        for fn, c in fn_cnt.most_common(args.top):
            print(f"{c:>7}  {fn[:130]}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("positive")
    p.add_argument("--raw", required=True)
    p.add_argument("--build-dir", default="/src/build")
    p.add_argument("--scope", required=True)
    p.add_argument("--graph", required=True)
    p.add_argument("--linemap", required=True)
    p.add_argument("--top", type=int, default=25)
    p.set_defaults(func=cmd_positive)

    n = sub.add_parser("negative")
    n.add_argument("--coverage", required=True)
    n.add_argument("--denominator")
    n.add_argument("--top", type=int, default=25)
    n.set_defaults(func=cmd_negative)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
