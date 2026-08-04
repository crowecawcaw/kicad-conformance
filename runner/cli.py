"""`python -m runner` -- see docs/ROADMAP.md M0 and docs/DESIGN.md for what this
implements. Flags are kept minimal (per the M0 task): PATHS, --regenerate, --adapter,
--determinism-check.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from runner.adapter import Adapter
from runner.assertions import (
    ASSERTED,
    FAILING_STATUSES,
    UNASSERTED_CASE,
    check_case_assertions,
)
from runner.determinism import check_determinism
from runner.engine import Engine, FAIL, PASS, REGENERATED, SKIP, XFAIL, make_tmp_root
from runner.manifest import CaseError, discover_cases, load_case
from runner.reduction_selftest import run_reduction_selftest


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m runner",
        description="kicad-conformance reference runner -- walks suites/, runs each "
                     "case.toml's checks through an adapter, and reports pass/fail.",
    )
    p.add_argument(
        "paths", nargs="*", default=["suites"],
        help="case/suite directories to run (default: suites/)",
    )
    p.add_argument(
        "--regenerate", action="store_true",
        help="write expected/<detected-version>/... from the current adapter's output "
             "instead of comparing against it (run inside the Docker Linux image so "
             "committed expected files stay LF/platform-canonical, DL-0016)",
    )
    p.add_argument(
        "--adapter", metavar="PATH", default=None,
        help="adapter executable to drive (default: the built-in kicad-cli adapter)",
    )
    p.add_argument(
        "--determinism-check", action="store_true",
        help="instead of a normal run, run every rich-output check twice per case and "
             "assert the normalized/reduced result is identical both times (§4a)",
    )
    p.add_argument(
        "--verify-assertions", action="store_true",
        help="instead of a normal run, check that each case's perturb/<slug>/ overlay "
             "still loads and makes at least one committed answer FAIL (docs/"
             "ASSERTED_COVERAGE.md, DL-0030) -- mutually exclusive with --regenerate and "
             "--determinism-check",
    )
    p.add_argument(
        "--reduction-selftest", action="store_true",
        help="instead of a normal run, feed every reduction in runner/reduce.py and "
             "runner/summary.py hand-built non-empty input and assert the reduced result "
             "is both non-trivial and input-dependent -- no adapter/Docker/kicad-cli "
             "needed. Guards against a reduction (like the erc.json one this caught) "
             "that always returns the same shape regardless of its input.",
    )
    return p


def _print_case_header(case_dir: Path) -> None:
    print(f"\n{case_dir.as_posix()}")


def run_normal(adapter: Adapter, case_dirs: list[Path], regenerate: bool) -> int:
    engine = Engine(adapter, regenerate=regenerate)
    print(f"oracle: {adapter.identity().splitlines()[0] if adapter.identity() != 'unknown' else 'unknown'}")
    print(f"adapter version verb reports: {engine.version}")

    counts: dict[str, int] = {}
    failing_cases = 0
    skipped_cases = 0
    total_cases = 0

    with make_tmp_root() as tmp:
        tmp_path = Path(tmp)
        for idx, case_dir in enumerate(case_dirs):
            try:
                # Validate early so a malformed case.toml is reported clearly even if
                # the case ends up skipped for other reasons.
                load_case(case_dir)
            except CaseError as e:
                print(f"\n{case_dir.as_posix()}")
                print(f"  [INVALID] {e}")
                counts[FAIL] = counts.get(FAIL, 0) + 1
                failing_cases += 1
                total_cases += 1
                continue

            total_cases += 1
            case_tmp = tmp_path / f"case{idx}"
            result = engine.run_case(case_dir, case_tmp)
            _print_case_header(case_dir)
            if result.skipped:
                print(f"  [SKIP] {result.skip_reason}")
                skipped_cases += 1
                continue
            print(f"  concept: {result.case.concept}")
            case_failed = False
            for cr in result.check_results:
                counts[cr.status] = counts.get(cr.status, 0) + 1
                marker = f"[{cr.status}]"
                print(f"  {marker:16s} {cr.label}")
                if cr.detail and cr.status != PASS:
                    for line in cr.detail.splitlines():
                        print(f"      {line}")
                if cr.status not in (PASS, SKIP, REGENERATED, XFAIL):
                    case_failed = True
            if case_failed:
                failing_cases += 1

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"cases: {total_cases} total, {total_cases - failing_cases - skipped_cases} clean, "
          f"{failing_cases} with a failing check, {skipped_cases} skipped")
    for status in sorted(counts):
        print(f"  {status}: {counts[status]}")

    return 1 if failing_cases > 0 else 0


def run_determinism_mode(adapter: Adapter, case_dirs: list[Path]) -> int:
    print("Determinism self-test (DESIGN §4a): running each rich-output check twice "
          "per case and comparing normalized/reduced output.\n")
    any_fail = False
    any_ran = False
    with make_tmp_root() as tmp:
        tmp_path = Path(tmp)
        for idx, case_dir in enumerate(case_dirs):
            case_tmp = tmp_path / f"case{idx}"
            try:
                outcomes = check_determinism(adapter, case_dir, case_tmp)
            except CaseError as e:
                print(f"{case_dir.as_posix()}: [INVALID] {e}")
                any_fail = True
                continue
            if not outcomes:
                continue
            print(case_dir.as_posix())
            for o in outcomes:
                any_ran = True
                status = "PASS" if o.ok else "FAIL"
                print(f"  [{status}] {o.label}: {o.detail}")
                if not o.ok:
                    any_fail = True
    if not any_ran:
        print("(no checks with a recorded expected file found under the given paths)")
    print("\nDETERMINISM: " + ("FAIL" if any_fail else "PASS"))
    return 1 if any_fail else 0


def run_verify_assertions_mode(adapter: Adapter, case_dirs: list[Path]) -> int:
    """`--verify-assertions` (docs/ASSERTED_COVERAGE.md, DL-0030). Structurally a sibling
    of `run_determinism_mode`: an alternate mode, not an addition to the normal run.
    """
    print("Asserted-coverage check (docs/ASSERTED_COVERAGE.md): for each perturb/<slug>/ "
          "overlay, confirming it still loads and moves at least one committed answer.\n")
    engine = Engine(adapter, regenerate=False)
    print(f"adapter version verb reports: {engine.version}\n")

    any_fail = False
    unasserted_count = 0
    total_happy = 0
    status_counts: dict[str, int] = {}
    unasserted_case_dirs: list[Path] = []

    with make_tmp_root() as tmp:
        tmp_path = Path(tmp)
        for idx, case_dir in enumerate(case_dirs):
            try:
                result = check_case_assertions(engine, case_dir, tmp_path / f"case{idx}")
            except CaseError as e:
                print(f"{case_dir.as_posix()}: [INVALID] {e}")
                any_fail = True
                continue

            if result.skipped:
                continue

            if result.unasserted:
                total_happy += 1
                unasserted_count += 1
                unasserted_case_dirs.append(case_dir)
                continue

            if not result.outcomes:
                continue  # a rejection case with no `perturb/` -- not part of this report

            total_happy += 1
            print(case_dir.as_posix())
            print(f"  concept: {result.concept}")
            for o in result.outcomes:
                status_counts[o.status] = status_counts.get(o.status, 0) + 1
                if o.status == ASSERTED:
                    label = f"[{o.label}]" if o.label else ""
                    print(f"  [{o.status}]  {o.slug}  moved: {', '.join(o.moved)}  {label}")
                else:
                    print(f"  [{o.status}]  {o.slug}")
                    for line in o.detail.splitlines():
                        print(f"      {line}")
                if o.status in FAILING_STATUSES:
                    any_fail = True
            print()

    print("=" * 72)
    print("ASSERTED-COVERAGE SUMMARY")
    print("=" * 72)
    for status in sorted(status_counts):
        print(f"  {status}: {status_counts[status]}")
    print(f"\n{unasserted_count} of {total_happy} happy cases carry no perturbation "
          f"({UNASSERTED_CASE})")

    out_path = Path("tools/coverage/out/asserted-cases.txt")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{unasserted_count} of {total_happy} happy cases carry no perturb/ directory"]
        lines += [p.as_posix() for p in unasserted_case_dirs]
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {out_path}")
    except OSError as e:
        print(f"(could not write {out_path}: {e})")

    print("\nASSERTED-COVERAGE: " + ("FAIL" if any_fail else "PASS"))
    return 1 if any_fail else 0


def run_reduction_selftest_mode() -> int:
    """No adapter, no case discovery, no Docker -- see `runner/reduction_selftest.py`."""
    print("Reduction self-test: feeding every reduce_*/build_*_summary function "
          "hand-built non-empty input and asserting a non-trivial, input-dependent "
          "result (no adapter/Docker/kicad-cli needed).\n")
    any_fail = False
    for outcome in run_reduction_selftest():
        status = "PASS" if outcome.ok else "FAIL"
        print(f"  [{status}] {outcome.label}: {outcome.detail}")
        if not outcome.ok:
            any_fail = True
    print("\nREDUCTION-SELFTEST: " + ("FAIL" if any_fail else "PASS"))
    return 1 if any_fail else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.reduction_selftest:
        return run_reduction_selftest_mode()

    if args.verify_assertions and args.determinism_check:
        parser.error("--verify-assertions and --determinism-check are mutually exclusive alternate modes")
    if args.verify_assertions and args.regenerate:
        parser.error("--verify-assertions and --regenerate are mutually exclusive -- a perturbation run must never write expected/")

    adapter = Adapter(Path(args.adapter)) if args.adapter else Adapter()
    roots = [Path(p) for p in args.paths]
    case_dirs = discover_cases(roots)
    if not case_dirs:
        print(f"No case.toml found under {[str(r) for r in roots]}", file=sys.stderr)
        return 1

    if args.verify_assertions:
        return run_verify_assertions_mode(adapter, case_dirs)

    if args.determinism_check:
        return run_determinism_mode(adapter, case_dirs)

    return run_normal(adapter, case_dirs, regenerate=args.regenerate)
