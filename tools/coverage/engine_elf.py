#!/usr/bin/env python3
"""
engine_elf.py -- stage 1 of the engine-scope denominator (see engine-scope.sh).
Builds a symbol-level reference graph from the instrumented build's relocatable
objects: node = mangled symbol (LOCAL symbols namespaced by object file), edge
u -> v = a relocation inside u's byte range points at v. Reads ELF64 .o files
directly rather than shelling out to readelf/objdump (~20x faster on a 3 GB tree).

Vtables (`_ZTV*`, `_ZTC*`, ...) are ordinary nodes, so "constructor runs -> vtable
referenced -> methods reachable" falls out of plain transitive closure -- Rapid
Type Analysis. It over-approximates, which is the safe direction for a denominator.

Outputs (into --outdir): edges.txt.gz, defs.jsonl.gz, elf-stats.json.

Usage (inside the coverage image):
    python3 engine_elf.py --build-dir /src/build --outdir /work/tools/coverage/out/scope
"""

import argparse
import gzip
import json
import os
import re
import struct
import subprocess
import sys
import time
from bisect import bisect_right

# "   1fd78:\tcall   1f480 <_Z41__static_initialization_and_destruction_0v>"
_BRANCH_RX = re.compile(
    rb"^\s+([0-9a-f]+):\s+(?:call|jmp)\s+([0-9a-f]+) <", re.M)

SHT_SYMTAB = 2
SHT_RELA = 4
SHT_SYMTAB_SHNDX = 18
SHF_ALLOC = 0x2
SHN_UNDEF = 0
SHN_ABS = 0xFFF1
SHN_COMMON = 0xFFF2
SHN_XINDEX = 0xFFFF

STT_FUNC, STT_SECTION, STT_FILE = 2, 3, 4
STB_LOCAL = 0

# Allocatable sections whose relocations are deliberately dropped.
#   .eh_frame          FDEs point at every function start; keeping them would make a
#                      single pseudo-node reference the entire TU.
#   .gcc_except_table  points at typeinfo/landing pads, never introduces a new callee.
SKIP_RELOC_SECTIONS = (".eh_frame", ".gcc_except_table")

# Relocation targets that are pure instrumentation or string data. Dropping them is
# not a soundness compromise: nothing reachable *through* them is code.
def _is_noise(name):
    return (not name) or name.startswith("__gcov") or name.startswith(".L")


class ElfError(Exception):
    pass


def _u(fmt, buf, off):
    return struct.unpack_from(fmt, buf, off)


class Graph:
    def __init__(self):
        self.edges = set()

    def add(self, u, v):
        if u != v:
            self.edges.add((u, v))


