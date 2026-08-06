"""Loads and validates `case.toml`.

A case names no verb and no output file: the input's file suffix chooses a fixed set of
recorded answers, and `extra` is the only opt-in knob. Unknown keys are rejected here, so
every normal suite load is also the schema lint.

Polarity comes from the manifest: a case that sets `control` is a rejection case; one
that does not is a happy case.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class CaseError(Exception):
    """A case.toml violates the schema, or a case directory violates its contract."""


class PerturbationError(CaseError):
    """A `perturb/` directory violates the overlay contract at the case level (today:
    a rejection case carrying `perturb/` at all). A single bad slug is recorded on that
    `Perturbation.error` instead, so one malformed slug does not hide the rest."""


# The answers a case may opt into on top of its input type's standard set. One name, one
# answer -- except `roundtrip`, which records nothing and asserts an invariant.
EXTRA_NAMES = frozenset({"drc", "erc", "refill", "roundtrip"})

# Every key `case.toml` is allowed to declare.
_KNOWN_KEYS = {
    "concept",
    "doc",
    "input",
    "extra",
    "control",
    "error_contains",
    "xfail",
}

# Case-directory entries that are never input/support files.
_NON_INPUT_ENTRIES = {"case.toml", "expected", "perturb"}


@dataclass
class Case:
    path: Path
    concept: str
    doc: Optional[str]
    input: str
    extra: list[str]
    control: Optional[str]
    error_contains: Optional[str]
    xfail: Optional[str]
    polarity: str  # "happy" or "failure", derived from whether `control` is set

    @property
    def inputs(self) -> list[str]:
        """The entry file first, then every other file/directory in the case directory
        (except `case.toml`, `expected/` and `perturb/`). Support files are discovered on
        disk, not declared: a hierarchical schematic's sub-sheets, a board's `.kicad_dru`
        rules, a rejection case's `control` fixture. All of them are copied to the scratch
        directory under their own names so relative references resolve."""
        support = sorted(
            p.name
            for p in self.path.iterdir()
            if p.name not in _NON_INPUT_ENTRIES and p.name != self.input
        )
        return [self.input, *support]

    @property
    def input_paths(self) -> list[Path]:
        return [self.path / name for name in self.inputs]

    def expected_dir(self, version: str) -> Path:
        return self.path / "expected" / version


def load_case(case_dir: Path) -> Case:
    """Parse and validate `<case_dir>/case.toml`. Raises CaseError on any schema
    violation so a malformed case is loud, not silently skipped."""
    toml_path = case_dir / "case.toml"
    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        raise CaseError(
            f"{toml_path}: unknown key(s) {unknown} -- the only allowed keys are "
            f"{sorted(_KNOWN_KEYS)}"
        )

    if not raw.get("concept"):
        raise CaseError(f"{toml_path}: `concept` is required (one sentence, the case's headline)")

    input_name = raw.get("input")
    if not isinstance(input_name, str) or not input_name:
        raise CaseError(f"{toml_path}: `input` is required (the entry file's name)")
    if not (case_dir / input_name).exists():
        raise CaseError(f"{toml_path}: `input` {input_name!r} does not exist")

    extra = list(raw.get("extra", []))
    bad_extra = sorted(set(extra) - EXTRA_NAMES)
    if bad_extra:
        raise CaseError(
            f"{toml_path}: unknown extra name(s) {bad_extra}; valid: {sorted(EXTRA_NAMES)}"
        )

    control = raw.get("control")
    if control is not None and not isinstance(control, str):
        raise CaseError(f"{toml_path}: `control` must be a string (a sibling fixture name)")

    error_contains = raw.get("error_contains")
    if error_contains is not None and not isinstance(error_contains, str):
        raise CaseError(f"{toml_path}: `error_contains` must be a string")

    xfail = raw.get("xfail")
    if xfail is not None and (not isinstance(xfail, str) or not xfail):
        raise CaseError(f"{toml_path}: `xfail` must be a non-empty divergence id (e.g. \"DIV-0004\")")

    polarity = "failure" if control is not None else "happy"
    if polarity == "happy":
        if error_contains is not None:
            raise CaseError(
                f"{toml_path}: `error_contains` with no `control` -- that pair only means "
                f"something on a rejection case, and a rejection case is one that sets "
                f"`control`"
            )
        if xfail is not None and "roundtrip" not in extra:
            raise CaseError(
                f"{toml_path}: `xfail` on a happy case marks its `roundtrip` answer, so "
                f"the case must opt into extra = [\"roundtrip\"]"
            )
    elif extra:
        raise CaseError(
            f"{toml_path}: a rejection case (one with `control`) records no answers, so "
            f"`extra` is not valid here"
        )

    return Case(
        path=case_dir,
        concept=raw["concept"],
        doc=raw.get("doc"),
        input=input_name,
        extra=extra,
        control=control,
        error_contains=error_contains,
        xfail=xfail,
        polarity=polarity,
    )


@dataclass
class Perturbation:
    """One `perturb/<slug>/` directory. `overlay` maps an input's filename to the
    perturbed file that replaces it for this run; anything not named is used unchanged.
    `error`, when set, means this slug is malformed and must not be run."""

    slug: str
    path: Path
    overlay: dict[str, Path]
    error: Optional[str] = None


def discover_perturbations(case: Case) -> list[Perturbation]:
    """Discover and validate every `perturb/<slug>/` under `case.path`. Returns `[]` when
    there is no `perturb/` directory -- that is the unasserted-case condition, not an
    error. Raises `PerturbationError` only for the case-level violation: a rejection case
    records no answers, so "the answer changed" is undefined for it."""
    perturb_root = case.path / "perturb"
    if not perturb_root.is_dir():
        return []

    if case.polarity == "failure":
        raise PerturbationError(
            f"{case.path}: this is a rejection case (sets `control`) and must not carry a "
            f"`perturb/` directory -- it records no answers, and its `control` is already "
            f"a falsifiability check"
        )

    declared_names = set(case.inputs)
    perturbations: list[Perturbation] = []
    for slug_dir in sorted(p for p in perturb_root.iterdir() if p.is_dir()):
        overlay: dict[str, Path] = {}
        unmatched: list[str] = []
        for f in sorted(slug_dir.iterdir()):
            if not f.is_file():
                continue
            if f.name in declared_names:
                overlay[f.name] = f
            else:
                unmatched.append(f.name)

        if unmatched:
            perturbations.append(Perturbation(
                slug=slug_dir.name, path=slug_dir, overlay={},
                error=(
                    f"file(s) {sorted(unmatched)} match none of this case's input(s) "
                    f"{sorted(declared_names)} -- an overlay filename that isn't an input "
                    f"is an authoring error, not a silent no-op"
                ),
            ))
            continue

        if not overlay:
            perturbations.append(Perturbation(
                slug=slug_dir.name, path=slug_dir, overlay={},
                error="perturb/<slug>/ contains no files -- nothing to overlay",
            ))
            continue

        perturbations.append(Perturbation(slug=slug_dir.name, path=slug_dir, overlay=overlay))
    return perturbations


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
