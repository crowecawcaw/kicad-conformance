#!/usr/bin/env python3
"""The reference adapter: wraps `kicad-cli` behind the kicad-conformance verb protocol
(DESIGN.md §2, §2a, §2b). This is the "reference adapter" DL-0007 describes — an
ordinary executable, invoked as a subprocess, so a non-Python implementation-under-test
can satisfy the exact same contract.

Invocation (matches DESIGN §2's `<adapter> <verb> --in <path...> --out <dir> [flags]`):

    kicad.py <verb> --in PATH [--in PATH ...] --out DIR [--root NAME] [--format FMT] \
             [EXTRA-ARGS...]

- `--in` may repeat (multi-sheet schematic: root + subsheets).
- `--out` is always an explicit path the runner dictates (§2a) — this adapter never
  relies on kicad-cli's derived-filename default.
- `--root` names which `--in` is the netlist root sheet (only meaningful for `netlist`).
- `--format` selects the schematic interchange format for `summary`/`netlist`
  (`kicadsexpr`, the default, or `kicadxml`).
- Trailing unrecognized tokens are forwarded verbatim to kicad-cli. This remains part of
  the adapter's own protocol (a generic escape hatch, DESIGN §2), but since [DL-0025] a
  `case.toml` has no `args` field to populate it with -- every choice this used to carry
  (which layer to render, which flags to pin) is now a fixed harness decision baked into
  this adapter (§2b).
- `version` and `capabilities` are meta-verbs answered directly by this script, not by
  shelling out per-file.

Locale/timezone: this adapter does NOT set LC_ALL/TZ itself — the runner sets them in
the environment it launches the adapter with (DESIGN §4), and they are inherited here.

Crash relay (see `runner/engine.py`'s `Verdict`/`classify` for the rationale): if
kicad-cli is killed by a signal, this adapter re-raises the identical signal against
itself before exiting, so the runner — whose direct subprocess child is this adapter,
not kicad-cli — still observes a genuinely signaled child (a negative `returncode`), not
a laundered "normal" exit code.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path

# Make `import runner....` work when this file is invoked directly as a script (its own
# directory, not the repo root, is on sys.path[0] by default). This file lives at
# <repo-root>/adapters/kicad.py -- one level up is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runner import summary as summarymod  # noqa: E402

# The verbs this adapter implements, answered by the `capabilities` meta-verb (DESIGN.md
# §2). `export-step` is reserved-but-unused (DL-0012) and deliberately excluded so it
# skip-and-counts rather than pretending to run. This used to live in a shared
# `runner/verbs.py` table with the coverage proxy; the proxy is gone (it printed one line
# nobody read), so this is now a plain literal, owned entirely by this adapter.
IMPLEMENTED_VERBS = (
    "version", "parse-sch", "parse-pcb", "parse-pcb-upgrade", "parse-sym", "parse-fp",
    "summary", "erc", "drc", "drc-refill-zones", "drc-parity", "netlist", "pos", "stats",
    "ipcd356", "render", "export-gerbers", "export-drill", "export-pdf", "export-dxf",
)


def discover_kicad_cli() -> str:
    """KICAD_CLI env -> PATH -> per-OS install dirs, newest-numeric-version first."""
    env = os.environ.get("KICAD_CLI")
    if env:
        return env
    found = shutil.which("kicad-cli")
    if found:
        return found
    if sys.platform.startswith("win"):
        candidates = glob(r"C:\Program Files\KiCad\*\bin\kicad-cli.exe")
    elif sys.platform == "darwin":
        candidates = glob("/Applications/KiCad*/kicad-cli") + glob(
            "/Applications/KiCad/*/kicad-cli"
        )
    else:
        candidates = glob("/usr/lib/kicad*/bin/kicad-cli") + glob(
            "/opt/kicad*/bin/kicad-cli"
        )
    candidates.sort(reverse=True)
    if candidates:
        return candidates[0]
    print(
        "kicad-cli not found (checked KICAD_CLI env, PATH, common install dirs)",
        file=sys.stderr,
    )
    sys.exit(127)


def _run_step(argv: list[str]) -> int:
    """Run kicad-cli and return its raw returncode, without relaying/exiting -- used
    when several subcommands must run in sequence (`summary`) and only the FIRST failure
    should end this adapter process."""
    return subprocess.run(argv).returncode


def _relay_and_exit(rc: int) -> None:
    """End this adapter process with termination semantics matching `rc` (see module
    docstring's "Crash relay")."""
    if rc < 0:
        sig = -rc
        try:
            os.kill(os.getpid(), sig)
        except OSError:
            pass
        sys.exit(128 + sig)  # fallback; os.kill above should already have ended us
    sys.exit(rc)


def run_and_relay(argv: list[str]) -> None:
    """Run kicad-cli; end this adapter process with matching termination semantics."""
    _relay_and_exit(_run_step(argv))


def parse_argv(argv: list[str]):
    if not argv:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    verb = argv[0]
    ins: list[str] = []
    out: str | None = None
    root: str | None = None
    fmt: str | None = None
    extra: list[str] = []
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--in":
            i += 1
            ins.append(argv[i])
        elif tok == "--out":
            i += 1
            out = argv[i]
        elif tok == "--root":
            i += 1
            root = argv[i]
        elif tok == "--format":
            i += 1
            fmt = argv[i]
        else:
            extra.append(tok)
        i += 1
    return verb, ins, out, root, fmt, extra


def cmd_version(cli: str, fmt: str | None) -> None:
    proc = subprocess.run(
        [cli, "version", "--format", fmt or "plain"], capture_output=True, text=True
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


def cmd_capabilities() -> None:
    print(json.dumps(sorted(IMPLEMENTED_VERBS)))
    sys.exit(0)


def _fresh_scratch_dir() -> Path:
    """A scratch dir OUTSIDE `--out` (DESIGN §2a's artifact directory). Read-only verbs
    still copy their input here before invoking kicad-cli -- see `_scratch_copy` --
    specifically so a scratch input copy is never mistaken for part of the artifact set
    `export-gerbers`/`export-drill` treat their entire `--out` directory as (exit-only,
    but the same isolation habit applies) and never lands inside a tree a comparator
    might one day walk."""
    return Path(tempfile.mkdtemp(prefix="kicad-adapter-src-"))


def _scratch_copy(src: str, scratch_dir: Path) -> Path:
    """Copy a single input file into a scratch dir and return the copy's path.

    This is used for EVERY verb, not just the rewrite-in-place `parse-*` ones: kicad-cli
    has been observed to write a `.kicad_prl` project-local-settings cache next to a
    board it merely READS (e.g. `pcb drc`) as a side effect. Operating on a scratch copy
    -- never on the committed fixture path under `suites/` -- keeps every such side
    effect out of the corpus.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    dest = scratch_dir / Path(src).name
    shutil.copyfile(src, dest)
    return dest


def _scratch_copy_tree(src: str, scratch_dir: Path) -> Path:
    """Same idea as `_scratch_copy`, for a directory input (a `.pretty` footprint lib)."""
    dest = scratch_dir / Path(src).name
    shutil.copytree(src, dest)
    return dest


# Sibling files a board case may ship alongside `board.kicad_pcb`, recognized by same
# stem in the SAME source directory (never declared in `case.toml`'s `input`/`inputs` --
# discovered on disk, exactly like the gerber `%TF.ProjectId` filename convention).
# `.kicad_dru` (custom DRC rules) and `.kicad_pro` (project settings, incl. per-check
# severity overrides) are both read by `pcb drc` with NO path flag of their own in
# 10.0.5 -- same-stem-same-directory is the ONLY way either reaches it. `.kicad_sch` is
# the same idea for `--schematic-parity` (`cmd_drc_parity`).
_BOARD_SIBLING_SUFFIXES = (".kicad_dru", ".kicad_pro", ".kicad_sch")


def _scratch_copy_board(src: str, scratch_dir: Path) -> Path:
    """Copy a board input into scratch, THEN copy any recognized same-stem sibling
    (`_BOARD_SIBLING_SUFFIXES`) found next to it in the source directory, under the
    scratch copy's own stem -- preserving the name-matching convention every sibling
    lookup depends on.

    This closes a real, verified gap: `_scratch_copy` (used by every board verb until
    this change) copies exactly the one named file, so a `.kicad_dru`/`.kicad_pro`/
    `.kicad_sch` sitting beside a committed `board.kicad_pcb` never reached `kicad-cli` at
    all -- `drc_rule_parser.cpp` was 0/505 lines for exactly this reason (docs/COVERAGE.md).
    Verified empirically, not assumed:

    - `.kicad_dru`: adding a same-stem custom rule (`(rule "my-wide-clearance"
      (constraint clearance (min 25mm)) ...)`) to `board-parse/populated-board`'s board
      turns 4 DRC violations into 9 -- ZERO effect without this copy in place (confirmed by
      running `pcb drc` on the same two directories, with and without the sibling
      present).
    - `.kicad_pro`: a same-stem `{"board": {"design_settings": {"rule_severities":
      {"track_dangling": "ignore", ...}}}}` turns 4 violations into 2, even with
      `--severity-all` passed -- that flag broadens which SEVERITIES are reported, it does
      not un-ignore a check the project turned off entirely.
    - `.kicad_sch`: `pcb drc --schematic-parity` with no same-stem schematic present
      prints "Failed to fetch schematic netlist for parity tests." and exits 0 with an
      EMPTY `schematic_parity: []` (a silent no-op, not a loud failure); with one present
      it reports real `missing_footprint`/`extra_footprint` findings.

    Copying all three unconditionally (rather than gating on which verb/extra is
    running) was verified harmless for every OTHER board answer (`summary`, plain `drc`,
    `render`, gerbers, drill, `pos`/`stats`/`ipcd356`) -- kicad-cli only consults the
    sibling that a given check actually uses, so an inert stray file alongside the board
    changes nothing for those. Rejection-case loaders (`parse-pcb`/`parse-pcb-upgrade`)
    also use this for consistency -- none of today's `rejects-*` board cases ship a
    sibling, so this is a no-op for them today, not a behaviour change.
    """
    dest = _scratch_copy(src, scratch_dir)
    src_path = Path(src)
    for suffix in _BOARD_SIBLING_SUFFIXES:
        sibling = src_path.with_suffix(suffix)
        if sibling.exists():
            shutil.copyfile(sibling, scratch_dir / (dest.stem + suffix))
    return dest


def _scratch_copy_all(ins: list[str], scratch_dir: Path, root: str | None) -> Path:
    """Copy EVERY `--in` (root + subsheets) into ONE scratch dir, preserving filenames --
    sufficient for same-directory multi-sheet projects (the common case) -- and return the
    scratch copy of the declared `root` (or `ins[0]` if `root` is unset or names nothing
    in `ins`).

    This is what makes a multi-sheet schematic's `summary`/`netlist` cover the WHOLE
    hierarchy instead of silently just the root sheet: `sch export netlist` resolves a
    sub-sheet's `(sheet ... (property "Sheetfile" "sub.kicad_sch"))` reference relative to
    the file it is run against, so every sub-sheet must actually be present on disk next
    to the root's scratch copy, under its real filename, or kicad-cli cannot find it.
    Copying only `ins[0]` (the bug this replaces) either makes kicad-cli fail to resolve
    the missing sub-sheet, or -- depending on how the defect is triggered -- silently
    export a netlist covering only the root sheet, which then compares "clean" against a
    summary that never saw the sub-sheet's components or the net crossing the sheet
    boundary. Either failure mode is a case that cannot fail is not evidence (DL-0013's
    reasoning applied to `inputs`/`root`, not just to rejection-case controls)."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    root_name = root or Path(ins[0]).name
    root_dest = None
    for src in ins:
        dest = scratch_dir / Path(src).name
        shutil.copyfile(src, dest)
        if Path(src).name == root_name:
            root_dest = dest
    if root_dest is None:
        root_dest = scratch_dir / Path(ins[0]).name
    return root_dest


def cmd_parse(cli: str, kind: str, ins: list[str], out: str, root: str | None = None) -> None:
    """parse-sch: `sch upgrade --force` rewrites IN PLACE, so copy the fixture to a
    scratch dir first and upgrade the copy (§2, §2a). Exit polarity only (DL-0024) --
    the re-emitted bytes are never compared against anything.

    Copies EVERY declared `--in` (root + subsheets), not just the first, via
    `_scratch_copy_all` -- the same single-vs-all audit `cmd_erc` needed (module
    docstring): no rejection case ships a multi-sheet `inputs`/`root` today, so this is
    behaviourally identical to the old single-file copy for every existing case, but
    there is no reason this verb should be the one exception if one ever does.

    `kind == "pcb"` is NOT handled here any more (DL-0029, DECISIONS.md) -- see
    `cmd_parse_pcb`/`cmd_parse_pcb_upgrade` below."""
    out_dir = Path(out)
    dest = _scratch_copy_all(ins, out_dir, root)
    run_and_relay([cli, kind, "upgrade", "--force", str(dest)])


def cmd_parse_pcb(cli: str, ins: list[str], out: str) -> None:
    """`parse-pcb`, since [DL-0029]: the board-loader probe for every rejection case is
    `pcb export stats`, not `pcb upgrade --force`. `pcb upgrade --force` was verified
    (two independent runs, 8/8 and 10/10 malformed boards) to SIGSEGV on every board it
    fails to load, always immediately after printing the correct `Failed to load board:
    ...` message -- so it can never distinguish "the harness has a graceful-rejection
    bug" from "kicad-cli always crashes here" (a crash is never a pass, DL-0013, so every
    such case scored a strict xfail rather than the genuine PASS its concept describes).
    `pcb export stats` was verified to reject the identical bytes gracefully, exit 3,
    with the same `Failed to load board: ...` message on stderr, on every one of those
    boards -- while still requiring a fully-parsed board to succeed on the accept path
    (same as `upgrade` did), so it remains a faithful "does this board load" probe. Exit
    polarity only, same as before -- the written `stats.json` is discarded, never
    compared against anything."""
    out_dir = Path(out)
    dest = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "stats", "--format", "json",
         "-o", str(out_dir / "stats.json"), str(dest)]
    )


