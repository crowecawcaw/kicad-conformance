"""Loads and validates `case.toml` per docs/TEST_CASE_FORMAT.md §5.

Since [DL-0025]/[DL-0027] a case names no verb and no output file: the input's file
suffix chooses a fixed set of **standard answers** (VALIDATION.md §9.1), and `extra`
(a flat list of names) is the only opt-in knob. There is no `[[check]]`, `op`,
`expected`, `outcome`, `args` or `compare` any more -- a manifest that still has one of
those is an authoring error, not something to silently honour (ROADMAP.md M0.5 item 3).

`known_divergence` (DL-0018) is unchanged: a case-level `[known_divergence]` table
declares that the *reference oracle itself* is known to diverge, as a strict xfail. See
`runner/engine.py` for the scoring this enables.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class CaseError(Exception):
    """A case.toml violates the schema or its own directory's polarity."""


@dataclass
class KnownDivergence:
    """A declared, tracked non-conformance of the reference oracle itself (DL-0018).

    Strict-xfail semantics, applied as a layer on top of the ordinary OK/REJECT/CRASH
    verdict (DESIGN.md §3a) -- it never changes what the adapter's verdict *is*, only how
    the runner scores an already-bad, already-expected verdict:

    - the case's actual verdict matches `kind` (e.g. a CRASH when `kind = "crash"`) ->
      XFAIL ("known divergence"), not a failure -- the build stays green.
    - the case instead comes back clean (the oracle got fixed) -> XPASS -- FAILS the
      build, because a strict xfail must not silently rot (docs/DIVERGENCES.md must be
      updated and the marker removed).
    """

    reason: str
    kind: str
    tracking: Optional[str] = None


# The extras a case may opt into (TEST_CASE_FORMAT.md §6). One name, one answer file --
# except `summary-kicadxml`, which adds no file and instead re-asserts the SAME
# `summary.json` from KiCad's XML netlist export (VALIDATION.md §4.2's cross-format-
# fairness proof). `runner/engine.py`'s `answer_for_extra` is the other half of this
# table (name -> invocation + comparison); kept here, not there, so `manifest.py` never
# has to import the engine to validate a case.
EXTRA_NAMES = frozenset(
    {"drc", "erc", "pos", "stats", "ipcd356", "netlist", "summary-kicadxml"}
)

# Every key `case.toml` is allowed to declare (TEST_CASE_FORMAT.md §5's field table). A
# manifest with anything else -- `op`, `[[check]]` (-> raw key `check`), `expected`,
# `outcome`, `args`, `compare`, `tags`, ... -- is loud, not silently accepted.
_KNOWN_KEYS = {
    "concept",
    "doc",
    "input",
    "inputs",
    "root",
    "extra",
    "control",
    "error_contains",
    "error_contains_any",
    "min_kicad",
    "skip_reason",
    "known_divergence",
}


@dataclass
class Case:
    path: Path
    concept: str
    doc: Optional[str]
    inputs: list[str]
    root: Optional[str]
    extra: list[str]
    control: Optional[str]
    error_contains: Optional[str]
    error_contains_any: Optional[list[str]]
    min_kicad: Optional[str]
    skip_reason: Optional[str]
    known_divergence: Optional[KnownDivergence]
    polarity: str  # "happy" or "failure", read off the directory path

    @property
    def input_paths(self) -> list[Path]:
        return [self.path / p for p in self.inputs]

    def expected_dir(self, version: str) -> Path:
        return self.path / "expected" / version


def _parse_known_divergence(raw: object, where: str) -> Optional[KnownDivergence]:
    """Parse a `[known_divergence]` table (TEST_CASE_FORMAT.md §8)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CaseError(f"{where}: `known_divergence` must be a table")
    reason = raw.get("reason")
    if not reason:
        raise CaseError(f"{where}: known_divergence.reason is required (one line, why the oracle diverges)")
    kind = raw.get("kind")
    if not kind:
        raise CaseError(f"{where}: known_divergence.kind is required (e.g. 'crash')")
    return KnownDivergence(reason=reason, kind=kind, tracking=raw.get("tracking"))


def _polarity_from_path(path: Path) -> Optional[str]:
    parts = {p.lower() for p in path.parts}
    if "happy" in parts:
        return "happy"
    if "failure" in parts:
        return "failure"
    return None


def load_case(case_dir: Path) -> Case:
    """Parse and validate `<case_dir>/case.toml`. Raises CaseError on any schema or
    polarity violation so a miscategorized/malformed case is loud, not silently
    skipped."""
    toml_path = case_dir / "case.toml"
    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise CaseError(
            f"{toml_path}: unknown key(s) {sorted(unknown)} -- checks are inferred from "
            f"the input's file type, see docs/TEST_CASE_FORMAT.md §2/§5 (there is no "
            f"[[check]], op, expected, outcome, args or compare any more, DL-0025/DL-0027)"
        )

    has_input = "input" in raw
    has_inputs = "inputs" in raw
    if has_input == has_inputs:
        raise CaseError(
            f"{toml_path}: exactly one of `input`/`inputs` is required "
            f"(got input={has_input}, inputs={has_inputs})"
        )
    inputs = [raw["input"]] if has_input else list(raw["inputs"])

    polarity = _polarity_from_path(case_dir)
    if polarity is None:
        raise CaseError(
            f"{toml_path}: case is not under a happy/ or failure/ directory"
        )

    if not raw.get("concept"):
        raise CaseError(f"{toml_path}: `concept` is required (one sentence, the case's headline)")

    extra = list(raw.get("extra", []))
    bad_extra = sorted(set(extra) - EXTRA_NAMES)
    if bad_extra:
        raise CaseError(
            f"{toml_path}: unknown extra name(s) {bad_extra}; valid: {sorted(EXTRA_NAMES)}"
        )

    control = raw.get("control")
    if control is not None and not isinstance(control, str):
        raise CaseError(f"{toml_path}: `control` must be a string (a sibling fixture path)")

    error_contains = raw.get("error_contains")
    error_contains_any = raw.get("error_contains_any")

    if polarity == "happy":
        if control is not None:
            raise CaseError(f"{toml_path}: happy/ case must not set `control` (failure/ only)")
        if error_contains is not None or error_contains_any is not None:
            raise CaseError(f"{toml_path}: happy/ case must not set error_contains* (failure/ only)")
    else:  # failure
        if control is None:
            raise CaseError(
                f"{toml_path}: failure/ case requires `control` -- a defect-free sibling "
                f"input that must be accepted (DL-0013: \"a test that can't fail is not evidence\")"
            )
        if extra:
            raise CaseError(
                f"{toml_path}: failure/ case records no answers at all, so `extra` is not valid here"
            )

    return Case(
        path=case_dir,
        concept=raw["concept"],
        doc=raw.get("doc"),
        inputs=inputs,
        root=raw.get("root"),
        extra=extra,
        control=control,
        error_contains=error_contains,
        error_contains_any=list(error_contains_any) if error_contains_any else None,
        min_kicad=raw.get("min_kicad"),
        skip_reason=raw.get("skip_reason"),
        known_divergence=_parse_known_divergence(raw.get("known_divergence"), str(toml_path)),
        polarity=polarity,
    )


def discover_cases(roots: list[Path]) -> list[Path]:
    """Walk `roots`, returning every directory that contains a `case.toml`, sorted for
    stable report ordering."""
    found: set[Path] = set()
    for root in roots:
        if (root / "case.toml").is_file():
            found.add(root)
            continue
        if root.is_file():
            continue
        for toml_path in root.rglob("case.toml"):
            found.add(toml_path.parent)
    return sorted(found, key=lambda p: p.as_posix())
