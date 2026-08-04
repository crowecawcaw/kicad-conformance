"""The runner's core: for a happy case (no `control` set), run the standard battery of
answers the input's file type implies (plus any `extra`) and compare each to
`expected/<version>/`; for a rejection case (sets `control`), run the type's loader and
check the exit/stderr/control contract (DESIGN.md §3a). This module has no
argparse/printing concerns of its own -- see runner/cli.py for the CLI and report
formatting.

DL-0025/DL-0027: a case names no verb and no output file. What gets recorded and how it
is compared follows from the **input's file suffix** (`board`/`sch`/`sym`/`fp`, via
`input_kind`) plus the case's `extra` list, never from a manifest field naming a check.
`battery_for` is the fixed per-type answer set (DESIGN.md §2); `answer_for_extra`
is the one opt-in knob (TEST_CASE_FORMAT.md §6). Comparison follows from each answer's
own `kind` ("json" -> normalized-JSON equality, "svg" -> normalized-SVG byte-exact,
"dir" -> a directory-tree compare with per-file normalizers, DESIGN.md §3) -- never
from a `compare` field (DL-0023).
"""
from __future__ import annotations

import enum
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from runner import normalize, reduce
from runner.adapter import Adapter
from runner.manifest import EXTRA_NAMES, Case, load_case


class Verdict(enum.Enum):
    """The three-way OK / REJECT / CRASH verdict (DESIGN.md §3a, DL-0013).

    A malformed input can make the oracle *crash* rather than reject cleanly (observed:
    KiCad 10.0.5 `pcb upgrade` on a truncated board prints a good `Expecting '('` message
    and then segfaults). A naive "non-zero = rejected" rule would silently pass an
    `outcome="error"` case on a crash. So termination is classified into three outcomes,
    and a CRASH is never a pass -- not for `happy`, not for `failure`.

    Adapter/child-process note: the runner's direct subprocess child is the *adapter*, not
    kicad-cli (DL-0007 -- the adapter contract is itself a subprocess boundary). If
    kicad-cli is signaled, the adapter process must re-raise that same signal against
    itself (see `adapters/kicad.py`) so the signal is still visible as a negative
    `returncode` on the adapter -- the runner's direct child -- rather than being silently
    absorbed into a normal adapter exit code. That is what makes this classifier meaningful
    through the adapter indirection layer.
    """

    OK = "OK"
    REJECT = "REJECT"
    CRASH = "CRASH"


def classify(returncode: int) -> Verdict:
    """Detection is portable: never hard-code the literal 139. On POSIX, Python's
    `subprocess` reports a negative `returncode` when the child was killed by a signal
    (`returncode == -signum`, i.e. `WIFSIGNALED`); we also treat any `returncode > 128`
    as crash-equivalent (the 128+signal convention some shells surface, and the
    Windows-fatal-exception-status case), as a defensive belt-and-suspenders rule."""
    if returncode == 0:
        return Verdict.OK
    if returncode < 0:
        return Verdict.CRASH
    if returncode > 128:
        return Verdict.CRASH
    return Verdict.REJECT


# Statuses a single answer/check can land on. Only PASS/XFAIL (and SKIP, which is
# excluded from both counts) count as non-failing for the exit code.
PASS = "PASS"
FAIL = "FAIL"
CRASH = "CRASH"
SKIP = "SKIP"
NEEDS_REGEN = "NEEDS-REGEN"
NOT_EVIDENCE = "NOT-EVIDENCE"
REGENERATED = "REGENERATED"
# DL-0018 -- strict xfail layer for a declared `known_divergence`. XFAIL is the expected,
# tracked bad verdict (never a build failure); XPASS is the oracle no longer reproducing
# the declared divergence (always a build failure -- the ledger must be updated, not
# silently left stale).
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


# --- The standard battery (DESIGN.md §2) -------------------------------------


