"""Loads and validates `case.toml` per docs/TEST_CASE_FORMAT.md §5.

Since [DL-0025]/[DL-0027] a case names no verb and no output file: the input's file
suffix chooses a fixed set of **standard answers** (DESIGN.md §2), and `extra`
(a flat list of names) is the only opt-in knob. There is no `[[check]]`, `op`,
`expected`, `outcome`, `args` or `compare` any more -- a manifest that still has one of
those is an authoring error, not something to silently honour.

**Polarity comes from the manifest, not the directory.** A case that sets `control` (and
therefore `error_contains`/`error_contains_any`) is a rejection case; one that sets
neither is a happy case. There is no `happy/`/`failure/` directory level to read this
off any more -- rejection case *directories* are named with a `rejects-` prefix purely as
a naming convention for readability, and the runner never parses the path for meaning.

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
    probe: Optional[str] = None
    # `probe` (DL-0029) is a narrow, deliberate escape hatch, not a general per-case verb
    # knob (DL-0025/DL-0027 removed exactly that): it exists ONLY so a `known_divergence`
    # can keep documenting a crash that lives on a *non-default* code path after the
    # standard probe for its input kind was remapped away from the path that crashes.
    # Concretely: `parse-pcb`'s probe moved from `pcb upgrade --force` (SIGSEGVs on every
    # malformed board) to `pcb export stats` (rejects gracefully) -- see DL-0029. Every
    # rejects-* board case now exercises the graceful path and genuinely PASSes except
    # `rejects-unterminated-sexpr` (DIV-0001), which sets `probe = "parse-pcb-upgrade"` to
    # keep invoking the old crashing command on purpose, so the segfault stays documented
    # and tested instead of silently going untested once the default probe stopped
    # tripping over it. `runner/engine.py`'s `_run_failure_case` substitutes this verb for
    # the derived `LOADER_VERB[kind]` (main check AND control) when set.


# The extras a case may opt into (TEST_CASE_FORMAT.md §6). One name, one answer file --
# except `summary-kicadxml`, which adds no file and instead re-asserts the SAME
# `summary.json` from KiCad's XML netlist export (DESIGN.md §3b.2's cross-format-
# fairness proof). `runner/engine.py`'s `answer_for_extra` is the other half of this
# table (name -> invocation + comparison); kept here, not there, so `manifest.py` never
# has to import the engine to validate a case.
EXTRA_NAMES = frozenset(
    {
        "drc", "erc", "pos", "stats", "ipcd356", "netlist", "summary-kicadxml",
        # DL-0036: refill zones before DRC (`pcb drc --refill-zones`) -- the only way to
        # reach `pcbnew/zone_filler.cpp`, otherwise dead (docs/COVERAGE.md).
        "refill-zones",
        # DL-0038: board/schematic parity (`pcb drc --schematic-parity`) -- needs a
        # same-stem `.kicad_sch` sibling next to the board (see adapters/kicad.py's
        # `_scratch_copy_board`); no manifest change needed for the sibling itself.
        "parity",
        # DL-0037: PDF/DXF export, opt-in (least-diffable formats; not part of any
        # standard battery).
        "pdf", "dxf",
    }
)

# Every key `case.toml` is allowed to declare (TEST_CASE_FORMAT.md §5's field table). A
# manifest with anything else -- `op`, `[[check]]` (-> raw key `check`), `expected`,
# `outcome`, `args`, `compare`, `tags`, `min_kicad`, ... -- is loud, not silently accepted.
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
    skip_reason: Optional[str]
    known_divergence: Optional[KnownDivergence]
    polarity: str  # "happy" or "failure", derived from whether `control` is set

    @property
    def input_paths(self) -> list[Path]:
        """Every `input`/`inputs` entry, resolved to a full path. A multi-sheet
        schematic's sub-sheets are listed here too (`runner/engine.py` passes the whole
        list to the adapter, preserving filenames, so relative sheet references
        resolve -- DESIGN.md §2's `netlist`/`summary` note)."""
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
    probe = raw.get("probe")
    if probe is not None and not isinstance(probe, str):
        raise CaseError(f"{where}: known_divergence.probe must be a string (a verb name)")
    return KnownDivergence(reason=reason, kind=kind, tracking=raw.get("tracking"), probe=probe)


def _polarity_from_manifest(control: Optional[str]) -> str:
    """A case's polarity is a fact about what it declares, not about which directory it
    sits in (owner ruling, superseding the old `happy/`/`failure/` split): a case that
    names a positive `control` is a rejection case (DL-0013 -- "a test that can't fail is
    not evidence" requires one); a case with no `control` is a happy case. There is
    nothing else it could mean -- a case cannot assert both "the tool must accept this"
    and "the tool must reject this" of the same input."""
    return "failure" if control is not None else "happy"


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
            f"[[check]], op, expected, outcome, args, compare or min_kicad any more, "
            f"DL-0025/DL-0027)"
        )

    has_input = "input" in raw
    has_inputs = "inputs" in raw
    if has_input == has_inputs:
        raise CaseError(
            f"{toml_path}: exactly one of `input`/`inputs` is required "
            f"(got input={has_input}, inputs={has_inputs})"
        )
    inputs = [raw["input"]] if has_input else list(raw["inputs"])

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
    polarity = _polarity_from_manifest(control)

    if polarity == "happy":
        if error_contains is not None or error_contains_any is not None:
            raise CaseError(
                f"{toml_path}: error_contains*/set with no `control` -- that pair only "
                f"means something on a rejection case, and a rejection case is exactly "
                f"one that sets `control` (DL-0013)"
            )
    else:  # failure (rejection) case
        if extra:
            raise CaseError(
                f"{toml_path}: a rejection case (one with `control`) records no answers "
                f"at all, so `extra` is not valid here"
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
