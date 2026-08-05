"""The runner's core.

A happy case (no `control`) records the fixed set of answers its input's file suffix
implies, plus any `extra`, and compares each to `expected/<version>/`. A rejection case
(sets `control`) runs the type's loader and checks the exit/stderr/control contract.
Comparison follows from each answer's own `kind`, never from a manifest field.
"""
from __future__ import annotations

import difflib
import enum
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from runner import normalize
from runner.adapter import Adapter
from runner.manifest import EXTRA_NAMES, Case, load_case


class Verdict(enum.Enum):
    """OK / REJECT / CRASH. A malformed input can make the oracle crash rather than
    reject cleanly, and a naive "non-zero means rejected" rule would score that as a
    pass. A CRASH is never a pass -- not for a happy case, not for a rejection case.

    The runner's direct child is the *adapter*, not kicad-cli, so the adapter re-raises
    any signal that killed kicad-cli against itself (see adapters/kicad.py) -- that is
    what keeps this classifier meaningful through the adapter indirection.
    """

    OK = "OK"
    REJECT = "REJECT"
    CRASH = "CRASH"


def classify(returncode: int) -> Verdict:
    """Portable, never hard-coding a literal signal number: `subprocess` reports a
    negative returncode for a signaled child, and anything above 128 is the shell's
    128+signal convention."""
    if returncode == 0:
        return Verdict.OK
    if returncode < 0 or returncode > 128:
        return Verdict.CRASH
    return Verdict.REJECT


# Statuses one answer/check can land on. PASS/XFAIL/SKIP/REGENERATED do not fail a run.
PASS = "PASS"
FAIL = "FAIL"
CRASH = "CRASH"
SKIP = "SKIP"
NEEDS_REGEN = "NEEDS-REGEN"
NOT_EVIDENCE = "NOT-EVIDENCE"
REGENERATED = "REGENERATED"
# `xfail = "DIV-NNNN"`: XFAIL is the declared, tracked bad verdict; XPASS means the
# divergence no longer reproduces, which fails the build so the ledger cannot rot.
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


# --- The recorded answers -----------------------------------------------------


@dataclass
class Answer:
    """One recorded answer: which adapter verb produces it, where the adapter writes the
    artifact, how it is compared, and its name under `expected/<version>/`."""

    name: str
    verb: str
    expected_name: str  # file or directory name under expected/<version>/
    kind: str  # "file" | "dir" | "invariant"
    artifact: Callable[[Path, Path], Path]  # (out_dir, input_path) -> artifact path


def input_kind(input_path: Path) -> str:
    """`board` / `sch` / `sym` / `fp`, from the input's suffix. A footprint library is
    either a `.pretty` directory or a lone `.kicad_mod` file."""
    suffix = input_path.suffix
    if suffix == ".kicad_pcb":
        return "board"
    if suffix == ".kicad_sch":
        return "sch"
    if suffix == ".kicad_sym":
        return "sym"
    if suffix in (".kicad_mod", ".pretty"):
        return "fp"
    raise ValueError(f"unrecognized input type: {input_path} (suffix {suffix!r})")


# The loader verb a rejection case's input type runs. There is no pure "parse and stop"
# subcommand, so each maps to a real kicad-cli subcommand that loads the file; exit
# polarity only. `board` maps to `pcb export stats`, not `pcb upgrade --force` -- the
# latter SIGSEGVs on every malformed board, while `export stats` rejects the same bytes
# gracefully and still requires a fully-parsed board on the accept path.
LOADER_VERB = {
    "board": "parse-pcb",
    "sch": "parse-sch",
    "sym": "parse-sym",
    "fp": "parse-fp",
}


def battery_for(kind: str) -> list[Answer]:
    """The standard answers for one input kind. No per-case opt-out: every happy case of
    a given type records the same set."""
    if kind == "board":
        return [
            Answer("stats", "stats", "stats.json", "file", lambda out, inp: out / "stats.json"),
            Answer("pos", "pos", "pos.csv", "file", lambda out, inp: out / "pos.csv"),
            Answer("ipcd356", "ipcd356", "ipcd356.d356", "file", lambda out, inp: out / "ipcd356.d356"),
            Answer("render", "render", "render-F_Cu.svg", "file", lambda out, inp: out / "render.svg"),
            Answer("gerbers", "export-gerbers", "gerbers", "dir", lambda out, inp: out),
            Answer("drill", "export-drill", "drill", "dir", lambda out, inp: out),
        ]
    if kind == "sch":
        return [
            Answer("netlist", "netlist", "netlist.net", "file", lambda out, inp: out / "netlist.net"),
            Answer("render", "render", "render.svg", "file", lambda out, inp: out / (inp.stem + ".svg")),
        ]
    if kind in ("sym", "fp"):
        # Libraries get drawings only -- kicad-cli 10.0.5 has no structured symbol or
        # footprint export. `render` writes one SVG per symbol-unit / footprint under
        # KiCad's own names.
        return [Answer("render", "render", "render", "dir", lambda out, inp: out)]
    raise ValueError(f"no standard battery for input kind {kind!r}")