@dataclass
class Answer:
    """One recorded answer: which adapter verb produces it, where the adapter writes
    the artifact, how it's compared, and what its `expected/<version>/` name is."""

    name: str
    verb: str
    expected_name: str  # filename (json/svg/file) or directory name (dir) under expected/<version>/
    kind: str  # "json" | "svg" | "dir" | "file"
    artifact: Callable[[Path, Path], Path]  # (out_dir, input_path) -> artifact path
    reduce: Optional[Callable[[Path], object]] = None  # "json" only: artifact -> canonical structure
    fmt: Optional[str] = None  # passed as the adapter's --format (summary-kicadxml)
    # `kind="json"` selects JSON-*comparison* semantics (structured equality after
    # `reduce()`) -- it does NOT mean the on-disk artifact is literally JSON text. Most
    # kind="json" answers' artifact IS a JSON document (summary/drc/erc/stats,
    # summary-kicadxml), but `pos` (pos.csv), `ipcd356` (board.d356) and `netlist`
    # (netlist.net) opt into kind="json" purely for the comparison semantics; their raw
    # artifact is CSV / IPC-D-356 text / an s-expression netlist. `raw_reader`, when set,
    # is how `engine.raw_snapshot` reads that raw pre-reduction content -- it must NOT
    # default to `json.loads` for these, or a legitimate non-JSON artifact reads as a
    # JSONDecodeError crash (see runner/determinism.py's raw/normalized pair). Left `None`
    # for every kind="json" answer whose artifact really is JSON, which keeps the default
    # (`_json_bytes`) meaningful.
    raw_reader: Optional[Callable[[Path], object]] = None
    # `summary-kicadxml` adds no file of its own -- it re-derives `summary.json` and
    # ASSERTS it against the same expected file the standard `summary` answer already
    # wrote, even under --regenerate (TEST_CASE_FORMAT.md §6's "the only entry that adds
    # no file"). Everything else may regenerate; this one only ever compares.
    compare_only: bool = False


def input_kind(input_path: Path) -> str:
    """`board` / `sch` / `sym` / `fp`, from the input's suffix (DESIGN.md §2). A
    footprint library is either a `.pretty` directory or a lone `.kicad_mod` file --
    both record the same `render/` answer (TEST_CASE_FORMAT.md §2)."""
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


# The loader verb a rejection case's input type runs (DESIGN.md §2's `parse-*` row --
# there is no pure "parse and stop" subcommand, so each maps to a real kicad-cli
# subcommand that loads the file, exit-polarity only). `sch`/`sym`/`fp` map to their
# `... upgrade --force`, which rewrites the scratch copy in place. `board` -> `parse-pcb`
# maps to `pcb export stats` (DL-0029), NOT `pcb upgrade --force` -- `upgrade --force`
# SIGSEGVs on every malformed board tested (see docs/DIVERGENCES.md's DIV-0001), while
# `export stats` rejects the same bytes gracefully. A single case
# (`rejects-unterminated-sexpr`) deliberately overrides this via
# `known_divergence.probe = "parse-pcb-upgrade"` to keep exercising that crash on
# purpose -- see `_run_failure_case` below.
LOADER_VERB = {
    "board": "parse-pcb",
    "sch": "parse-sch",
    "sym": "parse-sym",
    "fp": "parse-fp",
}


def _json_bytes(artifact: Path) -> object:
    return json.loads(artifact.read_text(encoding="utf-8"))


def battery_for(kind: str) -> list[Answer]:
    """The fixed, standard-answer set for one input kind (DESIGN.md §2). No
    per-case opt-out (DL-0025) -- every happy case of a given type records the same
    answers."""
    if kind == "board":
        return [
            Answer(
                "summary", verb="summary", expected_name="summary.json", kind="json",
                artifact=lambda out, inp: out / "summary.json",
                reduce=_json_bytes,
            ),
            Answer(
                "render", verb="render", expected_name="render-F_Cu.svg", kind="svg",
                artifact=lambda out, inp: out / "render.svg",
            ),
            Answer(
                "gerbers", verb="export-gerbers", expected_name="gerbers", kind="dir",
                artifact=lambda out, inp: out,
            ),
            Answer(
                "drill", verb="export-drill", expected_name="drill", kind="dir",
                artifact=lambda out, inp: out,
            ),
        ]
    if kind == "sch":
        return [
            Answer(
                "summary", verb="summary", expected_name="summary.json", kind="json",
                artifact=lambda out, inp: out / "summary.json",
                reduce=_json_bytes,
            ),
            Answer(
                "render", verb="render", expected_name="render.svg", kind="svg",
                artifact=lambda out, inp: out / (inp.stem + ".svg"),
            ),
        ]
    if kind in ("sym", "fp"):
        # Libraries get drawings only -- kicad-cli 10.0.5 has no structured symbol/
        # footprint export (DESIGN.md §3b.4). `render` writes one SVG per symbol-unit
        # / footprint straight into the out dir, under KiCad's own names.
        return [
            Answer(
                "render", verb="render", expected_name="render", kind="dir",
                artifact=lambda out, inp: out,
            ),
        ]
    raise ValueError(f"no standard battery for input kind {kind!r}")


