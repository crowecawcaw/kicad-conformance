#!/usr/bin/env python3
"""The Tier-2 gap report. Joins:

  - tier1-results.json          (which perturbations moved a recorded answer, and how)
  - tier2/perline/base__*.json  (per-line execution counts of each case's OWN input)
  - tier2/perline/pert__*.json  (per-line execution counts under each ASSERTED
                                  perturbation's input)

into three artifacts:

  asserted.json          per file: line_total / line_covered(executed) / line_asserted /
                          line_asserted_semantic
  asserted-credit.json   per (case, perturbation): credited line count, moved answers,
                          fan-out, low-specificity flag
  asserted-gap.md        the human-readable report: headline table, gap list, answers
                          moved, cases with no perturbation, fan-out distribution

Algorithm:

    executed  = { L : sum over C of base[C][L] > 0 }
    credited[C,P] = { L : base[C][L] > 0 and base[C][L] != pert[C,P][L] }   if P moved an answer
                  = {}                                                      otherwise
    asserted          = union of credited[C,P]
    asserted_semantic = union of credited[C,P] where label == "semantic"

A line missing from pert[C,P] entirely counts as pert count 0 (it simply didn't execute
under the perturbation) -- a real, credit-worthy inequality, not a missing-data case.

SCOPE NOTE (a documented simplification, flagged rather than silently made): this
measures LINE assertion only, not branches. Branches would follow the identical rule
against gcov's branch counters, which needs a second counter stream this script does not
extract (tier2_extract.py pulls `lines[].count` only, not `lines[].branches[]`). Branch
data is also noisy from exception edges; line-level is the number that matters most for
"is the headline gap real", and branch-level is left for a follow-up. Likewise the gap
list is per FILE, not per function, matching the file-level rollups the existing coverage
tooling already produces.

CAVEAT ON THE HEADLINE (measured, not assumed): a large block of executed lines is
structurally un-assertable by *input* perturbation and drags the ratio down. `kicad/cli/
command_*` parses a pinned argv, so its counts are identical in every base-vs-pert pair
(verified: 1333 executed lines, 0 ever differ). Same for common/jobs descriptors, settings
loading and startup/paths. Asserting that code needs varying CLI *flags*, which the
adapter deliberately pins for determinism -- it is not a corpus gap. On the 2026-08-05
corpus this is ~7786 of 55177 executed lines: 37.5% asserted overall, 42.9% excluding it.

Usage:
  python3 asserted.py --tier1 tier1-results.json --perline-dir tier2/perline \
                      --pooled-coverage report/coverage.json \
                      --out-dir out/report
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from tier2_jobs import safe_id

BUCKETS = [
    ("io/board",      ("pcbnew/pcb_io",)),
    ("io/schematic",  ("eeschema/sch_io",)),
    ("io/common",     ("common/io", "libs/sexpr", "common/drawing_sheet")),
    ("drc",           ("pcbnew/drc",)),
    ("erc",           ("eeschema/erc",)),
    ("netlist",       ("eeschema/netlist_exporters", "common/netlist_reader",
                       "pcbnew/netlist_reader")),
    ("connectivity",  ("pcbnew/connectivity", "pcbnew/ratsnest")),
    ("export/plot",   ("pcbnew/exporters", "common/plotters", "eeschema/printing")),
    ("cli/jobs",      ("kicad/cli", "common/jobs", "jobs_handler")),
    ("geometry",      ("libs/kimath", "libs/core")),
    ("gui",           ("/dialogs/", "/widgets/", "/tools/", "common/gal",
                       "preview_items", "3d-viewer")),
]


def bucket_of(path: str) -> str:
    for name, pats in BUCKETS:
        if any(p in path for p in pats):
            return name
    return "other"


def load_perline(perline_dir: Path, run_id: str) -> dict[str, dict[str, int]] | None:
    p = perline_dir / f"{run_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier1", required=True)
    ap.add_argument("--perline-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--unasserted-cases", default=None,
                     help="tools/coverage/out/asserted-cases.txt, for §4.3 item 4")
    args = ap.parse_args()

    tier1 = json.loads(Path(args.tier1).read_text(encoding="utf-8"))
    perline_dir = Path(args.perline_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_base_files = sorted(p.name for p in perline_dir.glob("base__*.json"))
    all_pert_files = sorted(p.name for p in perline_dir.glob("pert__*.json"))
    print(f"found {len(all_base_files)} base run(s), {len(all_pert_files)} pert run(s) "
          f"under {perline_dir}")

    # ---- executed: union over every collected base run --------------------------
    # file -> set of line numbers with count > 0, anywhere.
    executed: dict[str, set[str]] = defaultdict(set)
    # file -> line -> total count, summed over every case's base run (for line_total's
    # sibling "how hot" isn't needed here, only >0-ness, but keep for possible reuse).
    n_base_loaded = 0
    for fname in all_base_files:
        data = json.loads((perline_dir / fname).read_text(encoding="utf-8"))
        n_base_loaded += 1
        for file_path, lines in data.items():
            s = executed[file_path]
            for ln, cnt in lines.items():
                if cnt > 0:
                    s.add(ln)
    print(f"loaded {n_base_loaded} base runs; executed set spans {len(executed)} files")

    # ---- credit: per (case, ASSERTED perturbation) -------------------------------
    credit_records = []
    asserted: dict[str, set[str]] = defaultdict(set)
    asserted_semantic: dict[str, set[str]] = defaultdict(set)
    answers_moved_counts: dict[str, int] = defaultdict(int)
    missing_base = []
    missing_pert = []

    for case, info in tier1["cases"].items():
        base_run_id = "base__" + safe_id(case)
        base_data = load_perline(perline_dir, base_run_id)
        if base_data is None:
            missing_base.append(case)
            continue
        for p in info["perturbations"]:
            if p["status"] != "ASSERTED":
                continue
            pert_run_id = "pert__" + safe_id(case, p["slug"])
            pert_data = load_perline(perline_dir, pert_run_id)
            if pert_data is None:
                missing_pert.append(pert_run_id)
                continue

            credited_lines = 0
            for file_path, base_lines in base_data.items():
                pert_lines = pert_data.get(file_path, {})
                for ln, base_cnt in base_lines.items():
                    if base_cnt <= 0:
                        continue
                    pert_cnt = pert_lines.get(ln, 0)
                    if pert_cnt != base_cnt:
                        asserted[file_path].add(ln)
                        if p["label"] == "semantic":
                            asserted_semantic[file_path].add(ln)
                        credited_lines += 1

            for a in p["moved"]:
                answers_moved_counts[a] += 1

            credit_records.append({
                "case": case,
                "slug": p["slug"],
                "label": p["label"],
                "moved": p["moved"],
                "credited_lines": credited_lines,
            })

    if missing_base:
        print(f"WARNING: {len(missing_base)} case(s) have no base perline data yet "
              f"(partial run) -- their perturbations, if any were processed, are skipped "
              f"above too since credit needs both sides")
    if missing_pert:
        print(f"WARNING: {len(missing_pert)} ASSERTED perturbation(s) have no pert "
              f"perline data yet (partial run): {missing_pert[:5]}{'...' if len(missing_pert) > 5 else ''}")

    # ---- fan-out distribution + low-specificity flag -----------------------------
    fanouts = sorted(r["credited_lines"] for r in credit_records if r["credited_lines"] > 0)
    p90 = fanouts[int(0.9 * (len(fanouts) - 1))] if fanouts else 0
    for r in credit_records:
        r["low_specificity"] = r["credited_lines"] > p90 if p90 else False

    # ---- per-file rollup: line_total/covered come from the base runs themselves; --
    # we did not re-run gcovr, so "line_total" here means "distinct lines gcov reported
    # ANY count for" (0 or >0) across all base runs -- i.e. instrumented+reachable
    # lines seen. This is intentionally derived from the SAME data as `executed`,
    # not from a separately-run pooled gcovr pass, so the two numbers in this report
    # are self-consistent (measured together, same runs, same date).
    line_universe: dict[str, set[str]] = defaultdict(set)
    for fname in all_base_files:
        data = json.loads((perline_dir / fname).read_text(encoding="utf-8"))
        for file_path, lines in data.items():
            line_universe[file_path].update(lines.keys())

    per_file = {}
    for file_path in sorted(line_universe):
        total = len(line_universe[file_path])
        covered = len(executed.get(file_path, set()))
        asrt = len(asserted.get(file_path, set()))
        asrt_sem = len(asserted_semantic.get(file_path, set()))
        per_file[file_path] = {
            "line_total": total,
            "line_covered": covered,
            "line_asserted": asrt,
            "line_asserted_semantic": asrt_sem,
        }
        assert asrt <= covered, f"asserted > executed for {file_path} -- {asrt} > {covered}"

    (out_dir / "asserted.json").write_text(json.dumps(per_file, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "asserted-credit.json").write_text(
        json.dumps({"p90_fanout": p90, "records": credit_records}, indent=2), encoding="utf-8"
    )

    # ---- subsystem rollup ----------------------------------------------------
    roll = defaultdict(lambda: {"lines": 0, "covered": 0, "asserted": 0, "asserted_semantic": 0, "files": 0})
    for file_path, v in per_file.items():
        b = roll[bucket_of(file_path)]
        b["lines"] += v["line_total"]
        b["covered"] += v["line_covered"]
        b["asserted"] += v["line_asserted"]
        b["asserted_semantic"] += v["line_asserted_semantic"]
        b["files"] += 1

    unasserted_cases = []
    if args.unasserted_cases and Path(args.unasserted_cases).exists():
        lines = [l.strip() for l in Path(args.unasserted_cases).read_text(encoding="utf-8").splitlines() if l.strip()]
        unasserted_cases = [l for l in lines[1:] if l.startswith("suites/")]

    # ---- gap list: covered>0, asserted==0, sorted by covered lines desc ----------
    gap_list = sorted(
        (
            (fp, v["line_covered"], v["line_asserted"])
            for fp, v in per_file.items()
            if v["line_covered"] > 0 and v["line_asserted"] == 0
        ),
        key=lambda t: -t[1],
    )

    lines_out = []
    lines_out.append("# Tier-2 asserted-coverage gap report\n")
    lines_out.append(
        f"Generated from {n_base_loaded} per-case base runs and "
        f"{len([r for r in credit_records])} credited (case, ASSERTED-perturbation) pairs "
        f"under `tools/coverage/out/tier2/perline/`.\n"
    )
    if missing_base or missing_pert:
        lines_out.append(
            f"**PARTIAL RUN**: {len(missing_base)} case(s) and {len(missing_pert)} "
            f"perturbation(s) had no data yet at generation time -- see the run log. "
            f"The numbers below are computed over whatever was collected; re-run "
            f"`tools/coverage/tier2-run.sh` (resumable) and regenerate this report for "
            f"the complete figure.\n"
        )
    lines_out.append("## 1. Headline: executed vs asserted, per subsystem\n")
    lines_out.append("| bucket | lines | covered (executed) | asserted | asserted (semantic) | asserted/executed |")
    lines_out.append("|---|---:|---:|---:|---:|---:|")
    total_lines = total_cov = total_asrt = total_asrt_sem = 0
    for name, b in sorted(roll.items(), key=lambda kv: -kv[1]["lines"]):
        pct = (100.0 * b["asserted"] / b["covered"]) if b["covered"] else 0.0
        lines_out.append(
            f"| {name} | {b['lines']} | {b['covered']} | {b['asserted']} | "
            f"{b['asserted_semantic']} | {pct:.1f}% |"
        )
        total_lines += b["lines"]; total_cov += b["covered"]
        total_asrt += b["asserted"]; total_asrt_sem += b["asserted_semantic"]
    overall_pct = (100.0 * total_asrt / total_cov) if total_cov else 0.0
    lines_out.append(
        f"| **TOTAL** | {total_lines} | {total_cov} | {total_asrt} | {total_asrt_sem} | "
        f"{overall_pct:.1f}% |"
    )
    lines_out.append("")
    lines_out.append(
        "`covered`/`executed` here is derived from the SAME per-case instrumented runs "
        "as `asserted` (not the separately-run pooled `run-suite.sh` pass), so the two "
        "columns are measured together and directly comparable. It will not exactly "
        "match the pooled `run-suite.sh` figure (different run, and this report only "
        "counts lines gcov reported *any* count for across per-case runs -- see the "
        "script's docstring)."
    )
    lines_out.append("")

    lines_out.append("## 2. Fan-out and low-specificity perturbations\n")
    if fanouts:
        lines_out.append(
            f"Corpus of {len(fanouts)} credited perturbations. Fan-out (credited lines per "
            f"perturbation): median {statistics.median(fanouts):.0f}, p90 {p90}, "
            f"max {max(fanouts)}.\n"
        )
        top10 = sorted(credit_records, key=lambda r: -r["credited_lines"])[:10]
        lines_out.append("Top 10 by fan-out:\n")
        lines_out.append("| case | perturbation | credited lines | label | low-specificity |")
        lines_out.append("|---|---|---:|---|---|")
        for r in top10:
            lines_out.append(
                f"| {r['case']} | {r['slug']} | {r['credited_lines']} | {r['label']} | "
                f"{'yes' if r['low_specificity'] else ''} |"
            )
        lines_out.append("")
        low_spec = [r for r in credit_records if r["low_specificity"]]
        lines_out.append(
            f"**{len(low_spec)} perturbation(s) flagged `low-specificity`** "
            f"(fan-out above the corpus p90 of {p90}):\n"
        )
        for r in sorted(low_spec, key=lambda r: -r["credited_lines"]):
            lines_out.append(f"- `{r['case']}` / `{r['slug']}` -- {r['credited_lines']} lines credited ({r['label']})")
        lines_out.append("")
    else:
        lines_out.append("(no credited perturbations yet -- partial run)\n")

    lines_out.append("## 3. Answers moved (NOT a deletion candidate list)\n")
    lines_out.append(
        "How many credited perturbations were recorded as moving each answer kind. "
        "These counts are **battery-order-biased and cannot be read as "
        "'this answer does no work'**: `generate_and_compare_against_committed` "
        "(runner/engine.py) stops at the *first* answer that differs, so an answer is "
        "only ever credited when every earlier answer in its battery stayed identical. "
        "The board battery runs `stats, pos, ipcd356, render, gerbers, drill`, so "
        "`gerbers`/`drill` are structurally starved while `stats` is over-represented. "
        "Deciding an answer is dead needs a run with the short-circuit disabled.\n"
    )
    for name, n in sorted(answers_moved_counts.items(), key=lambda kv: -kv[1]):
        lines_out.append(f"- `{name}`: moved by {n} perturbation(s)")
    lines_out.append("")

    lines_out.append("## 4. Cases with no perturbation (UNASSERTED-CASE)\n")
    lines_out.append(f"{len(unasserted_cases)} happy case(s) carry no `perturb/` directory:\n")
    for c in unasserted_cases:
        lines_out.append(f"- `{c}`")
    lines_out.append("")

    lines_out.append(f"## 5. Gap list -- covered but nothing asserts it ({len(gap_list)} files)\n")
    lines_out.append("Files with `covered > 0` and `asserted == 0`, sorted by covered-line count:\n")
    lines_out.append("| file | covered | asserted |")
    lines_out.append("|---|---:|---:|")
    for fp, cov, asrt in gap_list[:60]:
        lines_out.append(f"| {fp} | {cov} | {asrt} |")
    if len(gap_list) > 60:
        lines_out.append(f"| ... ({len(gap_list) - 60} more) | | |")
    lines_out.append("")

    (out_dir / "asserted-gap.md").write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    print(f"wrote {out_dir/'asserted.json'}, {out_dir/'asserted-credit.json'}, {out_dir/'asserted-gap.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
