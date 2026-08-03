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
- Trailing unrecognized tokens are forwarded verbatim to kicad-cli (`case.toml`'s
  `args =`, e.g. `--layers F.Cu,B.Cu,Edge.Cuts`).
- `version` and `capabilities` are meta-verbs answered directly by this script, not by
  shelling out per-file.

Locale/timezone: this adapter does NOT set LC_ALL/TZ itself — the runner sets them in
the environment it launches the adapter with (DESIGN §4), and they are inherited here.

Crash relay (see runner/verdict.py for the rationale): if kicad-cli is killed by a
signal, this adapter re-raises the identical signal against itself before exiting, so
the runner — whose direct subprocess child is this adapter, not kicad-cli — still
observes a genuinely signaled child (a negative `returncode`), not a laundered "normal"
exit code.
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
# directory, not the repo root, is on sys.path[0] by default).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runner.verbs import IMPLEMENTED_VERBS  # noqa: E402


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


def run_and_relay(argv: list[str]) -> None:
    """Run kicad-cli; end this adapter process with matching termination semantics."""
    proc = subprocess.run(argv)
    rc = proc.returncode
    if rc < 0:
        sig = -rc
        try:
            os.kill(os.getpid(), sig)
        except OSError:
            pass
        sys.exit(128 + sig)  # fallback; os.kill above should already have ended us
    sys.exit(rc)


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
    specifically so a scratch input copy is never mistaken for part of a `golden-dir`
    verb's output (`export-gerbers`/`export-drill` treat their entire `--out` as the
    artifact set) and never lands inside the tree a golden-dir compare walks."""
    return Path(tempfile.mkdtemp(prefix="kicad-adapter-src-"))


def _scratch_copy(src: str, scratch_dir: Path) -> Path:
    """Copy a single input file into a scratch dir and return the copy's path.

    This is used for EVERY verb, not just the rewrite-in-place `upgrade` ones: kicad-cli
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


def cmd_upgrade(cli: str, kind: str, ins: list[str], out: str) -> None:
    """parse-sch / parse-pcb / upgrade: `sch|pcb upgrade --force` rewrites IN PLACE, so
    copy the fixture to a scratch dir first and upgrade the copy (§2, §2a). The scratch
    copy IS the artifact the runner reads back, so it lives directly under `--out`."""
    out_dir = Path(out)
    dest = _scratch_copy(ins[0], out_dir)
    run_and_relay([cli, kind, "upgrade", "--force", str(dest)])


def cmd_parse_sym(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    target = out_dir / (Path(ins[0]).stem + ".kicad_sym")
    run_and_relay([cli, "sym", "upgrade", "--force", "-o", str(target), str(src)])


def cmd_parse_fp(cli: str, ins: list[str], out: str) -> None:
    """fp upgrade takes a `.pretty` DIRECTORY (never a lone `.kicad_mod`) and refuses a
    pre-existing `-o` path, so the target must be a not-yet-created subdirectory."""
    out_dir = Path(out)
    src = _scratch_copy_tree(ins[0], _fresh_scratch_dir())
    target = out_dir / "upgraded.pretty"  # must NOT pre-exist
    run_and_relay([cli, "fp", "upgrade", "--force", "-o", str(target), str(src)])


def cmd_erc(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "sch", "erc", "--format", "json", "--severity-all",
         "-o", str(out_dir / "erc.json"), str(src)]
    )


def cmd_drc(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
         "-o", str(out_dir / "drc.json"), str(src)]
    )


def cmd_netlist(cli: str, ins: list[str], out: str, root: str | None, fmt: str | None) -> None:
    """Copies every `--in` (root + subsheets) flat into a scratch dir by basename —
    sufficient for same-directory multi-sheet projects (the common case) — then runs
    `sch export netlist` against the root sheet's scratch copy.

    `fmt` selects the interchange format (`kicadsexpr`, the default, or `kicadxml` --
    VALIDATION.md §3.1's cross-format-fairness reader). `-o` always names the same
    output file regardless of format: kicad-cli writes exactly the path it's given, not
    a format-derived extension (verified empirically), so `netlist.net` may contain
    either s-expr or XML text."""
    out_dir = Path(out)
    scratch = _fresh_scratch_dir()
    root_name = root or Path(ins[0]).name
    root_dest = None
    for src in ins:
        dest = scratch / Path(src).name
        shutil.copyfile(src, dest)
        if Path(src).name == root_name:
            root_dest = dest
    if root_dest is None:
        root_dest = scratch / Path(ins[0]).name
    run_and_relay(
        [cli, "sch", "export", "netlist", "--format", fmt or "kicadsexpr",
         "-o", str(out_dir / "netlist.net"), str(root_dest)]
    )