def answer_for_extra(name: str) -> Answer:
    """The one answer `extra = [name]` adds (TEST_CASE_FORMAT.md §6, TEST_CASE_FORMAT.md §6).
    Each name is the answer's filename, except `summary-kicadxml`, which reuses
    `summary.json` and only ever compares (never regenerates) -- see `Answer.compare_only`.
    """
    if name == "drc":
        return Answer(
            "drc", verb="drc", expected_name="drc.json", kind="json",
            artifact=lambda out, inp: out / "drc.json",
            reduce=lambda p: reduce.reduce_drc(_json_bytes(p)),
        )
    if name == "erc":
        return Answer(
            "erc", verb="erc", expected_name="erc.json", kind="json",
            artifact=lambda out, inp: out / "erc.json",
            reduce=lambda p: reduce.reduce_erc(_json_bytes(p)),
        )
    if name == "pos":
        return Answer(
            "pos", verb="pos", expected_name="pos.json", kind="json",
            artifact=lambda out, inp: out / "pos.csv",
            reduce=lambda p: reduce.reduce_pos(p.read_text(encoding="utf-8")),
            raw_reader=lambda p: p.read_text(encoding="utf-8"),
        )
    if name == "stats":
        return Answer(
            "stats", verb="stats", expected_name="stats.json", kind="json",
            artifact=lambda out, inp: out / "stats.json",
            reduce=lambda p: reduce.reduce_stats(_json_bytes(p)),
        )
    if name == "ipcd356":
        return Answer(
            "ipcd356", verb="ipcd356", expected_name="ipcd356.json", kind="json",
            artifact=lambda out, inp: out / "board.d356",
            reduce=lambda p: reduce.reduce_ipcd356(p.read_text(encoding="utf-8")),
            raw_reader=lambda p: p.read_text(encoding="utf-8"),
        )
    if name == "netlist":
        return Answer(
            "netlist", verb="netlist", expected_name="netlist.json", kind="json",
            artifact=lambda out, inp: out / "netlist.net",
            reduce=lambda p: reduce.reduce_netlist(p.read_text(encoding="utf-8")),
            raw_reader=lambda p: p.read_text(encoding="utf-8"),
        )
    if name == "summary-kicadxml":
        return Answer(
            "summary-kicadxml", verb="summary", expected_name="summary.json", kind="json",
            artifact=lambda out, inp: out / "summary.json",
            reduce=_json_bytes,
            fmt="kicadxml",
            compare_only=True,
        )
    if name == "refill-zones":
        # DL-0036: `pcb drc --refill-zones` -- the only way to reach
        # `pcbnew/zone_filler.cpp` (0/1991 lines, docs/COVERAGE.md's largest dead
        # non-GUI subsystem): every committed zone fixture ships a pre-baked
        # `(filled_polygon ...)`, so plain `drc` never invokes `ZONE_FILLER::Fill`.
        # A distinct verb (`drc-refill-zones`), not a flag threaded through the
        # ordinary `drc` extra -- DL-0025 deleted per-case CLI args on purpose.
        return Answer(
            "refill-zones", verb="drc-refill-zones", expected_name="refill-zones.json",
            kind="json", artifact=lambda out, inp: out / "refill-zones.json",
            reduce=lambda p: reduce.reduce_drc(_json_bytes(p)),
        )
    if name == "parity":
        # DL-0038: `pcb drc --schematic-parity` -- `drc_test_provider_schematic_parity
        # .cpp` (9/198, docs/COVERAGE.md) was essentially unreached because no case ever
        # passed the flag or shipped a same-stem schematic. The sibling `.kicad_sch` is
        # discovered on disk by `adapters/kicad.py`'s `_scratch_copy_board`, not declared
        # here or in the manifest -- same convention as `.kicad_dru`/`.kicad_pro`. Reuses
        # `reduce_drc` unchanged: its `schematic_parity` key was already wired up and
        # unused (dead code waiting for exactly this).
        return Answer(
            "parity", verb="drc-parity", expected_name="parity.json", kind="json",
            artifact=lambda out, inp: out / "parity.json",
            reduce=lambda p: reduce.reduce_drc(_json_bytes(p)),
        )
    if name == "pdf":
        # DL-0037: PDF_plotter.cpp (0/1433, docs/COVERAGE.md) -- opt-in, not part of any
        # standard battery (§4 of DESIGN.md flags PDF as the least-diffable format).
        # kind="file": a plain byte compare after `normalize.normalize_for` (dispatches
        # on `.pdf` -> `normalize_pdf`, which strips only `/CreationDate` -- verified the
        # one and only run-to-run difference, see adapters/kicad.py's `cmd_export_pdf`).
        return Answer(
            "pdf", verb="export-pdf", expected_name="pdf.pdf", kind="file",
            artifact=lambda out, inp: out / "pdf.pdf",
        )
    if name == "dxf":
        # DL-0037: DXF_plotter.cpp (0/651, docs/COVERAGE.md) -- board-only, opt-in.
        # Verified byte-identical run-to-run (no normalizer registered for `.dxf`;
        # `normalize_for` falls through to the generic CRLF->LF pass, per the honesty
        # rule -- DESIGN.md §4).
        return Answer(
            "dxf", verb="export-dxf", expected_name="dxf.dxf", kind="file",
            artifact=lambda out, inp: out / "dxf.dxf",
        )
    raise ValueError(f"unknown extra {name!r}")


