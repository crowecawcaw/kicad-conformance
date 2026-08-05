#!/usr/bin/env python3
"""
engine_scope.py -- stage 2 of the engine-coverage denominator.

Takes the symbol reference graph produced by `engine_elf.py` and a declarative
root set (`engine-roots.json`), and answers: which symbols can a `kicad-cli`
invocation reach?

Subcommands
-----------
  grep    REGEX        list defined symbols matching a regex (used to author roots)
  close                compute the closure and write scope.jsonl.gz + closure-stats.json
  why     SYMBOL       print one shortest root -> SYMBOL path (audit a classification)

The closure is a plain BFS. The interesting part is entirely in the root set and
in the fact that vtables are nodes (see engine_elf.py's header), so it is Rapid
Type Analysis: over-approximating, never under-approximating, as long as the
graph itself has every edge.
"""

import argparse
import collections
import gzip
import json
import os
import re
import sys
import time

SEP = "\x01"


def load_defs(graphdir):
    """symbol -> set of source files that define it; and object -> source."""
    sym_src = collections.defaultdict(set)
    obj_src = {}
    local_syms = collections.defaultdict(set)   # src -> {names}
    sym_kind = {}
    n = 0
    with gzip.open(os.path.join(graphdir, "defs.jsonl.gz"), "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            src = rec["src"]
            obj_src[rec["o"]] = src
            for name, bind, typ, sec in rec["syms"]:
                n += 1
                sym_src[name].add(src)
                sym_kind[name] = (bind, typ)
                if bind == 0:
                    local_syms[src].add(name)
    return sym_src, obj_src, local_syms, sym_kind, n


def load_graph(graphdir):
    """Intern node names to ints; return (names, index, adjacency CSR-ish)."""
    idx = {}
    names = []
    src_l = []
    dst_l = []

    def nid(s):
        i = idx.get(s)
        if i is None:
            i = len(names)
            idx[s] = i
            names.append(s)
        return i

    with gzip.open(os.path.join(graphdir, "edges.txt.gz"), "rt") as fh:
        for line in fh:
            u, _, v = line.rstrip("\n").partition("\t")
            src_l.append(nid(u))
            dst_l.append(nid(v))

    adj = collections.defaultdict(list)
    for u, v in zip(src_l, dst_l):
        adj[u].append(v)
    return names, idx, adj


def bare(name):
    """Strip the object-file namespace from a LOCAL node id."""
    return name.split(SEP, 1)[1] if SEP in name else name


def bfs(adj, seeds, barriers=frozenset()):
    """Transitive closure. `barriers` are nodes that are never entered, which is how
    a deliberately-unexercised subsystem is carved out: cut the entry point and see
    what falls out of the closure."""
    seen = set(s for s in seeds if s not in barriers)
    stack = list(seen)
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v not in seen and v not in barriers:
                seen.add(v)
                stack.append(v)
    return seen


def resolve_roots(spec, names, idx):
    """spec entry -> list of node indices.

    Root symbols are matched against the *bare* symbol name, so a root that is a
    file-local (`static`) function is picked up in whichever object defines it.
    """
    out = []
    exacts = set(spec.get("exact", []))
    rx = re.compile(spec["regex"]) if spec.get("regex") else None
    for i, n in enumerate(names):
        b = bare(n)
        if b in exacts or (rx and rx.search(b)):
            out.append(i)
    return out


def cmd_grep(args):
    sym_src, _obj_src, _loc, _kind, _n = load_defs(args.graphdir)
    rx = re.compile(args.regex)
    hits = [(s, sorted(v)) for s, v in sym_src.items() if rx.search(s)]
    hits.sort()
    for s, v in hits[: args.limit]:
        print(f"{s}\t{','.join(x for x in v if x)[:120]}")
    print(f"-- {len(hits)} matching symbols (showing {min(len(hits), args.limit)})",
          file=sys.stderr)


def cmd_why(args):
    names, idx, adj = load_graph(args.graphdir)
    spec = json.load(open(args.roots))
    kinds = set(args.kinds.split(","))
    seeds = []
    for gid, g in spec["groups"].items():
        if g.get("kind") not in kinds:
            continue
        for i in resolve_roots(g, names, idx):
            seeds.append(i)

    prev = {}
    seen = set()
    q = collections.deque()
    for i in seeds:
        if i not in seen:
            seen.add(i)
            prev[i] = None
            q.append(i)
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                prev[v] = u
                q.append(v)

    rc = 0
    for sym in args.symbol:
        targets = [i for i, n in enumerate(names)
                   if bare(n) == sym and i in seen]
        if not targets:
            targets = [i for i, n in enumerate(names) if sym in bare(n) and i in seen]
        if not targets:
            print(f"\n### {sym}: NOT REACHABLE from kinds={args.kinds}")
            rc = 1
            continue
        u = targets[0]
        path = []
        while u is not None:
            path.append(bare(names[u]))
            u = prev[u]
        print(f"\n### {sym}  ({len(path)-1} hops from a {args.kinds} root)")
        for step in reversed(path):
            print("   " + step[:150])
    return rc


def cmd_close(args):
    t0 = time.time()
    spec = json.load(open(args.roots))
    print("loading graph...", file=sys.stderr, flush=True)
    names, idx, adj = load_graph(args.graphdir)
    print(f"  {len(names)} nodes, {sum(len(v) for v in adj.values())} edges, "
          f"{time.time()-t0:.0f}s", file=sys.stderr, flush=True)

    # Cross-check the roots against the DEFINED symbols, not just the graph nodes.
    # A node only exists if some relocation touches it, so a root that is defined but
    # isolated would silently contribute an empty closure -- an under-approximated
    # denominator with no error message. This is the same class of failure as the
    # missing `_cvpcb.kiface` (docs/COVERAGE.md 2c): a report that looks complete.
    sym_src, _obj_src, _loc, _kind, _n = load_defs(args.graphdir)
    all_defined = list(sym_src)
    graph_bare = {bare(n) for n in names}

    groups = spec["groups"]
    per_group = {}
    seeds_by_kind = collections.defaultdict(list)
    root_report = {}
    missing_roots = {}

    # `excluded` groups are BARRIERS in every closure, not merely non-roots. Declaring
    # them and then not enforcing them is not enough: the three `IFACE` objects are
    # file-scope statics, so their constructors are reachable from the static-init
    # roots, and a constructor references its class's vtable -- which put
    # IFACE::CreateKiWindow back in scope through RTA and dragged the entire editor
    # frame / tool / dialog tree with it. Measured: enforcing the barrier is the
    # difference between 199550 and 12639 init-only lines.
    hard_barriers = set()
    for gid, g in groups.items():
        if g.get("kind") == "excluded":
            hard_barriers.update(resolve_roots(g, names, idx))

    for gid, g in groups.items():
        seeds = resolve_roots(g, names, idx)
        declared = {all_defined[i] for i in resolve_roots(g, all_defined, {})}
        absent = sorted(declared - graph_bare)
        if absent and g.get("kind") != "excluded":
            missing_roots[gid] = absent
        root_report[gid] = {"kind": g.get("kind"), "n_root_symbols": len(seeds),
                            "n_declared_in_defs": len(declared),
                            "absent_from_graph": absent[:20],
                            "why": g.get("why", "")}
        if g.get("kind") == "excluded":
            continue
        seeds_by_kind[g["kind"]].extend(seeds)
        if g.get("per_symbol"):
            # One closure per matched root symbol, so a gap can be attributed to
            # the specific CLI subcommand that would reach it.
            for s in seeds:
                per_group[bare(names[s])] = [s]
        else:
            per_group[gid] = seeds

    work_seeds = seeds_by_kind.get("work", [])
    init_seeds = seeds_by_kind.get("init", [])
    if not work_seeds:
        print("FATAL: no work roots matched -- the root spec does not fit this "
              "build's symbols. Refusing to emit a denominator.", file=sys.stderr)
        return 3

    print(f"work roots: {len(work_seeds)} symbols; init roots: {len(init_seeds)}",
          file=sys.stderr, flush=True)

    # THE ENGINE CLOSURE IS SEEDED FROM BOTH ROOT KINDS. Static initialisers are not
    # a separate, lesser category: KiCad registers its DRC test providers, its IO
    # plugins and its property descriptors from file-scope statics, so the ONLY
    # static path to DRC_TEST_PROVIDER_COPPER_CLEARANCE::Run runs through
    # _GLOBAL__sub_I_. Splitting them put demonstrably-executed rule-engine code in
    # the out-of-scope bucket. The "static constructors inflate the number" problem
    # is real but is not answered by a second closure -- it is answered by MEASURING
    # the floor (`engine-scope.sh floor`), which records what a no-op `kicad-cli`
    # invocation executes all by itself.
    work_only = bfs(adj, work_seeds, hard_barriers)
    init = bfs(adj, init_seeds, hard_barriers) if init_seeds else set()
    work = work_only | init
    print(f"  engine closure: {len(work)} nodes "
          f"(work-root-only {len(work_only)}, static-init-only "
          f"{len(init - work_only)}) ({time.time()-t0:.0f}s)",
          file=sys.stderr, flush=True)

    # Per-root-group closures, for attribution (which subcommand reaches what) and
    # for separating the deferred scope (3D/STEP, DL-0012).
    print(f"per-group closures for {len(per_group)} groups...", file=sys.stderr,
          flush=True)
    group_sets = {}
    for k, (gid, seeds) in enumerate(sorted(per_group.items())):
        group_sets[gid] = bfs(adj, seeds)
        if (k + 1) % 10 == 0:
            print(f"  ... {k+1}/{len(per_group)} ({time.time()-t0:.0f}s)",
                  file=sys.stderr, flush=True)

    # Deferred subsystems (3D/STEP per DL-0012, third-party import per ROADMAP.md).
    # These CANNOT be separated by per-verb closures, because IFACE::HandleJob
    # dispatches on job type and therefore reaches *every* job handler, so every
    # exporter is reachable from the kiface root regardless of which CLI verb was
    # asked for. The separable question is instead a CUT: remove the deferred entry
    # points from the graph and re-run the closure; whatever drops out was reachable
    # only through them.
    barrier_spec = spec.get("deferred_cut", {})
    barriers = set(hard_barriers)
    for pat in barrier_spec:
        rx = re.compile(pat)
        barriers.update(i for i, n in enumerate(names) if rx.search(bare(n)))
    work_cut = bfs(adj, work_seeds + init_seeds, barriers)
    deferred_only = work - work_cut

    # Mechanised negative check: symbols the rule claims are unreachable. Reported
    # against the WORK closure (which is what becomes the engine denominator) and
    # against the init closure separately, because init-only code is reported as its
    # own class and is not part of the engine figure.
    violations = []
    assert_unreachable = spec.get("assert_unreachable", {})
    bare_work = {bare(names[i]) for i in work_only}
    bare_init = {bare(names[i]) for i in init} - bare_work
    for pat, why in assert_unreachable.items():
        rx = re.compile(pat)
        hits = sorted(n for n in bare_work if rx.search(n))
        ihits = sorted(n for n in bare_init if rx.search(n))
        if hits or ihits:
            violations.append({"pattern": pat, "why": why,
                               "n_hits_work": len(hits), "n_hits_init_only": len(ihits),
                               "examples": hits[:8] or ihits[:8]})

    os.makedirs(args.outdir, exist_ok=True)
    scope_path = os.path.join(args.outdir, "scope.jsonl.gz")
    with gzip.open(scope_path, "wt", compresslevel=1) as out:
        for i in sorted(work):
            n = names[i]
            rec = {
                "sym": bare(n),
                "obj": n.split(SEP, 1)[0] if SEP in n else None,
                "w": 1,                                   # in the engine closure
                "i": 1 if i not in work_only else 0,       # only via static init
                "d": 1 if i in deferred_only else 0,       # only via a deferred verb
            }
            out.write(json.dumps(rec, separators=(",", ":")) + "\n")

    groups_path = os.path.join(args.outdir, "group-membership.jsonl.gz")
    with gzip.open(groups_path, "wt", compresslevel=1) as out:
        for gid, s in sorted(group_sets.items()):
            out.write(json.dumps(
                {"group": gid, "n": len(s),
                 "syms": sorted({bare(names[i]) for i in s})},
                separators=(",", ":")) + "\n")

    stats = {
        "nodes_total": len(names),
        "hard_barrier_symbols": len(hard_barriers),
        "engine_closure": len(work),
        "work_root_only_closure": len(work_only),
        "init_closure": len(init),
        "reachable_only_via_static_init": len(init - work_only),
        "deferred_barrier_symbols": len(barriers),
        "deferred_only": len(deferred_only),
        "union": len(work | init),
        "roots": root_report,
        "roots_declared_but_isolated": {k: len(v) for k, v in missing_roots.items()},
        "assert_unreachable_violations": violations,
        "seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(args.outdir, "closure-stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    print(json.dumps({k: v for k, v in stats.items()
                      if k not in ("roots",)}, indent=2)[:4000], file=sys.stderr)
    if missing_roots:
        print("\n*** ROOTS DECLARED IN defs BUT ISOLATED IN THE GRAPH ***",
              file=sys.stderr)
        for gid, v in missing_roots.items():
            print(f"  {gid}: {len(v)}, e.g. {v[:5]}", file=sys.stderr)
    if violations:
        print("\n*** assert_unreachable VIOLATIONS -- the GUI cut leaks. ***",
              file=sys.stderr)
        for v in violations:
            print(f"  {v['pattern']}: work={v['n_hits_work']} "
                  f"init-only={v['n_hits_init_only']}, e.g. {v['examples'][:3]}",
                  file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graphdir", default="/scope/graph")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grep")
    g.add_argument("regex")
    g.add_argument("--limit", type=int, default=100)
    g.set_defaults(func=cmd_grep)

    c = sub.add_parser("close")
    c.add_argument("--roots", required=True)
    c.add_argument("--outdir", required=True)
    c.set_defaults(func=cmd_close)

    w = sub.add_parser("why")
    w.add_argument("symbol", nargs="+")
    w.add_argument("--roots", required=True)
    w.add_argument("--kinds", default="work",
                   help="comma-separated root kinds to search from (work,init)")
    w.set_defaults(func=cmd_why)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