def cmd_parse_pcb_upgrade(cli: str, ins: list[str], out: str) -> None:
    """`parse-pcb-upgrade`: the OLD `parse-pcb` probe (`pcb upgrade --force`), kept as its
    own verb solely so `suites/board-parse/rejects-unterminated-sexpr` (DIV-0001,
    docs/DIVERGENCES.md) can keep deliberately exercising the `pcb upgrade --force`
    SIGSEGV documented there, via `case.toml`'s `known_divergence.probe` override
    (DL-0029) -- since the default `parse-pcb` probe moved to `pcb export stats` (which
    does NOT crash on that fixture), nothing would exercise this crash any more without
    a case explicitly asking for the old path. Not part of any case's standard battery;
    not advertised as a thing a normal case should reach for."""
    out_dir = Path(out)
    dest = _scratch_copy_board(ins[0], out_dir)
    run_and_relay([cli, "pcb", "upgrade", "--force", str(dest)])


def cmd_parse_sym(cli: str, ins: list[str], out: str) -> None:
    """`--out` must exist before `sym upgrade --force -o <out>/<stem>.kicad_sym` runs --
    verified bug (found while authoring `suites/symbol-lib/rejects-unterminated-sexpr`):
    nothing created `--out` here or upstream, so this verb failed on EVERY input, well-
    formed or not, whenever its target directory didn't already exist (which the
    runner's own scratch dirs never do ahead of time) -- exactly why
    `command_sym_upgrade.cpp::doPerform` sat at 0.00% in docs/COVERAGE.md. One-line fix,
    matching what `cmd_render`'s `.kicad_sym` branch already does."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    target = out_dir / (Path(ins[0]).stem + ".kicad_sym")
    run_and_relay([cli, "sym", "upgrade", "--force", "-o", str(target), str(src)])


def cmd_parse_fp(cli: str, ins: list[str], out: str) -> None:
    """fp upgrade takes a `.pretty` DIRECTORY (never a lone `.kicad_mod`) and refuses a
    pre-existing `-o` path, so the target must be a not-yet-created subdirectory.

    `--out` itself must still exist first -- same verified bug as `cmd_parse_sym` above
    (found while authoring `suites/footprint-lib/rejects-unterminated-sexpr`): `fp
    upgrade --force -o <out>/upgraded.pretty` fails with "Directory ... couldn't be
    created" whenever `--out` doesn't already exist, which is exactly why
    `command_fp_upgrade.cpp::doPerform` sat at 0.00%. `upgraded.pretty` itself must NOT
    pre-exist (that's the directory `fp upgrade -o` refuses to overwrite) -- only its
    PARENT (`--out`) needs to."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch_copy_tree(ins[0], _fresh_scratch_dir())
    target = out_dir / "upgraded.pretty"  # must NOT pre-exist
    run_and_relay([cli, "fp", "upgrade", "--force", "-o", str(target), str(src)])


def cmd_erc(cli: str, ins: list[str], out: str, root: str | None) -> None:
    """`sch erc`, DESIGN.md §2. **Copies EVERY declared `--in` (root + subsheets) into
    ONE scratch dir (`_scratch_copy_all`), not the single-file `_scratch_copy` this used
    before** -- verified a REAL bug, not a theoretical one: on a two-sheet schematic with
    U1 (root) and U2 (sub-sheet) tied by a GLOBAL label "SIG", running `sch erc` against
    a scratch dir holding ONLY the root sheet reports 4 violations INCLUDING A FALSE
    ONE -- `isolated_pin_label` on the global label, because the sub-sheet's matching pin
    is invisible to it -- and says nothing about U2's own violations. With both sheets
    copied, it reports the correct 6: no false isolated-label warning, plus U2's real
    `endpoint_off_grid`/`lib_symbol_issues` findings. So the single-copy bug does not just
    miss things silently, it FABRICATES a wrong finding -- same DESIGN.md §9 reasoning as
    `cmd_netlist`/`cmd_summary`'s sch branch, now applied here too."""
    out_dir = Path(out)
    root_dest = _scratch_copy_all(ins, _fresh_scratch_dir(), root)
    run_and_relay(
        [cli, "sch", "erc", "--format", "json", "--severity-all",
         "-o", str(out_dir / "erc.json"), str(root_dest)]
    )


def cmd_drc(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
         "-o", str(out_dir / "drc.json"), str(src)]
    )


