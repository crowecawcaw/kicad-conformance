"""The determinism self-test (DESIGN.md §4a): run every answer in a happy case's
battery TWICE on the same fixture and assert the *normalized/reduced* result is byte-/
value-identical both times. This is what proves a normalizer is load-bearing rather than
decorative -- "a test that cannot fail is not evidence" (ROADMAP.md, standing rule).

For each qualifying answer this also reports whether the RAW (pre-normalization) output
already differed between the two runs. That is informational, not a failure condition --
some outputs are provably stable and get no normalizer at all (the honesty rule, §4) --
but when raw output *does* differ while the normalized result does not, that is the
concrete, printed proof that the normalizer is doing real work (rather than "a
normalizer that never changes anything is either dead or masking something").

a rejection case has no recorded answers (TEST_CASE_FORMAT.md §7) and are excluded --
there is nothing rich to compare twice.
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
    if case.skip_reason or case.polarity == "failure":
        return outcomes

    input_paths = case.input_paths
    input_path = input_paths[0]
    for answer in _engine.answers_for_case(case):
        if not adapter.supports(answer.verb):
            continue
        label = answer.name
        out_a = tmp_root / f"{label}_run1"
        out_b = tmp_root / f"{label}_run2"
        result_a = adapter.invoke(answer.verb, input_paths, out_a, root=case.root, fmt=answer.fmt)
        result_b = adapter.invoke(answer.verb, input_paths, out_b, root=case.root, fmt=answer.fmt)
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
        except FileNotFoundError as e:
            outcomes.append(DeterminismOutcome(label, ok=False, raw_identical=False, detail=f"{label}: missing artifact {e}"))
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
