#!/usr/bin/env python3
"""
engine_lines.py -- stages 3 and 4 of the engine-coverage denominator.

`scan`   runs gcov over every .gcno in the instrumented build and caches, for each
         (source file, line), the mangled name of the function that owns it and the
         execution count. gcov's JSON intermediate format carries `function_name` on
         every line record, which is the exact join key the ELF reference graph
         produces -- no name demangling, no heuristics.

`report` joins that cache against the reachability closure from engine_scope.py and
         emits the denominator plus engine coverage.

Filters mirror `collect.sh`'s gcovr invocation exactly (same --filter and --exclude
set), so the totals here are comparable line-for-line with docs/COVERAGE.md.

    python3 engine_lines.py scan   --build-dir /src/build --raw /coverage/raw \\
                                   --out /scope/lines/linemap.jsonl.gz
    python3 engine_lines.py report --linemap /scope/lines/linemap.jsonl.gz \\
                                   --scope /scope/scope --graph /scope/graph \\
                                   --outdir /work/tools/coverage/out/engine
"""

import argparse
import collections
import gzip
import json
import os
import re
import subprocess
import sys
import time
from multiprocessing import Pool

SRC_ROOT = "/src/kicad"

# Mirrors collect.sh's gcovr --filter / --exclude set.
EXCLUDE_RX = re.compile(
    r"(/thirdparty/)|(/qa/)|(/build/)|(/CMakeFiles/)|(_wrap\.cxx$)|(\.pb\.(cc|h)$)")


def keep_file(path):
    if not path.startswith(SRC_ROOT + "/"):
        return False
    return not EXCLUDE_RX.search(path)