def cmd_drc_refill_zones(cli: str, ins: list[str], out: str) -> None:
    """New verb: `drc-refill-zones` -- `pcb drc --refill-zones` (DESIGN.md §2 table).
    Every committed zone fixture ships a pre-baked `(filled_polygon ...)`, so plain `drc`
    never invokes `ZONE_FILLER::Fill` at all -- `pcbnew/zone_filler.cpp` is 0/1991 lines
    in docs/COVERAGE.md, the single largest dead non-GUI subsystem in the whole tree.

    Verified empirically, not just documented: took a board with one rectangular zone
    whose `filled_polygon` was hand-corrupted to a much smaller, stale rectangle: running
    `pcb drc --refill-zones --save-board` rewrote the on-disk `filled_polygon` back to
    the correct full-outline polygon (with the filler's own corner rounding) -- proof the
    filler genuinely re-runs and recomputes geometry rather than echoing whatever was
    stored. Determinism verified too: run twice on an uncorrupted zone fixture, the
    reduced violation set is identical both times (raw JSON differs only in the
    `date` field `reduce_drc` already drops).

    A distinct verb rather than a flag on plain `drc`, per DL-0025 (no per-case CLI args)
    -- see the `refill-zones` extra in `runner/engine.py`."""
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
         "--refill-zones", "-o", str(out_dir / "refill-zones.json"), str(src)]
    )


