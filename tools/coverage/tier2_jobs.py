#!/usr/bin/env python3
"""Build the Tier-2 job list: one `base` job per case
(every case contributes to `executed`, happy or rejection) and one `pert` job per
perturbation Tier 1 already scored ASSERTED (an INERT/CRASH/INVALID-PERTURBATION
perturbation credits nothing by construction -- §4.2 -- so running it under gcov is
pure waste; this is the main cost-control decision beyond per-run isolation itself).

Usage:
  python3 tier2_jobs.py --suites-root suites --tier1 tier1-results.json --out jobs.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def safe_id(case_posix: str, slug: str | None = None) -> str:
    # case_posix looks like "suites/board-parse/populated-board"
    stem = case_posix.replace("suites/", "", 1).replace("/", "__")
    return f"{stem}__{slug}" if slug else stem


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites-root", default="suites")
    ap.add_argument("--tier1", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.tier1, encoding="utf-8") as f:
        tier1 = json.load(f)

    # Grouped by top-level suite (board-parse, drc, erc, footprint-lib, schematic-parse,
    # symbol-lib), each group sorted -- then ROUND-ROBINED across groups below. A
    # multi-hour run over Docker Desktop is exactly the kind of thing that gets
    # interrupted (this project's Docker has crashed under load before), and a plain
    # alphabetical case order would spend the first ~95 jobs entirely inside
    # board-parse before touching drc/erc/schematic-parse/symbol-lib at all -- a run cut
    # short at, say, job 60 would say nothing about 5 of the 6 subsystems. Round-robin
    # means a truncated run yields a representative sample across every subsystem as
    # early as possible, not a complete picture of one and silence on the rest.
    by_suite: dict[str, list[str]] = {}
    for p in Path(args.suites_root).rglob("case.toml"):
        case = p.parent.as_posix()
        suite = case.split("/")[1]  # "suites/<suite>/<case>"
        by_suite.setdefault(suite, []).append(case)
    for suite in by_suite:
        by_suite[suite].sort()

    all_cases = []
    suite_names = sorted(by_suite)
    i = 0
    while any(i < len(by_suite[s]) for s in suite_names):
        for s in suite_names:
            if i < len(by_suite[s]):
                all_cases.append(by_suite[s][i])
        i += 1

    # Within a case: base job immediately followed by that case's own ASSERTED pert
    # job(s) -- credit needs base AND pert for the same case, so interleaving at this
    # level too means a truncated run's prefix is always fully credit-computable rather
    # than "every base done, no perturbations done".
    jobs = []
    n_asserted = 0
    n_other = 0
    for case in all_cases:
        jobs.append({
            "run_id": "base__" + safe_id(case),
            "kind": "base",
            "case_dir": case,
            "pert_slug": None,
        })
        info = tier1["cases"].get(case)
        if not info:
            continue
        for p in info["perturbations"]:
            if p["status"] != "ASSERTED":
                n_other += 1
                continue
            n_asserted += 1
            jobs.append({
                "run_id": "pert__" + safe_id(case, p["slug"]),
                "kind": "pert",
                "case_dir": case,
                "pert_slug": p["slug"],
            })

    Path(args.out).write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    print(
        f"{len(all_cases)} base jobs (all cases) + {n_asserted} pert jobs "
        f"(ASSERTED perturbations; skipped {n_other} non-ASSERTED) = {len(jobs)} total "
        f"-> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
