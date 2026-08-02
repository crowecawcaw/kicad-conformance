"""The runner's core: run every check in a case, apply normalization/reduction, and
decide a verdict (DESIGN.md §3). This module has no argparse/printing concerns of its
own -- see runner/cli.py for the CLI and report formatting.
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
    `export-gerbers`/`export-drill` this is the whole scratch directory (golden-dir);
    for everything else it is a single file."""
    op = check.op
    if op in ("parse-sch", "parse-pcb", "upgrade"):
        return out_dir / Path(case.inputs[0]).name
    if op == "parse-sym":
        return out_dir / (Path(case.inputs[0]).stem + ".kicad_sym")
    if op == "parse-fp":
        return out_dir / "upgraded.pretty"
    if op == "erc":
        return out_dir / "erc.json"
    if op == "drc":
        return out_dir / "drc.json"
    if op == "netlist":
        return out_dir / "netlist.net"
    if op == "export-pos":
        return out_dir / "pos.csv"
    if op == "bom":
        return out_dir / "bom.csv"
    if op in ("export-gerbers", "export-drill"):
        return out_dir
    raise ValueError(f"no artifact resolver for op {op!r}")


def _reduce_structured(check: Check, artifact: Path) -> object:
    if check.op in ("drc", "erc"):
        with open(artifact, encoding="utf-8") as f:
            raw = json.load(f)
        return reduce.reduce_drc(raw) if check.op == "drc" else reduce.reduce_erc(raw)
    if check.op == "netlist":
        text = artifact.read_text(encoding="utf-8")
        return reduce.reduce_netlist(text)
    raise ValueError(f"no structured reduction for op {check.op!r}")


def _normalized_file_bytes(path: Path) -> bytes:
    return normalize.normalize_for(path, path.read_bytes())


def _normalized_dir_tree(dir_path: Path) -> dict[str, bytes]:
    tree = {}
    for f in sorted(dir_path.rglob("*")):
        if f.is_file():
            rel = f.relative_to(dir_path).as_posix()
            tree[rel] = normalize.normalize_for(f, f.read_bytes())
    return tree


def _write_golden_file(golden_path: Path, data: bytes) -> None:
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_path.write_bytes(data)


def _write_golden_dir(golden_dir: Path, tree: dict[str, bytes]) -> None:
    if golden_dir.exists():
        for f in golden_dir.rglob("*"):
            if f.is_file():
                f.unlink()
    golden_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in tree.items():
        dest = golden_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _exit_condition(
    check: Check, result, label: str
) -> tuple[bool, str, str]:
    """Apply the `exit` polarity/substring rule (DESIGN §3a). Returns
    (satisfied, status_if_not_satisfied, detail)."""
    verdict = classify(result.returncode)
    if check.expect == "ok":
        if verdict is Verdict.OK:
            return True, "", ""
        if verdict is Verdict.CRASH:
            return False, CRASH, f"{label}: adapter CRASHED (returncode={result.returncode}); a crash is never a pass"
        return False, FAIL, f"{label}: expected ok, got exit {result.returncode}\nstderr: {result.stderr.strip()}"
    # expect == "error"
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

        # Positive control (DESIGN §3a, DL-0013): every failure-case expect="error"
        # check must be shown falsifiable. Run it even when the main check already
        # failed/crashed -- that keeps the control machinery exercised (and visible in
        # the report) on every failure/ case, not just the ones whose main check
        # happens to reject cleanly. It can only make an already-failing check's report
        # richer; it never turns a CRASH/FAIL into a pass.
        control_note = ""
        control_ok = True  # vacuously true when there's no control to run (expect="ok")
        if check.expect == "error":
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

        if check.compare == "exit":
            return CheckResult(label, PASS)

        return self._compare_rich_output(case, check, label, out_dir)

    def _score_known_divergence(self, kd, satisfied: bool, result, label: str, control_note: str) -> Optional[CheckResult]:
        """Reinterpret an `expect`-vs-actual outcome already declared as a known,
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

    def _compare_rich_output(self, case: Case, check: Check, label: str, out_dir: Path) -> CheckResult:
        artifact = _resolve_artifact(check, case, out_dir)
        golden_root = case.golden_dir(self.version)
        golden_path = golden_root / check.golden

        if check.compare == "structured":
            try:
                actual = self._reduce_structured_safe(check, artifact)
            except FileNotFoundError:
                return CheckResult(label, FAIL, f"{label}: adapter did not write expected artifact at {artifact}")
            if self.regenerate:
                golden_path.parent.mkdir(parents=True, exist_ok=True)
                golden_path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                return CheckResult(label, REGENERATED, f"{label}: wrote {golden_path}")
            if not golden_path.exists():
                return CheckResult(label, NEEDS_REGEN, f"{label}: golden {golden_path} missing -- run --regenerate")
            expected = json.loads(golden_path.read_text(encoding="utf-8"))
            if actual == expected:
                return CheckResult(label, PASS)
            notes = reduce.describe_structured_mismatch(expected, actual)
            return CheckResult(label, FAIL, f"{label}: structured mismatch: {'; '.join(notes)}")

        if check.compare == "golden-file":
            if not artifact.exists():
                return CheckResult(label, FAIL, f"{label}: adapter did not write expected artifact at {artifact}")
            actual_bytes = _normalized_file_bytes(artifact)
            if self.regenerate:
                _write_golden_file(golden_path, actual_bytes)
                return CheckResult(label, REGENERATED, f"{label}: wrote {golden_path}")
            if not golden_path.exists():
                return CheckResult(label, NEEDS_REGEN, f"{label}: golden {golden_path} missing -- run --regenerate")
            expected_bytes = normalize.normalize_for(golden_path, golden_path.read_bytes())
            if actual_bytes == expected_bytes:
                return CheckResult(label, PASS)
            return CheckResult(label, FAIL, f"{label}: golden-file mismatch vs {golden_path}")

        if check.compare == "golden-dir":
            if not artifact.is_dir():
                return CheckResult(label, FAIL, f"{label}: adapter did not write expected directory at {artifact}")
            actual_tree = _normalized_dir_tree(artifact)
            if self.regenerate:
                _write_golden_dir(golden_path, actual_tree)
                return CheckResult(label, REGENERATED, f"{label}: wrote {golden_path}/ ({len(actual_tree)} files)")
            if not golden_path.exists():
                return CheckResult(label, NEEDS_REGEN, f"{label}: golden {golden_path} missing -- run --regenerate")
            expected_tree = _normalized_dir_tree(golden_path)
            if actual_tree == expected_tree:
                return CheckResult(label, PASS)
            missing = set(expected_tree) - set(actual_tree)
            extra = set(actual_tree) - set(expected_tree)
            differing = {
                k for k in (set(expected_tree) & set(actual_tree))
                if expected_tree[k] != actual_tree[k]
            }
            parts = []
            if missing:
                parts.append(f"missing files: {sorted(missing)}")
            if extra:
                parts.append(f"unexpected files: {sorted(extra)}")
            if differing:
                parts.append(f"differing files: {sorted(differing)}")
            return CheckResult(label, FAIL, f"{label}: golden-dir mismatch vs {golden_path}: {'; '.join(parts)}")

        raise ValueError(f"unhandled compare mode {check.compare!r}")

    def _reduce_structured_safe(self, check: Check, artifact: Path):
        if not artifact.exists():
            raise FileNotFoundError(artifact)
        return _reduce_structured(check, artifact)


def make_tmp_root() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="kicad-conformance-")