def cmd_drc_parity(cli: str, ins: list[str], out: str) -> None:
    """New verb: `drc-parity` -- `pcb drc --schematic-parity` (DESIGN.md §2 table).
    `drc_test_provider_schematic_parity.cpp` is 9/198 in docs/COVERAGE.md, essentially
    unreached: no case ever passed `--schematic-parity` and no board case ever shipped a
    schematic alongside it.

    Verified: `--schematic-parity` takes NO path flag of its own -- it looks for a
    schematic with the BOARD's own stem, next to the board (same same-stem-same-directory
    convention as `.kicad_dru`/`.kicad_pro`, see `_scratch_copy_board`, which this verb
    reuses so the sibling schematic reaches scratch). Without one present it prints
    "Failed to fetch schematic netlist for parity tests." and still exits 0 with an EMPTY
    `schematic_parity: []` -- a silent no-op, not a loud failure, so a case that names its
    schematic sibling wrong degrades quietly; name it `<board-stem>.kicad_sch`. With one
    present it reports real `missing_footprint`/`extra_footprint` findings (each carrying
    the component ref/value in its `description`, e.g. "Missing footprint U1 (T2)" --
    see `runner/reduce.py`'s restored `description` field, DL-0035)."""
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
         "--schematic-parity", "-o", str(out_dir / "parity.json"), str(src)]
    )