def _gcov_one(gcno):
    try:
        p = subprocess.run(["gcov", "--json-format", "--stdout", gcno],
                           capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return gcno, None, "timeout"
    if p.returncode != 0 or not p.stdout:
        return gcno, None, f"rc={p.returncode}"
    try:
        d = json.loads(p.stdout)
    except Exception as exc:  # noqa: BLE001
        return gcno, None, f"json: {exc!r}"
    out = []
    for f in d.get("files", []):
        path = f["file"]
        if not keep_file(path):
            continue
        rel = path[len(SRC_ROOT) + 1:]
        lines = {}
        for ln in f.get("lines", []):
            n = ln["line_number"]
            c = ln.get("count", 0)
            fn = ln.get("function_name") or ""
            prev = lines.get(n)
            if prev is None:
                lines[n] = [c, [fn] if fn else []]
            else:
                prev[0] = max(prev[0], c)
                if fn and fn not in prev[1]:
                    prev[1].append(fn)
        if lines:
            out.append((rel, lines))
    return gcno, out, None


def cmd_scan(args):
    t0 = time.time()
    build = args.build_dir
    if args.raw:
        graft = os.path.join(args.raw.rstrip("/"), build.lstrip("/"))
        if os.path.isdir(graft):
            n = subprocess.run(["bash", "-c",
                                f"find {graft} -name '*.gcda' | wc -l"],
                               capture_output=True, text=True).stdout.strip()
            print(f"grafting {n} .gcda from {graft}", file=sys.stderr, flush=True)
            subprocess.run(["cp", "-a", graft + "/.", build + "/"], check=True)
        else:
            print(f"WARNING: no raw profile tree at {graft}; every count will be 0. "
                  f"This yields the denominator but NOT a coverage figure.",
                  file=sys.stderr)

    subprocess.run(["bash", "-c",
                    f"rm -rf {build}/CMakeFiles/*/CompilerId* "
                    f"{build}/CMakeFiles/CMakeScratch"], check=False)

    gcnos = []
    for root, _d, files in os.walk(build):
        for f in files:
            if f.endswith(".gcno"):
                gcnos.append(os.path.join(root, f))
    gcnos.sort()
    print(f"{len(gcnos)} .gcno files", file=sys.stderr, flush=True)

    # file -> line -> [count, [fnames]]
    merged = collections.defaultdict(dict)
    errors = []
    done = 0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with Pool(args.jobs) as pool:
        for gcno, res, err in pool.imap_unordered(_gcov_one, gcnos, chunksize=4):
            done += 1
            if err:
                errors.append((gcno, err))
                continue
            for rel, lines in res:
                tgt = merged[rel]
                for n, (c, fns) in lines.items():
                    prev = tgt.get(n)
                    if prev is None:
                        tgt[n] = [c, list(fns)]
                    else:
                        prev[0] = max(prev[0], c)
                        for fn in fns:
                            if fn not in prev[1]:
                                prev[1].append(fn)
            if done % 200 == 0:
                print(f"  ... {done}/{len(gcnos)} ({time.time()-t0:.0f}s), "
                      f"{len(merged)} source files", file=sys.stderr, flush=True)

    with gzip.open(args.out, "wt", compresslevel=1) as out:
        for rel in sorted(merged):
            lines = merged[rel]
            out.write(json.dumps(
                {"f": rel,
                 "l": [[n, v[0], v[1]] for n, v in sorted(lines.items())]},
                separators=(",", ":")) + "\n")

    stats = {"gcno": len(gcnos), "files": len(merged),
             "lines": sum(len(v) for v in merged.values()),
             "errors": len(errors), "seconds": round(time.time() - t0, 1)}
    with open(args.out + ".stats.json", "w") as fh:
        json.dump({**stats, "error_sample": errors[:20]}, fh, indent=2)
    print(json.dumps(stats), file=sys.stderr)
    # gcov leaves a .gcov.json for every object it cannot resolve; a handful of
    # errors is normal (generated sources), a flood means the scan is not measuring.
    if errors and len(errors) > 0.02 * len(gcnos):
        print(f"FATAL: {len(errors)}/{len(gcnos)} gcov invocations failed",
              file=sys.stderr)
        return 3
    return 0


# ---------------------------------------------------------------- report ----

def load_scope(scopedir, graphdir):
    obj_src = {}
    with gzip.open(os.path.join(graphdir, "defs.jsonl.gz"), "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            obj_src[rec["o"]] = rec["src"]

    glob = {}
    loc = {}
    loc_any = {}
    with gzip.open(os.path.join(scopedir, "scope.jsonl.gz"), "rt") as fh:
        for line in fh:
            r = json.loads(line)
            flags = (r["w"], r["i"], r["d"])
            if r["obj"] is None:
                p = glob.get(r["sym"])
                glob[r["sym"]] = flags if p is None else (
                    max(p[0], flags[0]), max(p[1], flags[1]), min(p[2], flags[2]))
            else:
                src = obj_src.get(r["obj"])
                if src:
                    k = (src, r["sym"])
                    p = loc.get(k)
                    loc[k] = flags if p is None else (
                        max(p[0], flags[0]), max(p[1], flags[1]), min(p[2], flags[2]))
                p = loc_any.get(r["sym"])
                loc_any[r["sym"]] = flags if p is None else (
                    max(p[0], flags[0]), max(p[1], flags[1]), min(p[2], flags[2]))
    return glob, loc, loc_any


# Same ordered bucket list as collect.sh's focus.json rollup, so an engine figure
# can be read next to docs/COVERAGE.md 3 without re-deriving anything. FIRST match
# wins, so `gui` must stay last.
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


def bucket_of(path):
    for name, pats in BUCKETS:
        if any(p in path for p in pats):
            return name
    return "other"


def cmd_report(args):
    t0 = time.time()
    glob, loc, loc_any = load_scope(args.scope, args.graph)
    # symbol -> the CLI entry-point group that reaches it (first wins; a symbol
    # reachable from several verbs is attributed to the alphabetically first, which
    # is only used for the "which verb would exercise this" hint column).
    sym_group = {}
    gm = os.path.join(args.scope, "group-membership.jsonl.gz")
    if os.path.exists(gm) and args.per_command:
        with gzip.open(gm, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                for s in r["syms"]:
                    sym_group.setdefault(s, r["group"])

    os.makedirs(args.outdir, exist_ok=True)

    per_file = {}
    fallback_hits = 0
    unknown_fn = 0
    denom = gzip.open(os.path.join(args.outdir, "engine-denominator.tsv.gz"),
                      "wt", compresslevel=6)
    denom.write("# file\tline\tclass\tcovered\tfunction\tentry_point\n")
    group_tot = collections.defaultdict(lambda: [0, 0])
    buckets = collections.defaultdict(lambda: collections.Counter())
    file_entry = collections.defaultdict(collections.Counter)
    # (file, mangled name) -> [n_lines, entered?]. FUNCTION-ENTRY coverage is the
    # honest target for "complete engine coverage": 100% of engine *lines* is not
    # reachable (see docs/ENGINE_COVERAGE.md), but "every in-scope function is
    # entered at least once" is a target that can actually be closed.
    fnstate = {}

    CLASSES = ("engine", "deferred", "out")

    # The measured FREE FLOOR: lines a no-op `kicad-cli` invocation executes on its
    # own, before the suite asks for anything. This is the honest answer to the
    # static-constructor inflation that makes cli/jobs read 43.9% -- it is measured,
    # not modelled.
    floor = {}
    if args.floor and os.path.exists(args.floor):
        with gzip.open(args.floor, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                s = {n for n, c, _x in r["l"] if c > 0}
                if s:
                    floor[r["f"]] = s

    with gzip.open(args.linemap, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            f = rec["f"]
            agg = {"lines": 0, "covered": 0,
                   "engine_via_static_init": 0,
                   "engine_via_static_init_covered": 0}
            for c in CLASSES:
                agg[c] = 0
                agg[c + "_covered"] = 0
                agg[c + "_floor"] = 0
            for n, count, fns in rec["l"]:
                agg["lines"] += 1
                cov = 1 if count > 0 else 0
                agg["covered"] += cov
                w = i = 0
                d = 1
                hit = False
                for fn in fns:
                    fl = glob.get(fn)
                    if fl is None:
                        fl = loc.get((f, fn))
                        if fl is None:
                            fl = loc_any.get(fn)
                            if fl is not None:
                                fallback_hits += 1
                    if fl is None:
                        continue
                    hit = True
                    w = max(w, fl[0])
                    i = max(i, fl[1])
                    d = min(d, fl[2])
                if not fns:
                    unknown_fn += 1
                if not hit:
                    cls = "out"
                elif d:
                    cls = "deferred"
                else:
                    cls = "engine"
                agg[cls] += 1
                agg[cls + "_covered"] += cov
                if cls == "engine" and i:
                    # informational: in scope, but the only static path to it runs
                    # through a file-scope static's constructor
                    agg["engine_via_static_init"] += 1
                    agg["engine_via_static_init_covered"] += cov
                if cls != "out" and floor and n in floor.get(f, ()):
                    agg[cls + "_floor"] += 1
                ep = ""
                if sym_group and cls in ("engine", "deferred") and fns:
                    ep = sym_group.get(fns[0], "")
                    if ep:
                        group_tot[ep][0] += 1
                        group_tot[ep][1] += cov
                        if not cov:
                            file_entry[f][ep] += 1
                denom.write(f"{f}\t{n}\t{cls}\t{cov}\t{fns[0] if fns else ''}\t{ep}\n")
                if cls == "engine" and fns:
                    k = (f, fns[0])
                    e = fnstate.get(k)
                    if e is None:
                        fnstate[k] = [1, cov]
                    else:
                        e[0] += 1
                        e[1] = max(e[1], cov)
            per_file[f] = agg
            b = buckets[bucket_of(f)]
            for k, v in agg.items():
                b[k] += v
            b["files"] += 1
    denom.close()

    tot = collections.Counter()
    for agg in per_file.values():
        for k, v in agg.items():
            tot[k] += v

    def pct(c, l):
        return round(100.0 * c / l, 2) if l else 0.0

    gaps = sorted(
        ((f, a["engine"] - a["engine_covered"], a["engine"], a["engine_covered"])
         for f, a in per_file.items() if a["engine"] - a["engine_covered"] > 0),
        key=lambda t: -t[1])

    fn_total = len(fnstate)
    fn_entered = sum(1 for v in fnstate.values() if v[1])
    files_engine = [f for f, a in per_file.items() if a["engine"] > 0]
    files_engine_zero = [f for f in files_engine if per_file[f]["engine_covered"] == 0]

    out = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totals": dict(tot),
        "engine_percent": pct(tot["engine_covered"], tot["engine"]),
        "engine_plus_deferred_percent": pct(
            tot["engine_covered"] + tot["deferred_covered"],
            tot["engine"] + tot["deferred"]),
        "global_percent": pct(tot["covered"], tot["lines"]),
        "engine_floor_lines": tot["engine_floor"],
        "engine_earned_percent": pct(tot["engine_covered"] - tot["engine_floor"],
                                     tot["engine"] - tot["engine_floor"]),
        "engine_via_static_init": tot["engine_via_static_init"],
        "engine_via_static_init_covered": tot["engine_via_static_init_covered"],
        "engine_functions": fn_total,
        "engine_functions_entered": fn_entered,
        "engine_function_entry_percent": pct(fn_entered, fn_total),
        "engine_files": len(files_engine),
        "engine_files_at_zero": len(files_engine_zero),
        "join_fallback_by_name_only": fallback_hits,
        "lines_with_no_function_attribution": unknown_fn,
        "buckets": {b: {**dict(c), "engine_percent": pct(c["engine_covered"], c["engine"])}
                    for b, c in sorted(buckets.items())},
        "top_gaps": [{"file": f, "uncovered_engine_lines": u, "engine": e,
                      "engine_covered": c, "engine_percent": pct(c, e),
                      "entry_points": [k for k, _ in file_entry[f].most_common(3)]}
                     for f, u, e, c in gaps[:120]],
        "files": per_file,
    }
    if group_tot:
        out["per_command"] = {
            k: {"lines": v[0], "covered": v[1], "percent": pct(v[1], v[0])}
            for k, v in sorted(group_tot.items(), key=lambda kv: -kv[1][0])}
    with open(os.path.join(args.outdir, "engine-coverage.json"), "w") as fh:
        json.dump(out, fh, indent=2)

    L = []
    L.append(f"=== ENGINE COVERAGE ({out['generated_utc']}) ===")
    L.append(f"{'class':<12}{'lines':>10}{'covered':>10}{'pct':>9}")
    for cls in CLASSES:
        L.append(f"{cls:<12}{tot[cls]:>10}{tot[cls+'_covered']:>10}"
                 f"{pct(tot[cls+'_covered'], tot[cls]):>8.1f}%")
    L.append(f"{'ALL':<12}{tot['lines']:>10}{tot['covered']:>10}"
             f"{pct(tot['covered'], tot['lines']):>8.1f}%")
    L.append("")
    L.append(f"ENGINE COVERAGE = {tot['engine_covered']}/{tot['engine']} = "
             f"{out['engine_percent']}%")
    L.append(f"engine+deferred = {tot['engine_covered']+tot['deferred_covered']}/"
             f"{tot['engine']+tot['deferred']} = {out['engine_plus_deferred_percent']}%")
    L.append(f"global (cf. docs/COVERAGE.md) = {tot['covered']}/{tot['lines']} = "
             f"{out['global_percent']}%")
    if floor:
        L.append("")
        L.append(f"free floor (measured: what a no-op `kicad-cli` invocation runs "
                 f"by itself) = {tot['engine_floor']} engine lines")
        L.append(f"EARNED engine coverage, floor removed from both sides = "
                 f"{tot['engine_covered']-tot['engine_floor']}/"
                 f"{tot['engine']-tot['engine_floor']} = "
                 f"{out['engine_earned_percent']}%")
    L.append("")
    L.append(f"ENGINE FUNCTION-ENTRY coverage = {fn_entered}/{fn_total} = "
             f"{pct(fn_entered, fn_total)}%   "
             f"(engine files at 0%: {len(files_engine_zero)}/{len(files_engine)})")
    L.append(f"of the engine denominator, {tot['engine_via_static_init']} lines are "
             f"reachable ONLY via a file-scope static's constructor "
             f"({tot['engine_via_static_init_covered']} covered)")
    L.append("")
    L.append("=== engine denominator by subsystem bucket (collect.sh's patterns) ===")
    L.append(f"{'bucket':<14}{'files':>7}{'all_lines':>11}{'engine':>9}"
             f"{'eng_cov':>9}{'eng_pct':>9}{'out':>9}")
    for b, c in sorted(buckets.items(), key=lambda kv: -kv[1]["engine"]):
        L.append(f"{b:<14}{c['files']:>7}{c['lines']:>11}{c['engine']:>9}"
                 f"{c['engine_covered']:>9}"
                 f"{pct(c['engine_covered'], c['engine']):>8.1f}%{c['out']:>9}")
    L.append("")
    L.append("=== largest in-scope gaps (uncovered ENGINE lines) ===")
    L.append(f"{'uncov':>7}{'engine':>8}{'pct':>7}  file / reachable from")
    for f, u, e, c in gaps[:40]:
        eps = ",".join(k.replace("_ZN3CLI", "").replace("9doPerformER5KIWAY", "")
                       for k, _ in file_entry[f].most_common(2))
        L.append(f"{u:>7}{e:>8}{pct(c, e):>6.1f}%  {f}\n{'':>23}<- {eps[:110]}")
    L.append("")
    L.append(f"join fallbacks (name-only): {fallback_hits}; "
             f"lines with no function attribution: {unknown_fn}")
    text = "\n".join(L)
    with open(os.path.join(args.outdir, "engine-report.txt"), "w") as fh:
        fh.write(text + "\n")
    print(text)
    print(f"seconds: {time.time()-t0:.0f}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--build-dir", default="/src/build")
    s.add_argument("--raw", default="/coverage/raw")
    s.add_argument("--out", required=True)
    s.add_argument("--jobs", type=int, default=os.cpu_count())
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("report")
    r.add_argument("--linemap", required=True)
    r.add_argument("--scope", required=True)
    r.add_argument("--graph", required=True)
    r.add_argument("--outdir", required=True)
    r.add_argument("--per-command", action="store_true")
    r.add_argument("--floor", help="linemap of a no-op kicad-cli invocation")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
