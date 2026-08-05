"""`--verify-assertions`: per case, per `perturb/<slug>/` directory, checks that running
the case with the perturbation substituted for its input -- against the case's own
*committed* answers -- makes at least one comparison FAIL. This is the mechanized version
of "broke the input and watched it go red".

Sibling of `runner/determinism.py`: this owns the per-case loop and the four perturbation
statuses (ASSERTED / INERT / INVALID-PERTURBATION / CRASH) plus the case-level
UNASSERTED-CASE count. `cli.py` prints; `engine.py` owns the mechanism. Nothing here
re-implements a comparator or writes to `expected/`.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from runner import engine as _engine
from runner.engine import Engine, Verdict
from runner.manifest import (
    Case,
    Perturbation,
    PerturbationError,
    discover_perturbations,
    load_case,
)

ASSERTED = "ASSERTED"
INERT = "INERT"
INVALID_PERTURBATION = "INVALID-PERTURBATION"
CRASH = "CRASH"
# Case-level, not a perturbation status: a happy case that carries no `perturb/` at all.
# Counted and printed, never a failure.
UNASSERTED_CASE = "UNASSERTED-CASE"

# Perturbation statuses that fail the build -- unlike UNASSERTED_CASE, only ever counted.
FAILING_STATUSES = {INERT, INVALID_PERTURBATION, CRASH}


@dataclass
class PerturbationOutcome:
    case_dir: Path
    slug: str
    status: str
    moved: list[str] = field(default_factory=list)
    # "semantic" | "byte-only" -- only set when status == ASSERTED.
    label: Optional[str] = None
    detail: str = ""


@dataclass
class CaseAssertionResult:
    case_dir: Path
    concept: str
    outcomes: list[PerturbationOutcome] = field(default_factory=list)
    # True iff this is a happy case with zero `perturb/<slug>/` directories. A rejection
    # case is never unasserted: its `control` is already its falsifiability check.
    unasserted: bool = False
    skipped: bool = False
    skip_reason: str = ""

    @property
    def ok(self) -> bool:
        if self.skipped:
            return True
        return all(o.status not in FAILING_STATUSES for o in self.outcomes)


def _diff_excerpt(original: Path, perturbed: Path, max_lines: int = 16) -> str:
    """`diff <input> perturb/<slug>/<input>`, truncated so a large fixture doesn't flood
    the report. Decoded with replacement so this can never raise and hide the real INERT
    finding behind an encoding exception."""
    orig_lines = original.read_text(encoding="utf-8", errors="replace").splitlines()
    pert_lines = perturbed.read_text(encoding="utf-8", errors="replace").splitlines()
    diff = list(difflib.unified_diff(
        orig_lines, pert_lines,
        fromfile=original.name, tofile=f"perturb/.../{perturbed.name}", lineterm="", n=1,
    ))
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... ({len(diff)} total diff lines, truncated)"]
    return "\n".join(diff)


def _run_one_perturbation(
    engine: Engine, case: Case, pert: Perturbation, tmp_root: Path,
) -> PerturbationOutcome:
    if pert.error is not None:
        return PerturbationOutcome(case.path, pert.slug, INVALID_PERTURBATION, detail=pert.error)

    input_paths = [pert.overlay.get(name, case.path / name) for name in case.inputs]
    answers = _engine.answers_in_assertion_order(case)

    outcomes = _engine.generate_and_compare_against_committed(
        engine, case, answers, input_paths, tmp_root, label_prefix=pert.slug,
    )

    if not outcomes:
        return PerturbationOutcome(
            case.path, pert.slug, INVALID_PERTURBATION,
            detail="the adapter supports none of this case's answer verbs -- nothing to verify",
        )

    last = outcomes[-1]

    if last.verdict is Verdict.CRASH:
        return PerturbationOutcome(
            case.path, pert.slug, CRASH,
            detail=f"oracle CRASHED on the perturbed input while generating {last.name!r}: {last.detail}",
        )
    if last.verdict is Verdict.REJECT:
        # A perturbation of a happy case must still LOAD: one that simply breaks the
        # file trivially "changes the answer" and is a rejection case in disguise.
        return PerturbationOutcome(
            case.path, pert.slug, INVALID_PERTURBATION,
            detail=(
                f"perturbed input was REJECTED by the oracle while generating {last.name!r} "
                f"-- a happy-case perturbation must still load; this is a defect in the "
                f"perturbation itself, not evidence of anything: {last.detail}"
            ),
        )
    if last.differs is None:
        # verdict is OK but the comparison itself couldn't be carried out (e.g. no
        # committed expected file to compare against, or a malformed artifact) -- not one
        # of the four named states, but it must not silently score ASSERTED or INERT
        # either. Treated as INVALID-PERTURBATION: something about this perturbation
        # (or the case it's attached to) prevents verification.
        return PerturbationOutcome(
            case.path, pert.slug, INVALID_PERTURBATION,
            detail=f"could not verify {last.name!r} against the committed answer: {last.detail}",
        )
    if last.differs:
        moved = [o.name for o in outcomes if o.differs]
        label = "byte-only" if set(moved) <= _engine.BYTE_ONLY_ANSWERS else "semantic"
        return PerturbationOutcome(case.path, pert.slug, ASSERTED, moved=moved, label=label)

    # Ran the full answer list (no short-circuit) and NOTHING differed: INERT.
    diff_blocks = []
    for name, path in sorted(pert.overlay.items()):
        original = case.path / name
        diff_blocks.append(_diff_excerpt(original, path))
    detail = (
        "perturbed input differs from the case input, but every recorded answer is "
        "identical. Either the case does not assert this behaviour, or the perturbation "
        "is semantically a no-op.\n"
        + "\n".join(diff_blocks)
    )
    return PerturbationOutcome(case.path, pert.slug, INERT, detail=detail)


def check_case_assertions(engine: Engine, case_dir: Path, tmp_root: Path) -> CaseAssertionResult:
    """The per-case entry point, mirroring `determinism.check_determinism`'s shape."""
    case = load_case(case_dir)

    if case.polarity == "failure":
        # A rejection case must not carry `perturb/` at all -- its `control` already is a
        # falsifiability check. Surface the violation if present; otherwise a rejection
        # case contributes nothing to this report.
        try:
            discover_perturbations(case)
        except PerturbationError as e:
            return CaseAssertionResult(
                case_dir, case.concept,
                outcomes=[PerturbationOutcome(case_dir, "(perturb/ on a rejection case)", INVALID_PERTURBATION, detail=str(e))],
            )
        return CaseAssertionResult(case_dir, case.concept)

    try:
        perturbations = discover_perturbations(case)
    except PerturbationError as e:
        return CaseAssertionResult(
            case_dir, case.concept,
            outcomes=[PerturbationOutcome(case_dir, "(perturb/)", INVALID_PERTURBATION, detail=str(e))],
        )

    if not perturbations:
        return CaseAssertionResult(case_dir, case.concept, unasserted=True)

    outcomes = [
        _run_one_perturbation(engine, case, pert, tmp_root / f"pert{idx}")
        for idx, pert in enumerate(perturbations)
    ]
    return CaseAssertionResult(case_dir, case.concept, outcomes=outcomes)