def cmd_netlist(cli: str, ins: list[str], out: str, root: str | None, fmt: str | None) -> None:
    """Copies every `--in` (root + subsheets) into one scratch dir (`_scratch_copy_all`),
    preserving filenames, then runs `sch export netlist` against the root sheet's scratch
    copy.

    `fmt` selects the interchange format (`kicadsexpr`, the default, or `kicadxml` --
    DESIGN.md §3b's cross-format-fairness reader). `-o` always names the same output file
    regardless of format: kicad-cli writes exactly the path it's given, not a
    format-derived extension (verified empirically), so `netlist.net` may contain either
    s-expr or XML text."""
    out_dir = Path(out)
    root_dest = _scratch_copy_all(ins, _fresh_scratch_dir(), root)
    run_and_relay(
        [cli, "sch", "export", "netlist", "--format", fmt or "kicadsexpr",
         "-o", str(out_dir / "netlist.net"), str(root_dest)]
    )


def cmd_export_gerbers(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    """`pcb export gerbers`, byte-compared answers on every board (DESIGN.md §3d,
    DL-0026). **No `--layers`** (KiCad plots the board's own stored set, or its built-in
    default) and **no `--no-protel-ext`** -- the verified command is exactly this, and
    passing `--no-protel-ext` would switch every per-layer extension away from the
    Protel-style ones (`.gtl`/`.gbl`/`.gm1`/...) actually observed."""
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "gerbers", *extra, "-o", str(out_dir) + "/", str(src)]
    )


