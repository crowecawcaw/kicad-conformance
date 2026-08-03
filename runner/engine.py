"""The runner's core: run every check in a case, apply normalization/reduction, and
decide a verdict (DESIGN.md §3). This module has no argparse/printing concerns of its
own -- see runner/cli.py for the CLI and report formatting.

DL-0023/DL-0024: comparison mode is chosen by `op` alone, never by a `compare` field.
`model`/`drc`/`erc`/`netlist`/`pos`/`ipcd356`/`stats` compare a normalized JSON document
against `expected/<version>/<name>`; `render` compares normalized SVG bytes; every other
op (`parse-*`, `version`, `export-gerbers`, `export-drill`) is exit-polarity only. The
`golden-file`/`golden-dir` byte-comparison modes are deleted (DL-0024) along with the
s-expr/gerber/drill/bom normalizers and directory-tree comparator that only served them.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from runner import normalize, reduce
from runner.adapter import Adapter
from runner.manifest import Case, Check, load_case
from runner.verdict import Verdict, classify

# Statuses a single [[check]] can land on. Only PASS/XFAIL (and SKIP, which is excluded
# from both counts) count as non-failing for the exit code.
PASS = "PASS"
FAIL = "FAIL"
CRASH = "CRASH"
SKIP = "SKIP"
NEEDS_REGEN = "NEEDS-REGEN"
NOT_EVIDENCE = "NOT-EVIDENCE"
REGENERATED = "REGENERATED"
# DL-0018 -- strict xfail layer for a declared `known_divergence`. XFAIL is the expected,
# tracked bad verdict (never a build failure); XPASS is the oracle no longer reproducing
# the declared divergence (always a build failure -- the ledger must be updated, not
# silently left stale).
XFAIL = "XFAIL"
XPASS = "XPASS"

_FAILING_STATUSES = {FAIL, CRASH, NEEDS_REGEN, NOT_EVIDENCE, XPASS}


@dataclass
class CheckResult:
    label: str
    status: str
    detail: str = ""


@dataclass
class CaseResult:
    case: Case
    check_results: list[CheckResult] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def ok(self) -> bool:
        if self.skipped:
            return True
        return all(r.status not in _FAILING_STATUSES for r in self.check_results)


def _resolve_artifact(check: Check, case: Case, out_dir: Path) -> Path:
    """Where the adapter wrote the output the runner dictated (DESIGN §2a). For
    `export-gerbers`/`export-drill` this is the whole scratch directory (exit-only, no
    comparator reads it -- DL-0024); for everything else it is a single file."""
    op = check.op
    input_path = Path(case.inputs[0])
    if op in ("parse-sch", "parse-pcb"):
        return out_dir / input_path.name
    if op == "parse-sym":
        return out_dir / (input_path.stem + ".kicad_sym")
    if op == "parse-fp":
        return out_dir / "upgraded.pretty"
    if op == "erc":
        return out_dir / "erc.json"
    if op == "drc":
        return out_dir / "drc.json"
    if op == "netlist":
        return out_dir / "netlist.net"
    if op == "pos":
        return out_dir / "pos.csv"
    if op == "stats":
        return out_dir / "stats.json"
    if op == "ipcd356":
        return out_dir / "board.d356"
    if op == "model":
        return out_dir / "model.json"
    if op == "render":
        if input_path.suffix == ".kicad_pcb":
            return out_dir / "render.svg"
        # sch/sym/fp: kicad-cli writes its OWN derived filename into the `--out` dir
        # (`sch|sym|fp export svg -o <out>/` -> `<out>/<input-stem>.svg`), unlike a
        # board render where the adapter dictates the exact file name (VALIDATION §6).
        return out_dir / (input_path.stem + ".svg")
    if op in ("export-gerbers", "export-drill"):
        return out_dir
    raise ValueError(f"no artifact resolver for op {op!r}")


# Reduction for each JSON-comparison op (VALIDATION.md §3/§4/§5): given the check and the
# artifact path the adapter wrote, return the canonical, JSON-serializable structure to
# compare against the expected file. `model` needs no further reduction -- the adapter
# already wrote the fully-composed, merged document (DESIGN §2's "composition happens in
# the adapter").
def _reduce_json(check: Check, artifact: Path) -> object:
    op = check.op
    if op == "model":
        return json.loads(artifact.read_text(encoding="utf-8"))
    if op in ("drc", "erc"):
        raw = json.loads(artifact.read_text(encoding="utf-8"))
        return reduce.reduce_drc(raw) if op == "drc" else reduce.reduce_erc(raw)
    if op == "netlist":
        text = artifact.read_text(encoding="utf-8")
        if check.format == "kicadxml":
            return reduce.reduce_netlist_kicadxml(text)
        return reduce.reduce_netlist(text)
    if op == "stats":
        raw = json.loads(artifact.read_text(encoding="utf-8"))
        return reduce.reduce_stats(raw)
    if op == "pos":
        return reduce.reduce_pos(artifact.read_text(encoding="utf-8"))
    if op == "ipcd356":
        return reduce.reduce_ipcd356(artifact.read_text(encoding="utf-8"))
    raise ValueError(f"no JSON reduction for op {op!r}")


JSON_OPS = {"model", "drc", "erc", "netlist", "pos", "ipcd356", "stats"}


def _exit_condition(
    check: Check, result, label: str
) -> tuple[bool, str, str]:
    """Apply the `exit` polarity/substring rule (DESIGN §3a). Returns
    (satisfied, status_if_not_satisfied, detail)."""
    verdict = classify(result.returncode)
    if check.outcome == "ok":
        if verdict is Verdict.OK:
            return True, "", ""
        if verdict is Verdict.CRASH:
            return False, CRASH, f"{label}: adapter CRASHED (returncode={result.returncode}); a crash is never a pass"
        return False, FAIL, f"{label}: expected ok, got exit {result.returncode}\nstderr: {result.stderr.strip()}"
    # outcome == "error"
    if verdict is Verdict.OK:
        return False, FAIL, f"{label}: expected error, tool exited 0"
    if verdict is Verdict.CRASH:
        return False, CRASH, (
            f"{label}: adapter CRASHED (returncode={result.returncode}) instead of a "
            f"graceful rejection -- CRASH is never a pass, even for a failure/ case "
            f"(DL-0013). stderr: {result.stderr.strip()}"
        )
    # REJECT: check substring assertions
    stderr = result.stderr
    if check.error_contains and check.error_contains not in stderr:
        return False, FAIL, f"{label}: stderr did not contain {check.error_contains!r}\nstderr: {stderr.strip()}"
    if check.error_contains_any and not any(s in stderr for s in check.error_contains_any):
        return False, FAIL, f"{label}: stderr did not contain any of {check.error_contains_any!r}\nstderr: {stderr.strip()}"
    return True, "", ""


class Engine:
    def __init__(self, adapter: Adapter, regenerate: bool = False):
        self.adapter = adapter
        self.regenerate = regenerate
        self._version: Optional[str] = None

    @property
    def version(self) -> str:
        if self._version is None:
            self._version = self.adapter.version()
        return self._version

    def run_case(self, case_dir: Path, tmp_root: Path) -> CaseResult:
        case = load_case(case_dir)

        if case.skip_reason:
            return CaseResult(case=case, skipped=True, skip_reason=case.skip_reason)

        results: list[CheckResult] = []
        any_ran = False
        for i, check in enumerate(case.checks):
            label = check.label(i)
            if not self.adapter.supports(check.op):
                results.append(CheckResult(label, SKIP, f"adapter does not support verb {check.op!r}"))
                continue
            any_ran = True
            results.append(self._run_check(case, check, label, tmp_root))

        if not any_ran:
            return CaseResult(case=case, skipped=True, skip_reason="no check's verb is supported by this adapter")
        return CaseResult(case=case, check_results=results)

    def _run_check(self, case: Case, check: Check, label: str, tmp_root: Path) -> CheckResult:
        out_dir = tmp_root / f"{label.replace('/', '_')}_main"
        inputs = [case.path / p for p in case.inputs]
        result = self.adapter.invoke(
            check.op, inputs, out_dir, root=case.root, fmt=check.format, extra_args=check.args
        )

        satisfied, fail_status, detail = _exit_condition(check, result, label)

        # Positive control (DESIGN §3a, DL-0013): every failure-case outcome="error"
        # check must be shown falsifiable. Run it even when the main check already
        # failed/crashed -- that keeps the control machinery exercised (and visible in
        # the report) on every failure/ case, not just the ones whose main check
        # happens to reject cleanly. It can only make an already-failing check's report
        # richer; it never turns a CRASH/FAIL into a pass.
        control_note = ""
        control_ok = True  # vacuously true when there's no control to run (outcome="ok")
        if check.outcome == "error":
            control_ok, control_detail = self._check_control(case, check, label, tmp_root)
            control_note = control_detail
            if satisfied and not control_ok:
                return CheckResult(label, NOT_EVIDENCE, control_detail)

        # Known-oracle-divergence strict xfail (DL-0018) -- a layer on top of the plain
        # OK/REJECT/CRASH verdict, never a replacement for it. Only considered once the
        # positive control (if any) has already vouched for the check being evidence --
        # a NOT-EVIDENCE case above takes precedence over either xfail or xpass.
        kd = check.known_divergence if check.known_divergence is not None else case.known_divergence
        if kd is not None and control_ok:
            xfail_xpass = self._score_known_divergence(kd, satisfied, result, label, control_note)
            if xfail_xpass is not None:
                return xfail_xpass

        if not satisfied:
            if control_note:
                detail = f"{detail}\n{control_note}"
            return CheckResult(label, fail_status, detail)

        if check.expected is None:
            return CheckResult(label, PASS)

        if check.op in JSON_OPS:
            return self._compare_json(case, check, label, out_dir)
        if check.op == "render":
            return self._compare_render(case, check, label, out_dir)
        raise ValueError(f"check op {check.op!r} has `expected` set but no comparison is defined")

    def _score_known_divergence(self, kd, satisfied: bool, result, label: str, control_note: str) -> Optional[CheckResult]:
        """Reinterpret an `outcome`-vs-actual outcome already declared as a known,
        tracked oracle divergence (DL-0018). Returns `None` when the divergence
        declaration doesn't apply to this outcome (e.g. a genuine unrelated failure),
        in which case the caller falls through to ordinary FAIL/CRASH reporting."""
        if satisfied:
            # The check reached its normally-desired outcome (clean OK/REJECT) despite a
            # declared divergence -- the oracle no longer reproduces the known bug. That
            # is progress, but a *silent* pass here would let the ledger rot, so it FAILS
            # the build until a human retires the marker.
            detail = (
                f"{label}: XPASS -- known divergence ({kd.kind}) no longer reproduces: "
                f"the adapter returned a clean/expected result instead of the declared "
                f"{kd.kind}. Update docs/DIVERGENCES.md and remove the `known_divergence` "
                f"marker from case.toml.\n  declared reason: {kd.reason}"
            )
            if kd.tracking:
                detail += f"\n  tracking: {kd.tracking}"
            return CheckResult(label, XPASS, detail)

        verdict = classify(result.returncode)
        if kd.kind == "crash" and verdict is Verdict.CRASH:
            detail = f"{label}: XFAIL (known divergence, kind={kd.kind}) -- {kd.reason}"
            if kd.tracking:
                detail += f" [tracking: {kd.tracking}]"
            if control_note:
                detail += f"\n{control_note}"
            return CheckResult(label, XFAIL, detail)

        # Bad, but not the *declared* kind of bad -- report it as a normal failure so a
        # different, undeclared regression is never laundered through the divergence
        # marker.
        return None

    def _check_control(self, case: Case, check: Check, label: str, tmp_root: Path) -> tuple[bool, str]:
        """Returns (control_reached_ok, human-readable note) -- never raises, always
        runs, regardless of the main check's own outcome (see call site above)."""
        control_spec = check.control if check.control is not None else case.control
        if control_spec is None:
            return False, (
                f"{label}: failure case has no positive control (`control =`); "
                f"\"a test that can't fail is not evidence\" (DL-0013)"
            )
        if not isinstance(control_spec, str):
            return False, (
                f"{label}: table-form (inline patch) `control` is not implemented by "
                f"this runner yet -- only a sibling-fixture path is supported"
            )
        control_path = case.path / control_spec
        if not control_path.exists():
            return False, f"{label}: control fixture {control_spec!r} does not exist"

        control_out = tmp_root / f"{label.replace('/', '_')}_control"
        control_result = self.adapter.invoke(
            check.op, [control_path], control_out, fmt=check.format, extra_args=check.args
        )
        verdict = classify(control_result.returncode)
        if verdict is not Verdict.OK:
            return False, (
                f"{label}: positive control {control_spec!r} did not exit OK "
                f"(verdict={verdict.value}, returncode={control_result.returncode}) -- "
                f"the defect-free variant must succeed, or the failure isn't evidence "
                f"of the specific defect (DL-0013)\nstderr: {control_result.stderr.strip()}"
            )
        return True, f"{label}: positive control {control_spec!r} exited OK, as required (DL-0013)"

    def _compare_json(self, case: Case, check: Check, label: str, out_dir: Path) -> CheckResult:
        artifact = _resolve_artifact(check, case, out_dir)
        expected_root = case.expected_dir(self.version)
        expected_path = expected_root / check.expected

        if not artifact.exists():
            return CheckResult(label, FAIL, f"{label}: adapter did not write expected artifact at {artifact}")
        actual = _reduce_json(check, artifact)

        if self.regenerate:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return CheckResult(label, REGENERATED, f"{label}: wrote {expected_path}")
        if not expected_path.exists():
            return CheckResult(label, NEEDS_REGEN, f"{label}: expected file {expected_path} missing -- run --regenerate")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        if actual == expected:
            return CheckResult(label, PASS)
        notes = reduce.describe_structured_mismatch(expected, actual)
        return CheckResult(label, FAIL, f"{label}: mismatch vs {expected_path}: {'; '.join(notes)}")

    def _compare_render(self, case: Case, check: Check, label: str, out_dir: Path) -> CheckResult:
        # render (VALIDATION.md §6): KiCad-vs-KiCad is a normalized-SVG BYTE-EXACT
        # compare, zero tolerance, no rasterizer -- the cross-impl raster path (pinned
        # `resvg`) is deferred to M6 (DL-0021).
        artifact = _resolve_artifact(check, case, out_dir)
        expected_root = case.expected_dir(self.version)
        expected_path = expected_root / check.expected

        if not artifact.exists():
            return CheckResult(label, FAIL, f"{label}: adapter did not write expected SVG at {artifact}")
        actual_bytes = normalize.normalize_svg(artifact.read_bytes())

        if self.regenerate:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_bytes(actual_bytes)
            return CheckResult(label, REGENERATED, f"{label}: wrote {expected_path}")
        if not expected_path.exists():
            return CheckResult(label, NEEDS_REGEN, f"{label}: expected file {expected_path} missing -- run --regenerate")
        expected_bytes = normalize.normalize_svg(expected_path.read_bytes())
        if actual_bytes == expected_bytes:
            return CheckResult(label, PASS)
        return CheckResult(label, FAIL, f"{label}: render (SVG) mismatch vs {expected_path} (normalized-SVG byte compare)")


def make_tmp_root() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="kicad-conformance-")