def answer_for_extra(name: str) -> Answer:
    if name == "drc":
        return Answer("drc", "drc", "drc.json", "file", lambda out, inp: out / "drc.json")
    if name == "erc":
        return Answer("erc", "erc", "erc.json", "file", lambda out, inp: out / "erc.json")
    if name == "roundtrip":
        # Round-trip write-path testing: the adapter exports the fixture, re-serializes a
        # copy with `<kind> upgrade --force`, and exports that too, writing both sets side
        # by side. Nothing is recorded under expected/ -- the two halves are asserted
        # equal to each other, so a KiCad version bump changes nothing here.
        return Answer("roundtrip", "roundtrip", "", "invariant", lambda out, inp: out)
    raise ValueError(f"unknown extra {name!r}")


assert set() == EXTRA_NAMES - {"drc", "erc", "roundtrip"}, \
    "answer_for_extra must handle every name in manifest.EXTRA_NAMES"


def answers_for_case(case: Case) -> list[Answer]:
    answers = list(battery_for(input_kind(case.input_paths[0])))
    answers.extend(answer_for_extra(name) for name in case.extra)
    return answers


def answers_in_assertion_order(case: Case) -> list[Answer]:
    """Generation order for a perturbation run: the case's `extra` answers first (the
    case exists because of them), then the standard battery."""
    extras = [answer_for_extra(name) for name in case.extra]
    return extras + list(battery_for(input_kind(case.input_paths[0])))


# Answers whose byte compare is a KiCad self-consistency signal rather than a
# cross-implementation semantic one: in ecosystem mode these report INFO, never FAIL.
BYTE_ONLY_ANSWERS = frozenset({"gerbers", "drill"})


# --- Snapshots and comparison ------------------------------------------------