assert set() == EXTRA_NAMES - {
    "drc", "erc", "pos", "stats", "ipcd356", "netlist", "summary-kicadxml",
    "refill-zones", "parity", "pdf", "dxf",
}, "answer_for_extra must handle every name in manifest.EXTRA_NAMES"


def answers_for_case(case: Case) -> list[Answer]:
    """The full list of answers a happy case records: the standard battery for its
    input type, plus one `Answer` per `extra` name."""
    kind = input_kind(case.input_paths[0])
    answers = list(battery_for(kind))
    answers.extend(answer_for_extra(name) for name in case.extra)
    return answers


# --- `--verify-assertions` support (docs/ASSERTED_COVERAGE.md §3.2, DL-0030) ----------
#
# The pieces above (battery_for/answer_for_extra/normalized_snapshot/dir_snapshot) are
# reused UNCHANGED here -- no new comparator, no new answer format (§2.2). What's new is
# running that same generation against a *substituted* input set and comparing the
# result to the case's committed `expected/<version>/` without ever writing to it.

# Answer generation order for a perturbation run: the case's `extra` answers first (the
# case exists *because of* them), then the standard battery in its own fixed order
# (summary, render, gerbers/drill). Generation stops at the first differing answer
# (§3.2's short-circuit) -- `INERT` is the only outcome that ever pays for the full list.
def answers_in_assertion_order(case: Case) -> list[Answer]:
    kind = input_kind(case.input_paths[0])
    extras = [answer_for_extra(name) for name in case.extra]
    return extras + list(battery_for(kind))


# Answer names whose byte compare is a KiCad self-consistency signal, not a cross-adapter
# semantic one (DL-0015/DL-0026): in ecosystem mode `gerbers/`/`drill/` report INFO,
# never FAIL. A perturbation whose *only* moved answer is one of these asserts nothing
# outside KiCad-regression mode -- §3.2's `[byte-only]` label (vs `[semantic]`).
BYTE_ONLY_ANSWERS = frozenset({"gerbers", "drill"})


def expected_normalized(case: Case, answer: Answer, version: str):
    """The committed `expected/<version>/...` answer, read and normalized exactly the way
    `normalized_snapshot` reads a fresh run's artifact -- the other half of "did the
    committed answer move" (§3.2). Read-only: a perturbation run must NEVER write to
    `expected/` (DL-0030's "do not change any recorded answer")."""
    expected_root = case.expected_dir(version)
    expected_path = expected_root / answer.expected_name
    if answer.kind == "json":
        if not expected_path.exists():
            raise FileNotFoundError(f"{answer.name}: no committed expected file at {expected_path}")
        return json.loads(expected_path.read_text(encoding="utf-8"))
    if answer.kind == "svg":
        if not expected_path.exists():
            raise FileNotFoundError(f"{answer.name}: no committed expected file at {expected_path}")
        return normalize.normalize_svg(expected_path.read_bytes())
    if answer.kind == "file":
        if not expected_path.exists():
            raise FileNotFoundError(f"{answer.name}: no committed expected file at {expected_path}")
        return normalize.normalize_for(expected_path, expected_path.read_bytes())
    if answer.kind == "dir":
        if not expected_path.exists():
            raise FileNotFoundError(f"{answer.name}: no committed expected directory at {expected_path}")
        return dir_snapshot(expected_path, normalized=True)
    raise ValueError(f"no expected-normalized reader for answer kind {answer.kind!r}")


@dataclass
class AnswerGenOutcome:
    """One answer's outcome while generating against a (possibly overlaid) input set.
    `verdict` is `Verdict.OK`/`REJECT`/`CRASH` from classifying the adapter invocation
    itself -- this is what stands in for "did the perturbed input still load" (§3.1 rule
    3), reusing the same classifier a rejection case's positive control uses, rather than
    a separate load-only probe. `differs` is only meaningful when `verdict is Verdict.OK`
    and comparison actually ran (`None` if the run stopped before this answer could be
    compared, e.g. the adapter doesn't support its verb)."""

    name: str
    verdict: Verdict
    differs: Optional[bool] = None
    detail: str = ""


