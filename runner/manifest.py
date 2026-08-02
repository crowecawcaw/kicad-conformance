"""Loads and validates `case.toml` per docs/TEST_CASE_FORMAT.md §4.

The schema there is the authoring contract, documented independently of this runner
(DESIGN.md §9: "the runner is a reference, not the spec"). This module enforces the
"Rules the runner enforces" list at the end of TEST_CASE_FORMAT.md §4.2.

Judgment call (documented, not a silent guess): §4.2's field table lists `control` as a
**check**-level field, but the worked examples in §5.2/§5.2b place `control = "..."` at
the case's top level, alongside `input`. Since a failure case's positive control is
naturally "the one alternate fixture for this case" rather than a per-check knob, this
loader accepts `control` at the case level (as the worked examples show) and *also*
allows a `[[check]]` to override it with its own `control`, so both spellings work.

`known_divergence` (DL-0018) follows the identical resolution pattern: a case-level
`[known_divergence]` table is the default for every check in the case, and a `[[check]]`
may set its own `known_divergence = { ... }` to override it. See `KnownDivergence` below
and `runner/engine.py` for the strict-xfail scoring this enables.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union


class CaseError(Exception):
    """A case.toml violates the schema or its own directory's polarity."""


@dataclass
class KnownDivergence:
    """A declared, tracked non-conformance of the reference oracle itself (DL-0018).

    Strict-xfail semantics, applied as a layer on top of the ordinary OK/REJECT/CRASH
    verdict (DESIGN.md §3a) -- it never changes what the adapter's verdict *is*, only how
    the runner scores an already-bad, already-expected verdict:

    - the check's actual verdict matches `kind` (e.g. a CRASH when `kind = "crash"`) ->
      XFAIL ("known divergence"), not a failure -- the build stays green.
    - the check instead comes back clean (the oracle got fixed) -> XPASS -- FAILS the
      build, because a strict xfail must not silently rot (docs/DIVERGENCES.md must be
      updated and the marker removed).
    """

    reason: str
    kind: str
    tracking: Optional[str] = None


@dataclass
class Check:
    op: str
    expect: str
    error_contains: Optional[str] = None
    error_contains_any: Optional[list[str]] = None
    compare: str = "exit"
    golden: Optional[str] = None
    control: Optional[Union[str, dict]] = None
    format: Optional[str] = None
    args: list[str] = field(default_factory=list)
    name: Optional[str] = None
    known_divergence: Optional[KnownDivergence] = None

    def label(self, index: int) -> str:
        return self.name or f"{self.op}#{index + 1}"


@dataclass
class Case:
    path: Path
    concept: str
    doc: Optional[str]
    inputs: list[str]
    root: Optional[str]
    tags: list[str]
    min_kicad: Optional[str]
    skip_reason: Optional[str]
    control: Optional[Union[str, dict]]
    known_divergence: Optional[KnownDivergence]
    checks: list[Check]
    polarity: str  # "happy" or "failure", read off the directory path

    @property
    def input_paths(self) -> list[Path]:
        return [self.path / p for p in self.inputs]

    def golden_dir(self, version: str) -> Path:
        return self.path / "golden" / version


_VALID_COMPARE = {"exit", "structured", "golden-file", "golden-dir"}
_VALID_EXPECT = {"ok", "error"}


def _parse_known_divergence(raw: object, where: str) -> Optional[KnownDivergence]:
    """Parse a `[known_divergence]` table (case-level) or an inline `known_divergence =
    { ... }` (check-level override) -- same schema either way (TEST_CASE_FORMAT.md §4)."""
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

    has_input = "input" in raw
    has_inputs = "inputs" in raw
    if has_input == has_inputs:
        raise CaseError(
            f"{toml_path}: exactly one of `input`/`inputs` is required "
            f"(got input={has_input}, inputs={has_inputs})"
        )
    inputs = [raw["input"]] if has_input else list(raw["inputs"])

    raw_checks = raw.get("check", [])
    if not raw_checks:
        raise CaseError(f"{toml_path}: at least one [[check]] is required")

    checks: list[Check] = []
    for i, rc in enumerate(raw_checks):
        if "op" not in rc:
            raise CaseError(f"{toml_path}: check #{i + 1} missing required `op`")
        expect = rc.get("expect")
        if expect not in _VALID_EXPECT:
            raise CaseError(
                f"{toml_path}: check #{i + 1} has invalid/missing `expect` "
                f"(must be 'ok' or 'error'), got {expect!r}"
            )
        compare = rc.get("compare", "exit")
        if compare not in _VALID_COMPARE:
            raise CaseError(
                f"{toml_path}: check #{i + 1} has invalid `compare` {compare!r}"
            )
        golden = rc.get("golden")
        if compare in ("golden-file", "golden-dir", "structured") and not golden:
            raise CaseError(
                f"{toml_path}: check #{i + 1} compare={compare!r} requires `golden`"
            )
        error_contains = rc.get("error_contains")
        error_contains_any = rc.get("error_contains_any")
        if (error_contains or error_contains_any) and expect != "error":
            raise CaseError(
                f"{toml_path}: check #{i + 1} `error_contains*` only valid with "
                f"expect='error'"
            )
        checks.append(
            Check(
                op=rc["op"],
                expect=expect,
                error_contains=error_contains,
                error_contains_any=error_contains_any,
                compare=compare,
                golden=golden,
                control=rc.get("control"),
                format=rc.get("format"),
                args=list(rc.get("args", [])),
                name=rc.get("name"),
                known_divergence=_parse_known_divergence(
                    rc.get("known_divergence"), f"{toml_path}: check #{i + 1}"
                ),
            )
        )

    polarity = _polarity_from_path(case_dir)
    if polarity is None:
        raise CaseError(
            f"{toml_path}: case is not under a happy/ or failure/ directory"
        )

    has_error_check = any(c.expect == "error" for c in checks)
    if polarity == "happy" and has_error_check:
        raise CaseError(
            f"{toml_path}: case is under happy/ but has an expect='error' check"
        )
    if polarity == "failure" and not has_error_check:
        raise CaseError(
            f"{toml_path}: case is under failure/ but has no expect='error' check"
        )

    return Case(
        path=case_dir,
        concept=raw.get("concept", ""),
        doc=raw.get("doc"),
        inputs=inputs,
        root=raw.get("root"),
        tags=list(raw.get("tags", [])),
        min_kicad=raw.get("min_kicad"),
        skip_reason=raw.get("skip_reason"),
        control=raw.get("control"),
        known_divergence=_parse_known_divergence(raw.get("known_divergence"), str(toml_path)),
        checks=checks,
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
