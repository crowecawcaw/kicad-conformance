"""The determinism self-test: run every answer a happy case records TWICE on the same
fixture and assert the normalized result is identical both times. A normalizer that lets
drift through is caught here rather than as a mystery failure later.

Each answer also reports whether the RAW (pre-normalization) output differed between the
two runs. That is informational, not a failure: some outputs are provably stable and get
no normalizer at all. But when raw output differs while the normalized result does not,
that is printed proof the normalizer is load-bearing.

Rejection cases record no answers and are excluded -- there is nothing to compare twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from runner import engine as _engine
from runner.adapter import Adapter
from runner.manifest import load_case


@dataclass
class DeterminismOutcome:
    label: str
    ok: bool
    raw_identical: bool
    detail: str = ""


def check_determinism(adapter: Adapter, case_dir: Path, tmp_root: Path) -> list[DeterminismOutcome]:
    case = load_case(case_dir)
    outcomes: list[DeterminismOutcome] = []
    if case.polarity == "failure":
        return outcomes

    input_paths = case.input_paths
    input_path = input_paths[0]
    for answer in _engine.answers_for_case(case):
        if not adapter.supports(answer.verb):
            continue
        label = answer.name
        out_a = tmp_root / f"{label}_run1"
        out_b = tmp_root / f"{label}_run2"
        result_a = adapter.invoke(answer.verb, input_paths, out_a)
        result_b = adapter.invoke(answer.verb, input_paths, out_b)
        if result_a.returncode != 0 or result_b.returncode != 0:
            outcomes.append(DeterminismOutcome(
                label, ok=False, raw_identical=False,
                detail=f"{label}: adapter did not exit 0 on one or both runs; cannot compare",
            ))
            continue
        try:
            raw_a = _engine.raw_snapshot(answer, out_a, input_path)
            raw_b = _engine.raw_snapshot(answer, out_b, input_path)
            norm_a = _engine.normalized_snapshot(answer, out_a, input_path)
            norm_b = _engine.normalized_snapshot(answer, out_b, input_path)
        except (FileNotFoundError, ValueError, OSError) as e:
            # "The artifact wasn't shaped the way this answer expects" -- a reportable
            # defect in a case, the adapter, or an Answer's wiring, never something to
            # crash the whole suite over. Anything outside this tuple means the
            # comparison code itself is broken and stays loud.
            artifact_a = answer.artifact(out_a, input_path)
            outcomes.append(DeterminismOutcome(
                label, ok=False, raw_identical=False,
                detail=(
                    f"{case_dir.as_posix()} :: {label}: could not compare artifact "
                    f"{artifact_a} across the two runs: {e}"
                ),
            ))
            continue
        raw_identical = raw_a == raw_b
        ok = norm_a == norm_b
        detail = "normalized output stable across two runs"
        if not ok:
            detail = f"{label}: NORMALIZED OUTPUT DIFFERED ACROSS TWO RUNS -- normalizer is not load-bearing enough"
        elif not raw_identical:
            detail = f"{label}: raw output DIFFERED across runs but normalized output matched -- normalizer proven load-bearing"
        outcomes.append(DeterminismOutcome(label, ok=ok, raw_identical=raw_identical, detail=detail))
    return outcomes
