"""The determinism self-test (DESIGN.md §4a): run every rich-output check TWICE on the
same fixture and assert the *normalized/reduced* result is byte-/value-identical both
times. This is what proves a normalizer is load-bearing rather than decorative --
"a test that cannot fail is not evidence" (ROADMAP.md, standing rule).

For each qualifying check this also reports whether the RAW (pre-normalization) output
already differed between the two runs. That is informational, not a failure condition
-- some outputs are provably stable and get no normalizer at all (the honesty rule, §4)
-- but when raw output *does* differ while the normalized result does not, that is the
concrete, printed proof that the normalizer is doing real work (rather than "a
normalizer that never changes anything is either dead or masking something").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from runner import engine as _engine
from runner.adapter import Adapter
from runner.manifest import Case, Check, load_case


@dataclass
class DeterminismOutcome:
    label: str
    ok: bool
    raw_identical: bool
    detail: str = ""


def _raw_snapshot(check: Check, case: Case, out_dir: Path):
    """The pre-normalization content, in the same shape `_normalized_*` would
    consume, so a raw/normalized pair is a fair apples-to-apples comparison."""
    artifact = _engine._resolve_artifact(check, case, out_dir)
    if check.compare == "golden-dir":
        return {
            f.relative_to(artifact).as_posix(): f.read_bytes()
            for f in sorted(artifact.rglob("*"))
            if f.is_file()
        }
    if check.compare == "golden-file":
        return artifact.read_bytes()
    if check.compare == "structured":
        if check.op in ("drc", "erc"):
            return json.loads(artifact.read_text(encoding="utf-8"))
        if check.op == "netlist":
            return artifact.read_text(encoding="utf-8")
    raise ValueError(f"no raw snapshot for compare={check.compare!r} op={check.op!r}")


def _normalized_snapshot(check: Check, case: Case, out_dir: Path):
    artifact = _engine._resolve_artifact(check, case, out_dir)
    if check.compare == "golden-dir":
        return _engine._normalized_dir_tree(artifact)
    if check.compare == "golden-file":
        return _engine._normalized_file_bytes(artifact)
    if check.compare == "structured":
        return _engine._reduce_structured(check, artifact)
    raise ValueError(f"no normalized snapshot for compare={check.compare!r}")


def check_determinism(adapter: Adapter, case_dir: Path, tmp_root: Path) -> list[DeterminismOutcome]:
    case = load_case(case_dir)
    outcomes: list[DeterminismOutcome] = []
    if case.skip_reason:
        return outcomes
    for i, check in enumerate(case.checks):
        if check.compare not in ("golden-file", "golden-dir", "structured"):
            continue
        if not adapter.supports(check.op):
            continue
        label = check.label(i)
        inputs = [case.path / p for p in case.inputs]
        out_a = tmp_root / f"{label}_run1"
        out_b = tmp_root / f"{label}_run2"
        result_a = adapter.invoke(check.op, inputs, out_a, root=case.root, fmt=check.format, extra_args=check.args)
        result_b = adapter.invoke(check.op, inputs, out_b, root=case.root, fmt=check.format, extra_args=check.args)
        if result_a.returncode != 0 or result_b.returncode != 0:
            outcomes.append(DeterminismOutcome(label, ok=False, raw_identical=False,
                                                 detail=f"{label}: adapter did not exit 0 on one or both runs; cannot compare"))
            continue
        try:
            raw_a, raw_b = _raw_snapshot(check, case, out_a), _raw_snapshot(check, case, out_b)
            norm_a, norm_b = _normalized_snapshot(check, case, out_a), _normalized_snapshot(check, case, out_b)
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