def generate_and_compare_against_committed(
    engine: "Engine", case: Case, answers: list[Answer], input_paths: list[Path], tmp_root: Path,
    *, label_prefix: str,
) -> list[AnswerGenOutcome]:
    """Run `answers` in order against `input_paths` and compare each to the case's
    *committed* `expected/<version>/` -- never `--regenerate`s, regardless of
    `engine.regenerate` (a perturbation run must not alter recorded answers). Stops at
    the first answer whose adapter invocation isn't a clean OK, or whose result differs
    from committed (§3.2's short-circuit); an answer whose verb the adapter doesn't
    support is skipped entirely (not reported), matching the happy-case path's SKIP
    handling."""
    outcomes: list[AnswerGenOutcome] = []
    input_path = input_paths[0]
    for answer in answers:
        if not engine.adapter.supports(answer.verb):
            continue
        out_dir = tmp_root / f"{label_prefix}_{answer.name}"
        result = engine.adapter.invoke(answer.verb, input_paths, out_dir, root=case.root, fmt=answer.fmt)
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
            return outcomes  # short-circuit (§3.2)
    return outcomes


# --- Directory-tree comparison (gerbers/, drill/, library render/) ----------------


def _dir_files(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*")) if p.is_file()}


def dir_snapshot(root: Path, *, normalized: bool) -> dict[str, bytes]:
    """Every file under `root`, keyed by its path relative to `root`. Used by both the
    engine's directory comparator and the determinism self-test's raw/normalized pair."""
    out = {}
    for rel, p in _dir_files(root).items():
        data = p.read_bytes()
        if normalized:
            data = normalize.normalize_for(p, data)
        out[rel] = data
    return out


def raw_snapshot(answer: Answer, out_dir: Path, input_path: Path):
    """Pre-normalization content, in the same shape `normalized_snapshot` would consume
    (runner/determinism.py's raw/normalized pair, DESIGN.md §4a)."""
    artifact = answer.artifact(out_dir, input_path)
    if answer.kind == "json":
        if not artifact.exists():
            raise FileNotFoundError(f"{answer.name}: no artifact written at {artifact}")
        if answer.raw_reader is not None:
            return answer.raw_reader(artifact)
        try:
            return _json_bytes(artifact)
        except json.JSONDecodeError as e:
            # A kind="json" answer with no raw_reader is asserting its artifact IS a
            # literal JSON document (see Answer.raw_reader's docstring) -- if that's
            # false, that's a real defect (a wrong `kind`/missing `raw_reader`, or the
            # adapter writing garbage), not something to swallow. Name the answer and
            # artifact so it's diagnosable without re-deriving them from a bare
            # JSONDecodeError (json.loads doesn't know the filename it read).
            raise ValueError(
                f"{answer.name}: artifact at {artifact} is not valid JSON ({e})"
            ) from e
    if answer.kind in ("svg", "file"):
        if not artifact.exists():
            raise FileNotFoundError(f"{answer.name}: no artifact written at {artifact}")
        return artifact.read_bytes()
    if answer.kind == "dir":
        return dir_snapshot(artifact, normalized=False)
    raise ValueError(f"no raw snapshot for answer kind {answer.kind!r}")


def normalized_snapshot(answer: Answer, out_dir: Path, input_path: Path):
    artifact = answer.artifact(out_dir, input_path)
    if answer.kind == "json":
        return answer.reduce(artifact)
    if answer.kind == "svg":
        return normalize.normalize_svg(artifact.read_bytes())
    if answer.kind == "file":
        # `pdf`/`dxf` (DL-0037): a generic per-suffix normalizer dispatch
        # (`normalize.normalize_for`), unlike `svg`'s hardcoded `normalize_svg` --
        # PDF/DXF each need their own (or no) normalizer, chosen by the artifact's own
        # extension.
        return normalize.normalize_for(artifact, artifact.read_bytes())
    if answer.kind == "dir":
        return dir_snapshot(artifact, normalized=True)
    raise ValueError(f"no normalized snapshot for answer kind {answer.kind!r}")


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

        if case.skip_reason:
            return CaseResult(case=case, skipped=True, skip_reason=case.skip_reason)

        if case.polarity == "failure":
            return self._run_failure_case(case, tmp_root)
        return self._run_happy_case(case, tmp_root)

    # --- happy case: the standard battery + extras -------------------------------

    def _run_happy_case(self, case: Case, tmp_root: Path) -> CaseResult:
        # Every declared input goes to the adapter, not just the first -- a multi-sheet
        # schematic's sub-sheets (`inputs` + `root`) must all reach the scratch dir under
        # their original filenames, or `sch export netlist`/`summary` can only see the
        # root sheet in isolation (DESIGN.md §2's netlist note). `input_path` (singular)
        # is still what artifact-naming lambdas key off (e.g. a schematic render's
        # `<stem>.svg`) -- that is always the root/first input.
        input_paths = case.input_paths
        input_path = input_paths[0]
        answers = answers_for_case(case)

        results: list[CheckResult] = []
        for answer in answers:
            label = answer.name
            if not self.adapter.supports(answer.verb):
                results.append(CheckResult(label, SKIP, f"adapter does not support verb {answer.verb!r}"))
                continue

            out_dir = tmp_root / f"{label.replace('/', '_')}_main"
            result = self.adapter.invoke(answer.verb, input_paths, out_dir, root=case.root, fmt=answer.fmt)
            verdict = classify(result.returncode)
            if verdict is not Verdict.OK:
                status = CRASH if verdict is Verdict.CRASH else FAIL
                results.append(CheckResult(
                    label, status,
                    f"{label}: adapter did not exit OK (returncode={result.returncode})\n"
                    f"stderr: {result.stderr.strip()}",
                ))
                continue

            if answer.kind == "json":
                results.append(self._compare_json(case, answer, label, out_dir, input_path))
            elif answer.kind == "svg":
                results.append(self._compare_svg(case, answer, label, out_dir, input_path))
            elif answer.kind == "file":
                results.append(self._compare_file(case, answer, label, out_dir, input_path))
            elif answer.kind == "dir":
                results.append(self._compare_dir(case, answer, label, out_dir, input_path))
            else:
                raise ValueError(f"answer kind {answer.kind!r} has no comparator")

        if not results:
            return CaseResult(case=case, skipped=True, skip_reason="no answer's verb is supported by this adapter")
        return CaseResult(case=case, check_results=results)

    def _compare_json(self, case: Case, answer: Answer, label: str, out_dir: Path, input_path: Path) -> CheckResult:
        artifact = answer.artifact(out_dir, input_path)
        expected_root = case.expected_dir(self.version)
        expected_path = expected_root / answer.expected_name

        if not artifact.exists():
            return CheckResult(label, FAIL, f"{label}: adapter did not write expected artifact at {artifact}")
        actual = answer.reduce(artifact)

        if self.regenerate and not answer.compare_only:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return CheckResult(label, REGENERATED, f"{label}: wrote {expected_path}")
        if not expected_path.exists():
            return CheckResult(label, NEEDS_REGEN, f"{label}: expected file {expected_path} missing -- run --regenerate")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        if actual == expected:
            return CheckResult(label, PASS)
        notes = reduce.describe_structured_mismatch(expected, actual)
        return CheckResult(label, FAIL, f"{label}: mismatch vs {expected_path}: {'; '.join(notes)}")

    def _compare_svg(self, case: Case, answer: Answer, label: str, out_dir: Path, input_path: Path) -> CheckResult:
        # render (DESIGN.md §3c): KiCad-vs-KiCad is a normalized-SVG BYTE-EXACT
        # compare, zero tolerance, no rasterizer -- the cross-impl raster path (pinned
        # `resvg`) is deferred to M6 (DL-0021).
        artifact = answer.artifact(out_dir, input_path)
        expected_root = case.expected_dir(self.version)
        expected_path = expected_root / answer.expected_name

        if not artifact.exists():
            return CheckResult(label, FAIL, f"{label}: adapter did not write expected SVG at {artifact}")
        actual_bytes = normalize.normalize_svg(artifact.read_bytes())

        if self.regenerate:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_bytes(actual_bytes)
            return CheckResult(label, REGENERATED, f"{label}: wrote {expected_path}")
        if not expected_path.exists():
            return CheckResult(label, NEEDS_REGEN, f"{label}: expected file {expected_path} missing -- run --regenerate")
        expected_bytes = normalize.normalize_svg(expected_path.read_bytes())
        if actual_bytes == expected_bytes:
            return CheckResult(label, PASS)
        return CheckResult(label, FAIL, f"{label}: render (SVG) mismatch vs {expected_path} (normalized-SVG byte compare)")

    def _compare_file(self, case: Case, answer: Answer, label: str, out_dir: Path, input_path: Path) -> CheckResult:
        # pdf/dxf (DL-0037): a plain byte compare after the generic per-suffix
        # normalizer (`normalize.normalize_for`), the same shape as `_compare_svg` but
        # not hardcoded to `normalize_svg` -- PDF/DXF each dispatch to their own (or no)
        # normalizer by the artifact's extension.
        artifact = answer.artifact(out_dir, input_path)
        expected_root = case.expected_dir(self.version)
        expected_path = expected_root / answer.expected_name

        if not artifact.exists():
            return CheckResult(label, FAIL, f"{label}: adapter did not write expected artifact at {artifact}")
        actual_bytes = normalize.normalize_for(artifact, artifact.read_bytes())

        if self.regenerate:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_bytes(actual_bytes)
            return CheckResult(label, REGENERATED, f"{label}: wrote {expected_path}")
        if not expected_path.exists():
            return CheckResult(label, NEEDS_REGEN, f"{label}: expected file {expected_path} missing -- run --regenerate")
        expected_bytes = normalize.normalize_for(expected_path, expected_path.read_bytes())
        if actual_bytes == expected_bytes:
            return CheckResult(label, PASS)
        return CheckResult(label, FAIL, f"{label}: {label} mismatch vs {expected_path} (normalized byte compare)")

    def _compare_dir(self, case: Case, answer: Answer, label: str, out_dir: Path, input_path: Path) -> CheckResult:
        # gerbers/, drill/, library render/ (DESIGN.md §3d, TEST_CASE_FORMAT.md
        # §2): a directory answer is compared as a whole -- same filenames present, every
        # file byte-identical after normalization. Absence of any file is a FAILURE, not
        # a skip (TEST_CASE_FORMAT.md §2: "there is no mechanism for 'this answer is
        # legitimately absent'").
        artifact_dir = answer.artifact(out_dir, input_path)
        expected_root = case.expected_dir(self.version)
        expected_dir = expected_root / answer.expected_name

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
        actual_names, expected_names = set(actual_files), set(expected_files)
        missing = expected_names - actual_names
        extra = actual_names - expected_names
        if missing or extra:
            detail = f"{label}: file set mismatch vs {expected_dir}"
            if missing:
                detail += f"\n  missing (expected but not produced): {sorted(missing)}"
            if extra:
                detail += f"\n  unexpected (produced but not expected): {sorted(extra)}"
            return CheckResult(label, FAIL, detail)

        mismatches = []
        for rel in sorted(actual_names):
            a = normalize.normalize_for(actual_files[rel], actual_files[rel].read_bytes())
            e = normalize.normalize_for(expected_files[rel], expected_files[rel].read_bytes())
            if a != e:
                mismatches.append(rel)
        if mismatches:
            return CheckResult(label, FAIL, f"{label}: {len(mismatches)} file(s) differ vs {expected_dir}: {mismatches}")
        return CheckResult(label, PASS)

    # --- rejection case: the type's loader, exit + stderr + control -----------------

    def _run_failure_case(self, case: Case, tmp_root: Path) -> CaseResult:
        input_paths = case.input_paths
        input_path = input_paths[0]
        kind = input_kind(input_path)
        # DL-0029: `known_divergence.probe`, when set, overrides the derived loader verb
        # -- the sole use is `rejects-unterminated-sexpr` (DIV-0001) pinning itself to the
        # old `parse-pcb-upgrade` verb (`pcb upgrade --force`) so it keeps exercising the
        # segfault that `parse-pcb`'s default probe (`pcb export stats`) no longer reaches.
        probe_override = case.known_divergence.probe if case.known_divergence else None
        verb = probe_override or LOADER_VERB[kind]
        label = verb

        if not self.adapter.supports(verb):
            return CaseResult(case=case, skipped=True, skip_reason=f"adapter does not support verb {verb!r}")

        out_dir = tmp_root / "main"
        # Every declared input, not just the first (DL-0032 audit fix, same class of bug
        # as `cmd_erc`'s single-copy issue): no rejection case ships a multi-sheet
        # `inputs`/`root` today, so this is behaviourally identical to the old
        # single-input call for every existing case, but a future hierarchical rejection
        # case would otherwise silently lose its sub-sheets here even after the adapter
        # itself was fixed to copy every `--in` it's given.
        result = self.adapter.invoke(verb, input_paths, out_dir, root=case.root)
        satisfied, fail_status, detail = self._exit_condition(case, result, label)

        # Positive control (DESIGN §3a, DL-0013): every failure case must be shown
        # falsifiable. Run it even when the main check already failed/crashed, so the
        # control machinery is exercised (and visible in the report) on every rejection case
        # case, never just the ones whose main check happens to reject cleanly.
        control_ok, control_note = self._check_control(case, verb, label, tmp_root)
        if satisfied and not control_ok:
            return CaseResult(case=case, check_results=[CheckResult(label, NOT_EVIDENCE, control_note)])

        kd = case.known_divergence
        if kd is not None and control_ok:
            xfail_xpass = self._score_known_divergence(kd, satisfied, result, label, control_note)
            if xfail_xpass is not None:
                return CaseResult(case=case, check_results=[xfail_xpass])

        if not satisfied:
            if control_note:
                detail = f"{detail}\n{control_note}"
            return CaseResult(case=case, check_results=[CheckResult(label, fail_status, detail)])

        return CaseResult(case=case, check_results=[CheckResult(label, PASS, control_note)])

    def _exit_condition(self, case: Case, result, label: str) -> tuple[bool, str, str]:
        """Apply the `exit` polarity/substring rule (DESIGN §3a) for a rejection case --
        the tool must reject (a graceful, non-crashing non-zero exit) and, if declared,
        stderr must contain the asserted substring(s)."""
        verdict = classify(result.returncode)
        if verdict is Verdict.OK:
            return False, FAIL, f"{label}: expected error, tool exited 0"
        if verdict is Verdict.CRASH:
            return False, CRASH, (
                f"{label}: adapter CRASHED (returncode={result.returncode}) instead of a "
                f"graceful rejection -- CRASH is never a pass, even for a rejection case "
                f"(DL-0013). stderr: {result.stderr.strip()}"
            )
        # REJECT: check substring assertions
        stderr = result.stderr
        if case.error_contains and case.error_contains not in stderr:
            return False, FAIL, f"{label}: stderr did not contain {case.error_contains!r}\nstderr: {stderr.strip()}"
        if case.error_contains_any and not any(s in stderr for s in case.error_contains_any):
            return False, FAIL, f"{label}: stderr did not contain any of {case.error_contains_any!r}\nstderr: {stderr.strip()}"
        return True, "", ""

    def _score_known_divergence(self, kd, satisfied: bool, result, label: str, control_note: str) -> Optional[CheckResult]:
        """Reinterpret an already-classified verdict as a declared, tracked oracle
        divergence (DL-0018). Returns `None` when the declaration doesn't apply (e.g. a
        genuine unrelated failure), in which case the caller falls through to ordinary
        FAIL/CRASH reporting."""
        if satisfied:
            detail = (
                f"{label}: XPASS -- known divergence ({kd.kind}) no longer reproduces: "
                f"the adapter returned a clean/expected result instead of the declared "
                f"{kd.kind}. Update docs/DIVERGENCES.md and remove the `known_divergence` "
                f"marker from case.toml.\n  declared reason: {kd.reason}"
            )
            if kd.tracking:
                detail += f"\n  tracking: {kd.tracking}"
            return CheckResult(label, XPASS, detail)

        verdict = classify(result.returncode)
        if kd.kind == "crash" and verdict is Verdict.CRASH:
            detail = f"{label}: XFAIL (known divergence, kind={kd.kind}) -- {kd.reason}"
            if kd.tracking:
                detail += f" [tracking: {kd.tracking}]"
            if control_note:
                detail += f"\n{control_note}"
            return CheckResult(label, XFAIL, detail)

        # Bad, but not the *declared* kind of bad -- report it as a normal failure so a
        # different, undeclared regression is never laundered through the divergence
        # marker.
        return None

    def _check_control(self, case: Case, verb: str, label: str, tmp_root: Path) -> tuple[bool, str]:
        """Returns (control_reached_ok, human-readable note) -- never raises, always
        runs, regardless of the main check's own outcome (see call site above)."""
        if case.control is None:
            return False, (
                f"{label}: failure case has no positive control (`control =`); "
                f"\"a test that can't fail is not evidence\" (DL-0013)"
            )
        control_path = case.path / case.control
        if not control_path.exists():
            return False, f"{label}: control fixture {case.control!r} does not exist"

        control_out = tmp_root / "control"
        control_result = self.adapter.invoke(verb, [control_path], control_out)
        verdict = classify(control_result.returncode)
        if verdict is not Verdict.OK:
            return False, (
                f"{label}: positive control {case.control!r} did not exit OK "
                f"(verdict={verdict.value}, returncode={control_result.returncode}) -- "
                f"the defect-free variant must succeed, or the failure isn't evidence "
                f"of the specific defect (DL-0013)\nstderr: {control_result.stderr.strip()}"
            )
        return True, f"{label}: positive control {case.control!r} exited OK, as required (DL-0013)"


def make_tmp_root() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="kicad-conformance-")