def cmd_export_drill(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    """`pcb export drill`, byte-compared answer on every board (DESIGN.md §3d,
    DL-0026). No `--generate-map`, no `--generate-report` (its "Created on" stamp has no
    input to normalize -- DESIGN.md §4's fourth non-normalizer), no
    `--excellon-separate-th`: the verified default writes exactly one file, `<stem>.drl`.
    """
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "drill", "-o", str(out_dir) + "/", *extra, str(src)]
    )


def cmd_pos(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "pos", "--format", "csv", "--side", "both",
         "--units", "mm", "-o", str(out_dir / "pos.csv"), *extra, str(src)]
    )


def cmd_stats(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "stats", "--format", "json",
         "-o", str(out_dir / "stats.json"), str(src)]
    )


def cmd_ipcd356(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "ipcd356", "-o", str(out_dir / "board.d356"), str(src)]
    )


def cmd_render(cli: str, ins: list[str], out: str, extra: list[str], root: str | None = None) -> None:
    """`render` dispatches on the input's suffix (DESIGN.md §2, DL-0022): one board
    layer, one schematic sheet, one symbol, one footprint library -- all the same verb,
    all the same normalized-SVG-byte-exact comparison."""
    out_dir = Path(out)
    input_path = Path(ins[0])
    suffix = input_path.suffix
    if suffix == ".kicad_pcb":
        # `--layers F.Cu` is now a fixed harness decision, not a per-case parameter
        # (DESIGN.md §6.2, DL-0025 -- `args` is gone): F.Cu is the one layer every
        # board has, and the gerbers already cover every other layer byte-exactly. The
        # rest is pinned for determinism: `--page-size-mode 2` (board-area only),
        # `--exclude-drawing-sheet`, `--black-and-white`.
        src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
        run_and_relay(
            [cli, "pcb", "export", "svg", "--layers", "F.Cu", "--page-size-mode", "2",
             "--exclude-drawing-sheet", "--black-and-white", *extra,
             "-o", str(out_dir / "render.svg"), str(src)]
        )
    elif suffix == ".kicad_sch":
        # `sch export svg` writes `<out>/<stem>.svg` (a directory `-o`, not a file path).
        # `--exclude-drawing-sheet --black-and-white` per DESIGN.md §2's verified
        # invocation (matches the board/library renders' determinism pinning). Copies
        # every `--in`, not just the root (`_scratch_copy_all`), so a hierarchical
        # sheet's sub-sheet files are on disk if kicad-cli needs to resolve them.
        out_dir.mkdir(parents=True, exist_ok=True)
        src = _scratch_copy_all(ins, _fresh_scratch_dir(), root)
        run_and_relay(
            [cli, "sch", "export", "svg", "--exclude-drawing-sheet", "--black-and-white",
             *extra, "-o", str(out_dir) + "/", str(src)]
        )
    elif suffix == ".kicad_sym":
        out_dir.mkdir(parents=True, exist_ok=True)
        src = _scratch_copy(ins[0], _fresh_scratch_dir())
        run_and_relay(
            [cli, "sym", "export", "svg", "--black-and-white", *extra,
             "-o", str(out_dir) + "/", str(src)]
        )
    else:
        # `.pretty` LIBRARY DIRECTORY (never a lone `.kicad_mod`), like `parse-fp`.
        out_dir.mkdir(parents=True, exist_ok=True)
        src = _scratch_copy_tree(ins[0], _fresh_scratch_dir())
        run_and_relay(
            [cli, "fp", "export", "svg", "--black-and-white", *extra,
             "-o", str(out_dir) + "/", str(src)]
        )


