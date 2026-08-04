"""`--verify-assertions` (docs/ASSERTED_COVERAGE.md, DL-0030): per case, per
`perturb/<slug>/` directory, checks that running the case with the perturbation
substituted for its declared input(s) -- against the case's own *committed*
`expected/<version>/` answers -- makes at least one comparison FAIL. That is the
mechanized version of the contributor checklist's manual, unrecorded step
(`docs/TEST_CASE_FORMAT.md` §11: "broke the input and watched it go red").

This module is the sibling of `runner/determinism.py`: it owns the per-case loop and the
four perturbation statuses (`ASSERTED`/`INERT`/`INVALID-PERTURBATION`/`CRASH`) plus the
case-level `UNASSERTED-CASE` count (§3.4). `runner/cli.py` owns printing/exit-code
plumbing, same division as the determinism mode. `runner/engine.py` owns the mechanism
(input substitution + short-circuited generation, reusing the existing comparators
unchanged) -- nothing here re-implements a comparator or writes to `expected/`.
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
# Counted and printed, never a failure (§3.4's adoption ratchet).
UNASSERTED_CASE = "UNASSERTED-CASE"

# Perturbation statuses that fail the build from day one (§3.2's status table) -- unlike
# `UNASSERTED_CASE`, which is only ever counted.
FAILING_STATUSES = {INERT, INVALID_PERTURBATION, CRASH}


@dataclass
class PerturbationOutcome:
    case_dir: Path
    slug: str
    status: str
    moved: list[str] = field(default_factory=list)
    # "semantic" | "byte-only" (§3.2, DL-0015/DL-0026) -- only set when status == ASSERTED.
    label: Optional[str] = None
    detail: str = ""


@dataclass
class CaseAssertionResult:
    case_dir: Path
    concept: str
    outcomes: list[PerturbationOutcome] = field(default_factory=list)
    # True iff this is a happy, non-skipped case with zero `perturb/<slug>/` directories
    # -- the §3.4 ratchet count. A rejection case is never unasserted: its `control` is
    # already its falsifiability check (DL-0013), so it isn't counted either way.
    unasserted: bool = False
    skipped: bool = False
    skip_reason: str = ""

    @property
    def ok(self) -> bool:
        if self.skipped:
            return True
        return all(o.status not in FAILING_STATUSES for o in self.outcomes)


def _diff_excerpt(original: Path, perturbed: Path, max_lines: int = 16) -> str:
    """`diff <input> perturb/<slug>/<input>` (§3.1 rule 4's "complete statement of the
    perturbation"), truncated so a large fixture doesn't flood the report. Read as text
    with replacement on decode error -- fixtures are always text s-expressions, but this
    must never raise and hide the real INERT finding behind an encoding exception."""
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
        # Rule 2: an overlay filename matching no declared input is an error, not a
        # silent no-op.
        return PerturbationOutcome(case.path, pert.slug, INVALID_PERTURBATION, detail=pert.error)

    input_paths = [pert.overlay.get(Path(p).name, case.path / p) for p in case.inputs]
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
        # Rule 3: a perturbation of a happy case must still LOAD. A perturbation that
        # simply breaks the file trivially "changes the answer" and is a rejection case
        # wearing a disguise -- INVALID-PERTURBATION, never ASSERTED.
        return PerturbationOutcome(
            case.path, pert.slug, INVALID_PERTURBATION,
            detail=(
                f"perturbed input was REJECTED by the oracle while generating {last.name!r} "
                f"-- a happy-case perturbation must still load (§3.1 rule 3); this is a "
                f"defect in the perturbation itself, not evidence of anything: {last.detail}"
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
        "is semantically a no-op. Adjudicate; do not delete (§3.2/§6.6).\n"
        + "\n".join(diff_blocks)
    )
    return PerturbationOutcome(case.path, pert.slug, INERT, detail=detail)


def check_case_assertions(engine: Engine, case_dir: Path, tmp_root: Path) -> CaseAssertionResult:
    """The per-case entry point, mirroring `determinism.check_determinism`'s shape."""
    case = load_case(case_dir)

    if case.skip_reason:
        return CaseAssertionResult(case_dir, case.concept, skipped=True, skip_reason=case.skip_reason)

    if case.polarity == "failure":
        # Rule 5: a rejection case must not carry `perturb/` at all -- its `control`
        # already is a falsifiability check (§2.2). Surface the violation if present;
        # otherwise a rejection case contributes nothing to this report (neither
        # `UNASSERTED-CASE` nor any perturbation outcome).
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