def _dir_files(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*")) if p.is_file()}


def dir_snapshot(root: Path, *, normalized: bool) -> dict[str, bytes]:
    """Every file under `root`, keyed by its path relative to `root`."""
    out = {}
    for rel, p in _dir_files(root).items():
        data = p.read_bytes()
        if normalized:
            data = normalize.normalize_for(p, data)
        out[rel] = data
    return out


def raw_snapshot(answer: Answer, out_dir: Path, input_path: Path):
    """Pre-normalization content, in the shape `normalized_snapshot` would consume."""
    artifact = answer.artifact(out_dir, input_path)
    if answer.kind == "file":
        if not artifact.exists():
            raise FileNotFoundError(f"{answer.name}: no artifact written at {artifact}")
        return artifact.read_bytes()
    if answer.kind in ("dir", "invariant"):
        return dir_snapshot(artifact, normalized=False)
    raise ValueError(f"no raw snapshot for answer kind {answer.kind!r}")


def normalized_snapshot(answer: Answer, out_dir: Path, input_path: Path):
    artifact = answer.artifact(out_dir, input_path)
    if answer.kind == "file":
        return normalize.normalize_for(artifact, artifact.read_bytes())
    if answer.kind in ("dir", "invariant"):
        return dir_snapshot(artifact, normalized=True)
    raise ValueError(f"no normalized snapshot for answer kind {answer.kind!r}")


def expected_normalized(case: Case, answer: Answer, version: str):
    """The committed answer, read and normalized the same way a fresh run's artifact is.
    Read-only: a perturbation run must never write to `expected/`."""
    expected_path = case.expected_dir(version) / answer.expected_name
    if not expected_path.exists():
        raise FileNotFoundError(f"{answer.name}: nothing committed at {expected_path}")
    if answer.kind == "file":
        return normalize.normalize_for(expected_path, expected_path.read_bytes())
    if answer.kind == "dir":
        return dir_snapshot(expected_path, normalized=True)
    raise ValueError(
        f"{answer.name}: kind={answer.kind!r} answers have no expected/ file -- callers "
        f"must skip this kind"
    )


# --- `--verify-assertions` support ------------------------------------------


@dataclass
class AnswerGenOutcome:
    """One answer's outcome while generating against a (possibly overlaid) input set.
    `verdict` stands in for "did the perturbed input still load"; `differs` is only
    meaningful when the verdict is OK and a comparison actually ran."""

    name: str
    verdict: Verdict
    differs: Optional[bool] = None
    detail: str = ""


def generate_and_compare_against_committed(
    engine: "Engine", case: Case, answers: list[Answer], input_paths: list[Path],
    tmp_root: Path, *, label_prefix: str,
) -> list[AnswerGenOutcome]:
    """Run `answers` against `input_paths` and compare each to the case's *committed*
    answers -- never regenerating, regardless of `engine.regenerate`. Stops at the first
    answer that does not exit OK or that differs from committed."""
    outcomes: list[AnswerGenOutcome] = []
    input_path = input_paths[0]
    for answer in answers:
        if not engine.adapter.supports(answer.verb):
            continue
        if answer.kind == "invariant":
            # `roundtrip` has no committed answer to compare a perturbation against: it
            # asserts two halves of one run agree with each other, which a perturbed input
            # does not change the shape of. Its falsifiability comes from elsewhere (it is
            # already known to fail on real writer defects).
            continue
        out_dir = tmp_root / f"{label_prefix}_{answer.name}"
        result = engine.adapter.invoke(answer.verb, input_paths, out_dir)
        verdict = classify(result.returncode)
        if verdict is not Verdict.OK:
            outcomes.append(AnswerGenOutcome(answer.name, verdict, detail=result.stderr.strip()))
            return outcomes
        try:
            actual = normalized_snapshot(answer, out_dir, input_path)
            expected = expected_normalized(case, answer, engine.version)
        except (FileNotFoundError, ValueError, OSError) as e:
            outcomes.append(AnswerGenOutcome(answer.name, verdict, detail=f"could not compare: {e}"))
            return outcomes
        differs = actual != expected
        outcomes.append(AnswerGenOutcome(answer.name, verdict, differs=differs))
        if differs:
            return outcomes
    return outcomes


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
        if case.polarity == "failure":
            return self._run_failure_case(case, tmp_root)
        return self._run_happy_case(case, tmp_root)

    # --- happy case ------------------------------------------------------------

    def _run_happy_case(self, case: Case, tmp_root: Path) -> CaseResult:
        # Every file in the case directory goes to the adapter, entry file first: a
        # multi-sheet schematic's sub-sheets and a board's `.kicad_dru`/`.kicad_pro`
        # siblings must all reach the scratch dir under their original names, or
        # kicad-cli cannot resolve them.
        input_paths = case.input_paths
        input_path = input_paths[0]

        results: list[CheckResult] = []
        for answer in answers_for_case(case):
            label = answer.name
            if not self.adapter.supports(answer.verb):
                results.append(CheckResult(label, SKIP, f"adapter does not support verb {answer.verb!r}"))
                continue

            out_dir = tmp_root / f"{label}_main"
            result = self.adapter.invoke(answer.verb, input_paths, out_dir)
            verdict = classify(result.returncode)
            if verdict is not Verdict.OK:
                results.append(CheckResult(
                    label, CRASH if verdict is Verdict.CRASH else FAIL,
                    f"{label}: adapter did not exit OK (returncode={result.returncode})\n"
                    f"stderr: {result.stderr.strip()}",
                ))
                continue

            if answer.kind == "file":
                check = self._compare_file(case, answer, label, out_dir, input_path)
            elif answer.kind == "dir":
                check = self._compare_dir(case, answer, label, out_dir, input_path)
            elif answer.kind == "invariant":
                check = self._compare_invariant(answer, label, out_dir, input_path)
            else:
                raise ValueError(f"answer kind {answer.kind!r} has no comparator")
            results.append(self._apply_xfail(case, label, check))

        if not results:
            return CaseResult(case=case, skipped=True, skip_reason="no answer's verb is supported by this adapter")
        return CaseResult(case=case, check_results=results)

    def _compare_file(self, case: Case, answer: Answer, label: str, out_dir: Path, input_path: Path) -> CheckResult:
        artifact = answer.artifact(out_dir, input_path)
        expected_path = case.expected_dir(self.version) / answer.expected_name

        if not artifact.exists():
            return CheckResult(label, FAIL, f"{label}: adapter did not write expected artifact at {artifact}")
        actual = normalize.normalize_for(artifact, artifact.read_bytes())

        if self.regenerate:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_bytes(actual)
            return CheckResult(label, REGENERATED, f"{label}: wrote {expected_path}")
        if not expected_path.exists():
            return CheckResult(label, NEEDS_REGEN, f"{label}: expected file {expected_path} missing -- run --regenerate")
        expected = normalize.normalize_for(expected_path, expected_path.read_bytes())
        if actual == expected:
            return CheckResult(label, PASS)
        return CheckResult(label, FAIL, f"{label}: mismatch vs {expected_path}\n{_text_diff(expected, actual)}")

    def _compare_dir(self, case: Case, answer: Answer, label: str, out_dir: Path, input_path: Path) -> CheckResult:
        # A directory answer is compared as a whole: the same filenames must be present
        # and every file byte-identical after normalization. A missing file is a failure,
        # never a skip.
        artifact_dir = answer.artifact(out_dir, input_path)
        expected_dir = case.expected_dir(self.version) / answer.expected_name

        actual_files = _dir_files(artifact_dir)
        if not actual_files:
            return CheckResult(label, FAIL, f"{label}: adapter wrote no files at {artifact_dir}")

        if self.regenerate:
            if expected_dir.exists():
                shutil.rmtree(expected_dir)
            expected_dir.mkdir(parents=True, exist_ok=True)
            for rel, p in actual_files.items():
                dest = expected_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(normalize.normalize_for(p, p.read_bytes()))
            return CheckResult(label, REGENERATED, f"{label}: wrote {len(actual_files)} file(s) to {expected_dir}")

        if not expected_dir.exists() or not any(expected_dir.iterdir()):
            return CheckResult(label, NEEDS_REGEN, f"{label}: expected dir {expected_dir} missing -- run --regenerate")

        expected_files = _dir_files(expected_dir)
        missing = set(expected_files) - set(actual_files)
        extra = set(actual_files) - set(expected_files)
        if missing or extra:
            detail = f"{label}: file set mismatch vs {expected_dir}"
            if missing:
                detail += f"\n  missing (expected but not produced): {sorted(missing)}"
            if extra:
                detail += f"\n  unexpected (produced but not expected): {sorted(extra)}"
            return CheckResult(label, FAIL, detail)

        mismatches = [
            rel for rel in sorted(actual_files)
            if normalize.normalize_for(actual_files[rel], actual_files[rel].read_bytes())
            != normalize.normalize_for(expected_files[rel], expected_files[rel].read_bytes())
        ]
        if mismatches:
            return CheckResult(label, FAIL, f"{label}: {len(mismatches)} file(s) differ vs {expected_dir}: {mismatches}")
        return CheckResult(label, PASS)

    def _compare_invariant(self, answer: Answer, label: str, out_dir: Path, input_path: Path) -> CheckResult:
        # `roundtrip`: the one answer kind that never reads or writes `expected/`. The
        # adapter has already exported both halves; this asserts they mean the same thing.
        # `--regenerate` does not apply -- there is nothing to record.
        original = dir_snapshot(out_dir / "original", normalized=True)
        roundtripped = dir_snapshot(out_dir / "roundtripped", normalized=True)
        if not original or not roundtripped:
            return CheckResult(
                label, FAIL,
                f"{label}: adapter did not write both halves under {out_dir} "
                f"(original: {len(original)} file(s), roundtripped: {len(roundtripped)})",
            )
        if original == roundtripped:
            return CheckResult(label, PASS)
        moved = sorted(
            set(original) ^ set(roundtripped)
            | {k for k in set(original) & set(roundtripped) if original[k] != roundtripped[k]}
        )
        return CheckResult(
            label, FAIL,
            f"{label}: re-serializing the fixture and re-exporting it no longer means the "
            f"same thing as the original -- the writer lost or changed information the "
            f"reader accepted. Differing exports: {moved}",
        )

    def _apply_xfail(self, case: Case, label: str, result: CheckResult) -> CheckResult:
        """`xfail` on a happy case marks its `roundtrip` answer as a declared, tracked
        oracle divergence. Every other answer on the case is scored as-is, so a
        divergence can never launder a genuine mismatch elsewhere."""
        if case.xfail is None or label != "roundtrip":
            return result
        if result.status == PASS:
            return CheckResult(label, XPASS, (
                f"{label}: XPASS -- declared divergence {case.xfail} no longer reproduces. "
                f"Update the divergence ledger and remove `xfail` from case.toml."
            ))
        if result.status in (FAIL, CRASH):
            return CheckResult(label, XFAIL, f"{label}: XFAIL ({case.xfail})\n{result.detail}")
        return result

    # --- rejection case ---------------------------------------------------------

    def _run_failure_case(self, case: Case, tmp_root: Path) -> CaseResult:
        input_paths = case.input_paths
        verb = LOADER_VERB[input_kind(input_paths[0])]
        label = verb

        if not self.adapter.supports(verb):
            return CaseResult(case=case, skipped=True, skip_reason=f"adapter does not support verb {verb!r}")

        result = self.adapter.invoke(verb, input_paths, tmp_root / "main")
        satisfied, fail_status, detail = self._exit_condition(case, result, label)

        # Positive control: every rejection case must be shown falsifiable. Run it even
        # when the main check already failed, so the control is exercised (and visible in
        # the report) on every rejection case, not only the ones that reject cleanly.
        control_ok, control_note = self._check_control(case, verb, label, tmp_root)
        if satisfied and not control_ok:
            return CaseResult(case=case, check_results=[CheckResult(label, NOT_EVIDENCE, control_note)])

        if case.xfail is not None and control_ok:
            scored = self._score_xfail(case, satisfied, result, label, control_note)
            if scored is not None:
                return CaseResult(case=case, check_results=[scored])

        if not satisfied:
            if control_note:
                detail = f"{detail}\n{control_note}"
            return CaseResult(case=case, check_results=[CheckResult(label, fail_status, detail)])

        return CaseResult(case=case, check_results=[CheckResult(label, PASS, control_note)])

    def _exit_condition(self, case: Case, result, label: str) -> tuple[bool, str, str]:
        """The tool must reject -- a graceful, non-crashing non-zero exit -- and, if
        declared, stderr must contain the asserted substring."""
        verdict = classify(result.returncode)
        if verdict is Verdict.OK:
            return False, FAIL, f"{label}: expected error, tool exited 0"
        if verdict is Verdict.CRASH:
            return False, CRASH, (
                f"{label}: adapter CRASHED (returncode={result.returncode}) instead of a "
                f"graceful rejection -- a crash is never a pass, even for a rejection "
                f"case. stderr: {result.stderr.strip()}"
            )
        if case.error_contains and case.error_contains not in result.stderr:
            return False, FAIL, (
                f"{label}: stderr did not contain {case.error_contains!r}\n"
                f"stderr: {result.stderr.strip()}"
            )
        return True, "", ""

    def _score_xfail(self, case: Case, satisfied: bool, result, label: str, control_note: str) -> Optional[CheckResult]:
        """On a rejection case, `xfail` declares that the oracle crashes instead of
        rejecting cleanly. Returns `None` when the declaration does not apply, so an
        undeclared regression is never laundered through the marker."""
        if satisfied:
            return CheckResult(label, XPASS, (
                f"{label}: XPASS -- declared divergence {case.xfail} no longer reproduces: "
                f"the oracle rejected cleanly. Update the divergence ledger and remove "
                f"`xfail` from case.toml."
            ))
        if classify(result.returncode) is Verdict.CRASH:
            detail = f"{label}: XFAIL ({case.xfail}) -- oracle crashed instead of rejecting cleanly"
            if control_note:
                detail += f"\n{control_note}"
            return CheckResult(label, XFAIL, detail)
        return None

    def _check_control(self, case: Case, verb: str, label: str, tmp_root: Path) -> tuple[bool, str]:
        """Returns (control_reached_ok, note) -- never raises, always runs."""
        if case.control is None:
            return False, f"{label}: rejection case has no positive control (`control =`)"
        control_path = case.path / case.control
        if not control_path.exists():
            return False, f"{label}: control fixture {case.control!r} does not exist"

        control_result = self.adapter.invoke(verb, [control_path], tmp_root / "control")
        verdict = classify(control_result.returncode)
        if verdict is not Verdict.OK:
            return False, (
                f"{label}: positive control {case.control!r} did not exit OK "
                f"(verdict={verdict.value}, returncode={control_result.returncode}) -- the "
                f"defect-free variant must succeed, or the failure isn't evidence of the "
                f"specific defect\nstderr: {control_result.stderr.strip()}"
            )
        return True, f"{label}: positive control {case.control!r} exited OK, as required"


def _text_diff(expected: bytes, actual: bytes, max_lines: int = 12) -> str:
    """A short unified diff of two normalized answers, for the failure report."""
    try:
        e = expected.decode("utf-8").splitlines()
        a = actual.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return "  (binary answer; no textual diff)"
    diff = [
        line for line in difflib.unified_diff(e, a, "expected", "actual", lineterm="", n=1)
    ]
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... ({len(diff)} total diff lines, truncated)"]
    return "\n".join("  " + line for line in diff)


def make_tmp_root() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="kicad-conformance-")