def cmd_export_pdf(cli: str, ins: list[str], out: str, root: str | None) -> None:
    """New verb: `export-pdf`. `common/plotters/PDF_plotter.cpp` is 0/1433 in
    docs/COVERAGE.md (plus `pdf_outline_font.cpp` 0/327, `pdf_stroke_font.cpp` 0/298) --
    entirely unreached, because no `export-pdf` verb or `pdf` extra existed. Dispatches
    on the input suffix like `render`: a board plots the SAME single fixed layer
    (`F.Cu`, DESIGN.md §2b) to a single-file PDF (`--mode-single`, verified: with that
    flag `-o` is a full file path, not a directory); a schematic plots its whole
    hierarchy (root + every declared subsheet, via `_scratch_copy_all` so a sub-sheet
    page resolves) to one multi-page PDF -- `sch export pdf`'s `-o` is already a FILE
    path (verified: unlike `sch export svg`, no directory-vs-file gotcha here).

    Determinism, verified in Docker (run twice, 2s apart, FIXED output filename both
    times so the embedded `/Title` never varies): the ONLY byte difference across the
    two runs is `/CreationDate (D:...)` -- kicad-cli 10.0.5's PDF output has no `/ModDate`
    and no `/ID` trailer entry at all in this plot mode (contrary to the general "PDF is
    the least diffable format" caution -- that caution is about PDF as a format in
    general, not falsified, but empirically not everything it warns about is present
    here). `runner/normalize.py`'s `normalize_pdf` strips exactly that one field, and
    does NOT also run the generic CRLF->LF pass every other text answer gets -- PDF is
    binary and a blind CRLF rewrite could corrupt a compressed content stream that
    happens to contain that byte pair."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(ins[0])
    suffix = input_path.suffix
    if suffix == ".kicad_pcb":
        src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
        run_and_relay(
            [cli, "pcb", "export", "pdf", "--layers", "F.Cu", "--mode-single",
             "--black-and-white", "-o", str(out_dir / "pdf.pdf"), str(src)]
        )
    elif suffix == ".kicad_sch":
        root_dest = _scratch_copy_all(ins, _fresh_scratch_dir(), root)
        run_and_relay(
            [cli, "sch", "export", "pdf", "--exclude-drawing-sheet", "--black-and-white",
             "-o", str(out_dir / "pdf.pdf"), str(root_dest)]
        )
    else:
        print(
            f"export-pdf: does not apply to {suffix or '(directory)'!r} input",
            file=sys.stderr,
        )
        sys.exit(2)


def cmd_export_dxf(cli: str, ins: list[str], out: str) -> None:
    """New verb: `export-dxf` -- board-only (kicad-cli has no `sch export dxf`).
    `common/plotters/DXF_plotter.cpp` is 0/651 in docs/COVERAGE.md, entirely unreached
    (no `export-dxf` verb or `dxf` extra existed).

    Verified BYTE-IDENTICAL across two runs, 2s apart, same fixture: no creation date,
    no embedded filename/title, nothing wall-clock at all in kicad-cli 10.0.5's DXF
    output. Per the honesty rule (DESIGN.md §4: "an identity normalizer would imply a
    nondeterminism that does not exist"), this gets NO dedicated normalizer -- only the
    generic CRLF->LF fallback every otherwise-unrecognized text answer already gets from
    `runner/normalize.py`'s `normalize_for`."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(ins[0])
    if input_path.suffix != ".kicad_pcb":
        print(
            f"export-dxf: does not apply to {input_path.suffix or '(directory)'!r} "
            f"input -- board-only (kicad-cli has no `sch export dxf`)",
            file=sys.stderr,
        )
        sys.exit(2)
    src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "dxf", "--layers", "F.Cu", "--mode-single",
         "--output-units", "mm", "-o", str(out_dir / "dxf.dxf"), str(src)]
    )