def cmd_export_gerbers(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    args = list(extra)
    if "--no-protel-ext" not in args:
        args.append("--no-protel-ext")  # always pinned, DESIGN §2b
    run_and_relay(
        [cli, "pcb", "export", "gerbers", *args, "-o", str(out_dir) + "/", str(src)]
    )


def cmd_export_drill(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "drill", "--generate-report",
         "--report-path", str(out_dir / "drill-report.rpt"),
         "-o", str(out_dir) + "/", *extra, str(src)]
    )


def cmd_export_pos(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "pos", "--format", "csv", "--side", "both",
         "--units", "mm", "-o", str(out_dir / "pos.csv"), *extra, str(src)]
    )


def cmd_bom(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "sch", "export", "bom", "-o", str(out_dir / "bom.csv"), *extra, str(src)]
    )


def cmd_export_stats(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "stats", "--format", "json",
         "-o", str(out_dir / "stats.json"), str(src)]
    )


def cmd_export_ipcd356(cli: str, ins: list[str], out: str) -> None:
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "ipcd356", "-o", str(out_dir / "board.d356"), str(src)]
    )


def cmd_export_svg_pcb(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    """`extra` (the case's `args =`) carries `--layers <L>` -- the layer set is a
    per-case parameter, never a fixed list (VALIDATION.md §5.1, mirroring the gerber
    layer set, DESIGN §2b). The remaining flags are pinned for determinism (VALIDATION
    §4.3): `--page-size-mode 2` (board-area only, so page size can't drift),
    `--exclude-drawing-sheet`, `--black-and-white` (removes theme-color dependence)."""
    out_dir = Path(out)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "pcb", "export", "svg", "--page-size-mode", "2",
         "--exclude-drawing-sheet", "--black-and-white", *extra,
         "-o", str(out_dir / "render.svg"), str(src)]
    )


def cmd_export_svg_sch(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    """`sch export svg` writes `<out>/<stem>.svg` (a directory `-o`, not a file path --
    unlike `export-svg-pcb`); the runner's artifact resolver knows this (engine.py
    `_resolve_artifact`)."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "sch", "export", "svg", "--no-background-color", *extra,
         "-o", str(out_dir) + "/", str(src)]
    )


def cmd_export_svg_sym(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch_copy(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "sym", "export", "svg", "--black-and-white", *extra,
         "-o", str(out_dir) + "/", str(src)]
    )


def cmd_export_svg_fp(cli: str, ins: list[str], out: str, extra: list[str]) -> None:
    """`fp export svg`, like `parse-fp`, takes a `.pretty` LIBRARY DIRECTORY, never a
    lone `.kicad_mod` (DESIGN §2)."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch_copy_tree(ins[0], _fresh_scratch_dir())
    run_and_relay(
        [cli, "fp", "export", "svg", "--black-and-white", *extra,
         "-o", str(out_dir) + "/", str(src)]
    )


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

    if verb in ("parse-sch", "upgrade-sch"):
        cmd_upgrade(cli, "sch", ins, out)
    elif verb in ("parse-pcb", "upgrade-pcb"):
        cmd_upgrade(cli, "pcb", ins, out)
    elif verb == "upgrade":
        # Dispatch on the input's extension since `upgrade` doesn't otherwise say
        # which loader to use.
        ext = Path(ins[0]).suffix
        kind = {".kicad_sch": "sch", ".kicad_pcb": "pcb"}.get(ext)
        if kind is None:
            print(f"upgrade: cannot infer sch/pcb from {ins[0]!r}", file=sys.stderr)
            return 2
        cmd_upgrade(cli, kind, ins, out)
    elif verb == "parse-sym":
        cmd_parse_sym(cli, ins, out)
    elif verb == "parse-fp":
        cmd_parse_fp(cli, ins, out)
    elif verb == "erc":
        cmd_erc(cli, ins, out)
    elif verb == "drc":
        cmd_drc(cli, ins, out)
    elif verb == "netlist":
        cmd_netlist(cli, ins, out, root, fmt)
    elif verb == "export-gerbers":
        cmd_export_gerbers(cli, ins, out, extra)
    elif verb == "export-drill":
        cmd_export_drill(cli, ins, out, extra)
    elif verb == "export-pos":
        cmd_export_pos(cli, ins, out, extra)
    elif verb == "bom":
        cmd_bom(cli, ins, out, extra)
    elif verb == "export-stats":
        cmd_export_stats(cli, ins, out)
    elif verb == "export-ipcd356":
        cmd_export_ipcd356(cli, ins, out)
    elif verb == "export-svg-pcb":
        cmd_export_svg_pcb(cli, ins, out, extra)
    elif verb == "export-svg-sch":
        cmd_export_svg_sch(cli, ins, out, extra)
    elif verb == "export-svg-sym":
        cmd_export_svg_sym(cli, ins, out, extra)
    elif verb == "export-svg-fp":
        cmd_export_svg_fp(cli, ins, out, extra)
    else:
        print(f"unsupported verb: {verb!r}", file=sys.stderr)
        return 127
    return 0  # unreachable in the shelled-out cases (run_and_relay calls sys.exit)


if __name__ == "__main__":
    sys.exit(main())