def parse_object(path, objrel, graph, stats):
    with open(path, "rb") as fh:
        data = fh.read()

    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ElfError("not an ELF64 LSB file")

    (e_shoff,) = _u("<Q", data, 0x28)
    (e_shentsize,) = _u("<H", data, 0x3A)
    (e_shnum,) = _u("<H", data, 0x3C)
    (e_shstrndx,) = _u("<H", data, 0x3E)

    def shdr(i):
        o = e_shoff + i * e_shentsize
        name, typ, flags, addr, offset, size, link, info, align, entsize = _u(
            "<IIQQQQIIQQ", data, o)
        return dict(name=name, type=typ, flags=flags, offset=offset, size=size,
                    link=link, info=info)

    s0 = shdr(0)
    if e_shnum == 0:
        e_shnum = s0["size"]
    if e_shstrndx == SHN_XINDEX:
        e_shstrndx = s0["link"]

    sections = [shdr(i) for i in range(e_shnum)]
    shstr = sections[e_shstrndx]
    shstrtab = data[shstr["offset"]:shstr["offset"] + shstr["size"]]

    def sname(sec):
        end = shstrtab.find(b"\0", sec["name"])
        return shstrtab[sec["name"]:end].decode("utf-8", "replace")

    secnames = [sname(s) for s in sections]

    symtab_idx = xindex_idx = None
    for i, s in enumerate(sections):
        if s["type"] == SHT_SYMTAB:
            symtab_idx = i
        elif s["type"] == SHT_SYMTAB_SHNDX:
            xindex_idx = i
    if symtab_idx is None:
        return []

    symtab = sections[symtab_idx]
    strtab = sections[symtab["link"]]
    strdata = data[strtab["offset"]:strtab["offset"] + strtab["size"]]
    symdata = data[symtab["offset"]:symtab["offset"] + symtab["size"]]
    nsyms = symtab["size"] // 24

    xshndx = None
    if xindex_idx is not None:
        xs = sections[xindex_idx]
        xshndx = data[xs["offset"]:xs["offset"] + xs["size"]]

    names = [None] * nsyms
    binds = [0] * nsyms
    types = [0] * nsyms
    shndxs = [0] * nsyms
    values = [0] * nsyms
    sizes = [0] * nsyms
    for i in range(nsyms):
        st_name, st_info, _st_other, st_shndx, st_value, st_size = _u(
            "<IBBHQQ", symdata, i * 24)
        if st_shndx == SHN_XINDEX and xshndx is not None:
            (st_shndx,) = _u("<I", xshndx, i * 4)
        end = strdata.find(b"\0", st_name)
        names[i] = strdata[st_name:end].decode("utf-8", "replace")
        binds[i] = st_info >> 4
        types[i] = st_info & 0xF
        shndxs[i] = st_shndx
        values[i] = st_value
        sizes[i] = st_size

    # node id for each symbol index
    def node_id(i):
        n = names[i]
        if binds[i] == STB_LOCAL:
            return objrel + "\x01" + n
        return n

    nodeid = [None] * nsyms

    # Per-section interval index over SIZED defined symbols, plus an exact-address
    # map for zero-sized ones. Keeping the zero-sized symbols out of the sorted list
    # is what makes the lookup O(log n) instead of a backward scan (the SWIG wrapper
    # object has 100k symbols in one section and made the naive version quadratic).
    sized = {}
    exact = {}
    for i in range(nsyms):
        t = types[i]
        if t in (STT_SECTION, STT_FILE):
            continue
        sh = shndxs[i]
        if sh in (SHN_UNDEF, SHN_ABS, SHN_COMMON) or sh >= len(sections):
            continue
        nodeid[i] = node_id(i)
        if sizes[i]:
            sized.setdefault(sh, []).append((values[i], values[i] + sizes[i], i))
        else:
            exact.setdefault(sh, {}).setdefault(values[i], i)
    for v in sized.values():
        v.sort()
    starts = {k: [x[0] for x in v] for k, v in sized.items()}

    # --- alias unification ---------------------------------------------------
    # GCC emits ctors (C1/C2/C5) and dtors (D0/D1/D2/D5) as multiple symbols at ONE
    # address. Callers relocate against C1 but gcov attributes lines to whichever
    # name owns the body, so symbols sharing an address are linked both ways.
    for shndx, lst in sized.items():
        i = 0
        while i < len(lst):
            j = i
            while j + 1 < len(lst) and lst[j + 1][0] == lst[i][0]:
                j += 1
            if j > i:
                grp = [k for _v, _e, k in lst[i:j + 1]
                       if types[k] == STT_FUNC and nodeid[k] is not None]
                if len(grp) > 1:
                    stats["alias_groups"] += 1
                    head = grp[0]
                    for k in grp[1:]:
                        graph.add(nodeid[head], nodeid[k])
                        graph.add(nodeid[k], nodeid[head])
            i = j + 1

    def owner(shndx, off):
        lst = sized.get(shndx)
        if lst:
            pos = bisect_right(starts[shndx], off) - 1
            if pos >= 0:
                s, e, i = lst[pos]
                if s <= off < e:
                    return i
        ex = exact.get(shndx)
        if ex is not None:
            i = ex.get(off)
            if i is not None:
                return i
        return -1

    defs = []
    for i in range(nsyms):
        if nodeid[i] is None:
            continue
        if _is_noise(names[i]):
            continue
        defs.append([names[i], binds[i], types[i], secnames[shndxs[i]]])

    text_shndx = None
    for i, nm in enumerate(secnames):
        if nm == ".text":
            text_shndx = i
            break
    text_reloc_sites = set()

    for si, s in enumerate(sections):
        if s["type"] != SHT_RELA:
            continue
        target = s["info"]
        if target >= len(sections):
            continue
        tsec = sections[target]
        if not (tsec["flags"] & SHF_ALLOC):
            continue  # .debug_*, .comment -- the bulk, and irrelevant
        tname = secnames[target]
        if any(tname.startswith(p) for p in SKIP_RELOC_SECTIONS):
            continue
        n = s["size"] // 24
        rd = data[s["offset"]:s["offset"] + s["size"]]
        for r in range(n):
            r_offset, r_info, r_addend = _u("<QQq", rd, r * 24)
            symidx = r_info >> 32
            if target == text_shndx:
                text_reloc_sites.add(r_offset)
            if symidx == 0 or symidx >= nsyms:
                continue

            # --- source side -------------------------------------------------
            # Which defined symbol owns the byte range this relocation sits in?
            fi = owner(target, r_offset)
            if fi < 0 or nodeid[fi] is None:
                # An anonymous relocation site (.init_array slot, jump table,
                # compiler-generated data). Deliberately NOT a per-section pseudo-node
                # -- that once smeared every string literal into every function of the
                # same object. Counted for auditability; must be ~zero for `.text`,
                # where it would mean losing a real call edge.
                stats["site_anon"] += 1
                k = "site_anon_by_section"
                stats[k][tname] = stats[k].get(tname, 0) + 1
                continue
            u = nodeid[fi]

            # --- target side -------------------------------------------------
            if types[symidx] == STT_SECTION:
                # A reference to an anonymous address inside a section (a string
                # constant, a gcov counter, a jump table). Resolve by address --
                # PC32 carries a -4 bias, so try both -- and if nothing owns it,
                # drop: an anonymous datum is a sink, it never introduces code.
                cand = owner(shndxs[symidx], r_addend)
                if cand < 0:
                    cand = owner(shndxs[symidx], r_addend + 4)
                if cand < 0:
                    tsn = secnames[shndxs[symidx]]
                    stats["target_anon"] += 1
                    k = "target_anon_by_section"
                    stats[k][tsn] = stats[k].get(tsn, 0) + 1
                    continue
                symidx = cand
            tn = names[symidx]
            if _is_noise(tn):
                continue
            v = nodeid[symidx] if nodeid[symidx] is not None else (
                objrel + "\x01" + tn if binds[symidx] == STB_LOCAL else tn)
            graph.add(u, v)

    # --- intra-.text direct calls, recovered by disassembly -------------------
    # A `call` between two LOCAL symbols in the SAME section is resolved by the
    # assembler and leaves NO relocation, so a relocation-only graph silently loses
    # every static->static call (e.g. `_GLOBAL__sub_I_<tu>` calling the TU's static
    # init function) -- with those missing, DRC test providers fell out of the
    # denominator while the suite was demonstrably executing them. objdump recovers
    # the branch target; sites that DO carry a relocation are skipped (there the
    # displacement is a zero placeholder and the printed "target" is meaningless).
    if text_shndx is not None and sized.get(text_shndx):
        stats["disasm_objects"] += 1
        try:
            p = subprocess.run(
                ["objdump", "-d", "-j", ".text", "--no-show-raw-insn", path],
                capture_output=True, timeout=900)
            starts_set = {v for v, _e, _i in sized[text_shndx]}
            for m in _BRANCH_RX.finditer(p.stdout):
                site = int(m.group(1), 16)
                tgt = int(m.group(2), 16)
                if site + 1 in text_reloc_sites:
                    continue
                if tgt not in starts_set:
                    continue  # a jump inside a function, not a call to one
                fi = owner(text_shndx, site)
                ti = owner(text_shndx, tgt)
                if fi < 0 or ti < 0 or nodeid[fi] is None or nodeid[ti] is None:
                    continue
                if _is_noise(names[fi]) or _is_noise(names[ti]):
                    continue
                stats["disasm_edges"] += 1
                graph.add(nodeid[fi], nodeid[ti])
        except subprocess.TimeoutExpired:
            stats["disasm_timeouts"] += 1

    return defs