def cmd_summary(cli: str, ins: list[str], out: str, root: str | None, fmt: str | None) -> None:
    """`summary` (DESIGN.md §3b, DL-0022, renamed by DL-0028) dispatches on the input's
    suffix and composes the resulting export(s) into one merged `<out>/summary.json`,
    written by THIS adapter (DESIGN §2's "composition happens in the adapter") -- the
    runner only ever reads the merged document back, never the intermediate
    `stats.json`/`pos.csv`/`board.d356`/`netlist.net` files.

    A board runs all three exports in sequence and relays the FIRST failure exactly as
    `run_and_relay` would (crash relay included). A schematic runs one export, in the
    format `fmt` names (`kicadsexpr` default, or `kicadxml` -- DESIGN §3b's
    cross-format-fairness proof: both formats must compose to the identical summary,
    which is what `extra = ["summary-kicadxml"]` asserts) -- against a scratch dir
    holding EVERY declared `--in`, not just the first, so a multi-sheet (`inputs` +
    `root`) schematic's `sch export netlist` walks the whole hierarchy instead of
    silently summarizing the root sheet alone (`_scratch_copy_all`). A `.kicad_sym`/
    `.pretty` input has no structured kicad-cli export to build a summary from (DESIGN
    §4.5) -- the runner rejects it with a clear error instead of inventing a projection.
    """
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(ins[0])
    suffix = input_path.suffix

    if suffix == ".kicad_pcb":
        # Boards are always a single file -- no `root`/subsheet concept applies.
        src = _scratch_copy_board(ins[0], _fresh_scratch_dir())
        scratch = _fresh_scratch_dir()
        stats_path = scratch / "stats.json"
        pos_path = scratch / "pos.csv"
        d356_path = scratch / "board.d356"
        steps = [
            [cli, "pcb", "export", "stats", "--format", "json", "-o", str(stats_path), str(src)],
            [cli, "pcb", "export", "pos", "--format", "csv", "--side", "both",
             "--units", "mm", "-o", str(pos_path), str(src)],
            [cli, "pcb", "export", "ipcd356", "-o", str(d356_path), str(src)],
        ]
        for step in steps:
            rc = _run_step(step)
            if rc != 0:
                _relay_and_exit(rc)
        stats_json = json.loads(stats_path.read_text(encoding="utf-8"))
        pos_text = pos_path.read_text(encoding="utf-8")
        d356_text = d356_path.read_text(encoding="utf-8")
        summary = summarymod.build_board_summary(stats_json, pos_text, d356_text)
    elif suffix == ".kicad_sch":
        # Every `--in` (root + subsheets) lands in ONE scratch dir under its own
        # filename -- a sub-sheet is referenced by name from the root, so it must be on
        # disk next to the root's scratch copy or kicad-cli cannot resolve it, and a
        # missing subsheet is exactly the bug this replaces (module docstring).
        root_dest = _scratch_copy_all(ins, _fresh_scratch_dir(), root)
        scratch = _fresh_scratch_dir()
        net_path = scratch / "netlist.net"
        export_fmt = fmt or "kicadsexpr"
        rc = _run_step(
            [cli, "sch", "export", "netlist", "--format", export_fmt,
             "-o", str(net_path), str(root_dest)]
        )
        if rc != 0:
            _relay_and_exit(rc)
        netlist_text = net_path.read_text(encoding="utf-8")
        summary = summarymod.build_schematic_summary(netlist_text, export_fmt)
    else:
        print(
            f"summary: does not apply to {suffix or '(directory)'!r} input -- "
            f"kicad-cli 10.0.5 offers no structured symbol/footprint export "
            f"(DESIGN.md §4.5); use `render` instead",
            file=sys.stderr,
        )
        sys.exit(2)

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sys.exit(0)


def main() -> int:
    verb, ins, out, root, fmt, extra = parse_argv(sys.argv[1:])

    if verb == "capabilities":
        cmd_capabilities()
        return 0

    cli = discover_kicad_cli()

    if verb == "version":
        cmd_version(cli, fmt)
        return 0

    if out is None or not ins:
        print(f"{verb}: --in and --out are required", file=sys.stderr)
        return 2

    if verb == "parse-sch":
        cmd_parse(cli, "sch", ins, out, root)
    elif verb == "parse-pcb":
        cmd_parse_pcb(cli, ins, out)
    elif verb == "parse-pcb-upgrade":
        cmd_parse_pcb_upgrade(cli, ins, out)
    elif verb == "parse-sym":
        cmd_parse_sym(cli, ins, out)
    elif verb == "parse-fp":
        cmd_parse_fp(cli, ins, out)
    elif verb == "summary":
        cmd_summary(cli, ins, out, root, fmt)
    elif verb == "erc":
        cmd_erc(cli, ins, out, root)
    elif verb == "drc":
        cmd_drc(cli, ins, out)
    elif verb == "drc-refill-zones":
        cmd_drc_refill_zones(cli, ins, out)
    elif verb == "drc-parity":
        cmd_drc_parity(cli, ins, out)
    elif verb == "netlist":
        cmd_netlist(cli, ins, out, root, fmt)
    elif verb == "export-gerbers":
        cmd_export_gerbers(cli, ins, out, extra)
    elif verb == "export-drill":
        cmd_export_drill(cli, ins, out, extra)
    elif verb == "export-pdf":
        cmd_export_pdf(cli, ins, out, root)
    elif verb == "export-dxf":
        cmd_export_dxf(cli, ins, out)
    elif verb == "pos":
        cmd_pos(cli, ins, out, extra)
    elif verb == "stats":
        cmd_stats(cli, ins, out)
    elif verb == "ipcd356":
        cmd_ipcd356(cli, ins, out)
    elif verb == "render":
        cmd_render(cli, ins, out, extra, root)
    else:
        print(f"unsupported verb: {verb!r}", file=sys.stderr)
        return 127
    return 0  # unreachable in the shelled-out cases (run_and_relay calls sys.exit)


if __name__ == "__main__":
    sys.exit(main())
