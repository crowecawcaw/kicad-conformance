"""`python -m runner` -- see docs/ROADMAP.md M0 and docs/DESIGN.md for what this
implements. Flags are kept minimal (per the M0 task): PATHS, --regenerate, --adapter,
--coverage-proxy, --determinism-check.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from runner.adapter import Adapter
from runner.coverage import build_coverage_report
from runner.determinism import check_determinism
from runner.engine import Engine, FAIL, PASS, REGENERATED, SKIP, XFAIL, make_tmp_root
from runner.manifest import CaseError, discover_cases, load_case


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
        "--coverage-proxy", action="store_true",
        help="print the full CLI-surface + format-token coverage report (DESIGN §7a); "
             "a one-line summary is always printed regardless of this flag",
    )
    p.add_argument(
        "--determinism-check", action="store_true",
        help="instead of a normal run, run every rich-output check twice per case and "
             "assert the normalized/reduced result is identical both times (§4a)",
    )
    return p


def _print_case_header(case_dir: Path) -> None:
    print(f"\n{case_dir.as_posix()}")


def run_normal(adapter: Adapter, case_dirs: list[Path], regenerate: bool, show_coverage: bool) -> int:
    engine = Engine(adapter, regenerate=regenerate)
    print(f"oracle: {adapter.identity().splitlines()[0] if adapter.identity() != 'unknown' else 'unknown'}")
    print(f"adapter version verb reports: {engine.version}")

    counts: dict[str, int] = {}
    failing_cases = 0
    skipped_cases = 0
    total_cases = 0
    loaded_cases = []

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
            loaded_cases.append(result.case)
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

    # DESIGN §7a: a one-line coverage-proxy summary is always printed; --coverage-proxy
    # expands it to the full CLI-surface + format-token report.
    report = build_coverage_report(loaded_cases)
    if show_coverage:
        print("\n" + report.render())
    else:
        total_verbs = len(report.exercised_verbs) + len(report.unexercised_verbs())
        print(
            f"\ncoverage proxy: {len(report.exercised_verbs)}/{total_verbs} verbs exercised "
            f"(pass --coverage-proxy for the full report)"
        )

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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    adapter = Adapter(Path(args.adapter)) if args.adapter else Adapter()
    roots = [Path(p) for p in args.paths]
    case_dirs = discover_cases(roots)
    if not case_dirs:
        print(f"No case.toml found under {[str(r) for r in roots]}", file=sys.stderr)
        return 1

    if args.determinism_check:
        return run_determinism_mode(adapter, case_dirs)

    return run_normal(adapter, case_dirs, regenerate=args.regenerate, show_coverage=args.coverage_proxy)