def source_for_object(objpath, build_dir):
    """.../CMakeFiles/<target>.dir/<relpath>.o  ->  KiCad-relative source path.
    CMake writes out-of-tree sources as `__/` segments; those are `..`."""
    rel = os.path.relpath(objpath, build_dir)
    parts = rel.split(os.sep)
    try:
        i = parts.index("CMakeFiles")
    except ValueError:
        return None
    tail = [".." if p == "__" else p for p in parts[i + 2:]]
    joined = parts[:i] + tail
    if not joined:
        return None
    src = os.path.normpath("/".join(joined)).replace(os.sep, "/")
    return src[:-2] if src.endswith(".o") else src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", default="/src/build")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--objects-from")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    if args.objects_from:
        with open(args.objects_from) as fh:
            objs = [l.strip() for l in fh if l.strip()]
    else:
        objs = []
        for root, _d, files in os.walk(args.build_dir):
            for f in files:
                if f.endswith(".o"):
                    objs.append(os.path.join(root, f))
    objs.sort()

    graph = Graph()
    stats = {"objects": len(objs), "defs": 0, "failed": 0,
             "site_anon": 0, "site_anon_by_section": {},
             "target_anon": 0, "target_anon_by_section": {},
             "disasm_objects": 0, "disasm_edges": 0, "disasm_timeouts": 0,
             "alias_groups": 0}
    failed = []
    t0 = time.time()

    defs_path = os.path.join(args.outdir, "defs.jsonl.gz")
    with gzip.open(defs_path, "wt", compresslevel=1) as out:
        for k, o in enumerate(objs):
            objrel = os.path.relpath(o, args.build_dir).replace(os.sep, "/")
            try:
                defs = parse_object(o, objrel, graph, stats)
            except Exception as exc:  # noqa: BLE001
                failed.append((o, repr(exc)))
                stats["failed"] += 1
                continue
            stats["defs"] += len(defs)
            out.write(json.dumps(
                {"o": objrel, "src": source_for_object(o, args.build_dir),
                 "syms": defs}, separators=(",", ":")) + "\n")
            if (k + 1) % 250 == 0:
                print(f"  ... {k+1}/{len(objs)} objects, {len(graph.edges)} edges, "
                      f"{time.time()-t0:.0f}s", file=sys.stderr, flush=True)

    stats["edges"] = len(graph.edges)
    stats["seconds"] = round(time.time() - t0, 1)

    edges_path = os.path.join(args.outdir, "edges.txt.gz")
    with gzip.open(edges_path, "wt", compresslevel=1) as out:
        for u, v in sorted(graph.edges):
            out.write(u + "\t" + v + "\n")

    # Soundness gate. A dropped relocation site in a `.text` section would be a
    # lost call edge -- an under-approximated denominator, the dangerous direction.
    # At -O0 every instruction lives inside a sized function symbol, so this must
    # be zero (or a rounding error against ~4M edges).
    text_anon = sum(v for k, v in stats["site_anon_by_section"].items()
                    if k.startswith(".text"))
    stats["site_anon_in_text"] = text_anon
    with open(os.path.join(args.outdir, "elf-stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)

    print(json.dumps({k: v for k, v in stats.items()
                      if not k.endswith("_by_section")}), file=sys.stderr)
    if text_anon:
        print(f"WARNING: {text_anon} relocation sites in .text sections were not "
              f"covered by any function symbol -- those call edges are LOST.",
              file=sys.stderr)
    for o, e in failed[:20]:
        print(f"  FAILED {o}: {e}", file=sys.stderr)
    if failed:
        # A parse failure is a silently missing chunk of the graph -- the exact
        # failure mode this project has been burned by twice. Make it fatal.
        sys.exit(3)


if __name__ == "__main__":
    main()
