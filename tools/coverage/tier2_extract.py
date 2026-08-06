#!/usr/bin/env python3
"""Runs INSIDE the coverage image. Extracts per-line execution counts for exactly the
`.gcda` files a single isolated run touched, aggregated into one compact JSON.

This is the piece of §4.1 that turns a `GCOV_PREFIX` bucket into `base[C]`/`pert[C,P]`:
a mapping `{source_file: {line_number: count}}`, restricted to files under `--src-dir`
(KiCad's own tree -- the same scope `collect.sh` filters to) so the JSON stays small.

Why per-.gcda, not gcovr over the whole tree: a single run's bucket only ever touches a
few hundred to a few thousand of the ~1900 instrumented objects (whatever got dlopen'd /
linked for that one kicad-cli invocation); gcovr's normal invocation walks the *entire*
object tree every time; run 275 times that difference is hours vs minutes. `gcov -j
--stdout` on exactly the touched files, parallelized, was measured at 316 files / 0.7s on
this workstation (16 vCPU) -- see tools/coverage/README.md's Tier-2 section.

Usage (inside the coverage image):
  python3 tier2_extract.py --bucket /coverage/raw/tier2/<run_id>/src/build \
                           --build-dir /src/build --src-dir /src/kicad \
                           --out /work/tools/coverage/out/tier2/perline/<run_id>.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

# Same exclusions as collect.sh's gcovr invocation, applied here at the line level
# instead of the file level -- vendored code, upstream's own tests, and generated
# sources are not KiCad code and would only bloat the credit/gap report.
EXCLUDE_SUBSTRINGS = ("/thirdparty/", "/qa/", "/build/", "/CMakeFiles/")
EXCLUDE_SUFFIXES = ("_wrap.cxx",)
EXCLUDE_DOUBLE_SUFFIXES = (".pb.cc", ".pb.h")


def _wanted(file_path: str, src_dir: str) -> bool:
    if not file_path.startswith(src_dir):
        return False
    if any(s in file_path for s in EXCLUDE_SUBSTRINGS):
        return False
    if file_path.endswith(EXCLUDE_SUFFIXES):
        return False
    if any(file_path.endswith(s) for s in EXCLUDE_DOUBLE_SUFFIXES):
        return False
    return True


def _run_one(rel: Path, build_dir: Path) -> dict:
    # IMPORTANT: `gcov -o <objdir> <path>` resolves the `.gcda` to open by taking
    # <objdir>/<basename-of-path>, NOT by opening <path> itself -- passing the bucket's
    # copy directly here silently produced all-zero counts ("cannot open data file,
    # assuming not executed" on stderr, easy to miss) because gcov went looking for the
    # basename back in the REAL /src/build, where GCOV_PREFIX had never written anything.
    # The caller must have already grafted the bucket's .gcda onto `build_dir` (matching
    # collect.sh's approach) before this runs; `gcda_real` below is that grafted path.
    gcda_real = build_dir / rel
    objdir = gcda_real.parent
    try:
        proc = subprocess.run(
            ["gcov", "-j", "-t", "-o", str(objdir), str(gcda_real)],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {}
    if not proc.stdout.strip():
        return {}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True,
                     help="GCOV_PREFIX bucket's .../src/build subtree -- used ONLY to "
                          "enumerate which relative paths were touched; the caller must "
                          "have already grafted (cp -a) this onto --build-dir")
    ap.add_argument("--build-dir", required=True,
                     help="the real /src/build, with this run's .gcda already grafted "
                          "onto it (co-located with the baked-in .gcno)")
    ap.add_argument("--src-dir", required=True, help="KiCad source root, e.g. /src/kicad")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    bucket_root = Path(args.bucket)
    build_dir = Path(args.build_dir)

    if not bucket_root.is_dir():
        # Nothing was touched (e.g. the case was SKIPped by the adapter, or crashed
        # before any instrumented code ran). Write an empty map rather than erroring --
        # the caller (the worker script) decides whether that is itself a finding.
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({}), encoding="utf-8")
        print("WARNING: no bucket directory -- 0 files touched", file=sys.stderr)
        return 0

    gcdas = list(bucket_root.rglob("*.gcda"))
    print(f"extracting {len(gcdas)} touched objects from {bucket_root}", file=sys.stderr)

    agg: dict[str, dict[str, int]] = {}
    n_files_seen = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = [ex.submit(_run_one, g.relative_to(bucket_root), build_dir) for g in gcdas]
        for fut in concurrent.futures.as_completed(futures):
            data = fut.result()
            for f in data.get("files", []):
                path = f.get("file", "")
                if not _wanted(path, args.src_dir):
                    continue
                n_files_seen += 1
                line_map = agg.setdefault(path, {})
                for line in f.get("lines", []):
                    # gcov's per-line "count" can be absent for non-executable lines
                    # (comments/braces) -- gcov only emits an entry when it instrumented
                    # the line at all, so absence here is "not a countable line", not 0.
                    if "count" not in line:
                        continue
                    ln = str(line["line_number"])
                    # SUM across TUs (§4.1): a header included by several .cpp files
                    # gets counted once per TU that includes it; the correct per-line
                    # total is the sum, matching gcovr's own aggregation.
                    line_map[ln] = line_map.get(ln, 0) + int(line["count"])

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(agg, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}: {len(agg)} distinct KiCad source files, "
          f"{n_files_seen} (file x TU) entries aggregated", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
