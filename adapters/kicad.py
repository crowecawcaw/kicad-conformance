#!/usr/bin/env python3
"""The reference adapter: wraps `kicad-cli` behind the conformance verb protocol.

    kicad.py <verb> --in PATH [--in PATH ...] --out DIR

Every verb does one thing: run kicad-cli and leave its raw output files in `--out`. No
composition, no reduction -- the runner normalizes and compares what lands there.

`--in` may repeat. The FIRST `--in` is the entry file; the rest are support files (a
schematic's sub-sheets, a board's `.kicad_dru`/`.kicad_pro` siblings, a rejection case's
control fixture). All of them are copied into one scratch directory under their own
names, because kicad-cli resolves sub-sheet and sibling references by name relative to
the file it is given. Nothing ever runs against the committed fixture in place: kicad-cli
writes caches next to files it merely reads.

`--out` is always an explicit path the runner dictates; this adapter never relies on
kicad-cli's derived-filename default.

`version` and `capabilities` are meta-verbs answered directly here.

Locale/timezone are set by the runner in the environment it launches this with.

Crash relay: if kicad-cli is killed by a signal, this adapter re-raises the identical
signal against itself before exiting, so the runner -- whose direct child is this script,
not kicad-cli -- still sees a genuinely signaled child rather than a laundered exit code.
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

IMPLEMENTED_VERBS = (
    "version", "parse-sch", "parse-pcb", "parse-sym", "parse-fp",
    "drc", "erc", "netlist", "pos", "stats", "ipcd356",
    "render", "export-gerbers", "export-drill", "refill", "roundtrip",
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
        candidates = glob("/Applications/KiCad*/kicad-cli") + glob("/Applications/KiCad/*/kicad-cli")
    else:
        candidates = glob("/usr/lib/kicad*/bin/kicad-cli") + glob("/opt/kicad*/bin/kicad-cli")
    candidates.sort(reverse=True)
    if candidates:
        return candidates[0]
    print("kicad-cli not found (checked KICAD_CLI env, PATH, common install dirs)", file=sys.stderr)
    sys.exit(127)


def _run_step(argv: list[str]) -> int:
    """Run kicad-cli and return its raw returncode, without exiting -- used when several
    subcommands must run in sequence and only the first failure should end this process."""
    return subprocess.run(argv).returncode


def _relay_and_exit(rc: int) -> None:
    if rc < 0:
        sig = -rc
        try:
            os.kill(os.getpid(), sig)
        except OSError:
            pass
        sys.exit(128 + sig)  # fallback; the os.kill above should already have ended us
    sys.exit(rc)


def run_and_relay(argv: list[str]) -> None:
    _relay_and_exit(_run_step(argv))


def parse_argv(argv: list[str]):
    if not argv:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    verb = argv[0]
    ins: list[str] = []
    out: str | None = None
    fmt: str | None = None
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--in":
            i += 1
            ins.append(argv[i])
        elif tok == "--out":
            i += 1
            out = argv[i]
        elif tok == "--format":  # `version` only
            i += 1
            fmt = argv[i]
        i += 1
    return verb, ins, out, fmt


def cmd_version(cli: str, fmt: str | None) -> None:
    proc = subprocess.run([cli, "version", "--format", fmt or "plain"], capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


def cmd_capabilities() -> None:
    print(json.dumps(sorted(IMPLEMENTED_VERBS)))
    sys.exit(0)


def _scratch(ins: list[str], scratch_dir: Path | None = None) -> Path:
    """Copy every `--in` into one scratch directory outside `--out`, preserving names,
    and return the copy of the entry file (`ins[0]`). A `.pretty` footprint library is a
    directory, so it is copied as a tree."""
    scratch_dir = scratch_dir or Path(tempfile.mkdtemp(prefix="kicad-adapter-src-"))
    scratch_dir.mkdir(parents=True, exist_ok=True)
    for src in ins:
        src_path = Path(src)
        dest = scratch_dir / src_path.name
        if src_path.is_dir():
            shutil.copytree(src_path, dest)
        else:
            shutil.copyfile(src_path, dest)
    return scratch_dir / Path(ins[0]).name


# --- loader probes (rejection cases): exit polarity only, output discarded ----------


def cmd_parse_sch(cli: str, ins: list[str], out: str) -> None:
    """`sch upgrade --force` rewrites in place, so it runs on the scratch copy."""
    run_and_relay([cli, "sch", "upgrade", "--force", str(_scratch(ins, Path(out)))])


def cmd_parse_pcb(cli: str, ins: list[str], out: str) -> None:
    """The board loader probe is `pcb export stats`, not `pcb upgrade --force`: the
    latter SIGSEGVs on every malformed board it fails to load, so it can never
    distinguish a harness bug from an unconditional crash. `export stats` rejects the
    same bytes gracefully (exit 3, same "Failed to load board" message) while still
    requiring a fully-parsed board to succeed."""
    src = _scratch(ins)
    run_and_relay([cli, "pcb", "export", "stats", "--format", "json",
                   "-o", str(Path(out) / "stats.json"), str(src)])


def cmd_parse_sym(cli: str, ins: list[str], out: str) -> None:
    """`--out` must exist before `sym upgrade --force -o <out>/<stem>.kicad_sym` runs."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch(ins)
    run_and_relay([cli, "sym", "upgrade", "--force",
                   "-o", str(out_dir / (Path(ins[0]).stem + ".kicad_sym")), str(src)])


def cmd_parse_fp(cli: str, ins: list[str], out: str) -> None:
    """`fp upgrade` takes a `.pretty` directory and refuses a pre-existing `-o` path, so
    the target must be a not-yet-created subdirectory of an existing `--out`."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch(ins)
    run_and_relay([cli, "fp", "upgrade", "--force", "-o", str(out_dir / "upgraded.pretty"), str(src)])


# --- recorded answers ---------------------------------------------------------


def cmd_stats(cli: str, ins: list[str], out: str) -> None:
    run_and_relay([cli, "pcb", "export", "stats", "--format", "json",
                   "-o", str(Path(out) / "stats.json"), str(_scratch(ins))])


def cmd_pos(cli: str, ins: list[str], out: str) -> None:
    run_and_relay([cli, "pcb", "export", "pos", "--format", "csv", "--side", "both",
                   "--units", "mm", "-o", str(Path(out) / "pos.csv"), str(_scratch(ins))])


def cmd_ipcd356(cli: str, ins: list[str], out: str) -> None:
    run_and_relay([cli, "pcb", "export", "ipcd356",
                   "-o", str(Path(out) / "ipcd356.d356"), str(_scratch(ins))])


def cmd_drc(cli: str, ins: list[str], out: str) -> None:
    run_and_relay([cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
                   "-o", str(Path(out) / "drc.json"), str(_scratch(ins))])


def cmd_erc(cli: str, ins: list[str], out: str) -> None:
    """Runs against a scratch dir holding every sheet. With only the root sheet present,
    `sch erc` does not merely miss the sub-sheet's findings -- it fabricates a wrong one
    (an isolated_pin_label on a global label whose matching pin is invisible to it)."""
    run_and_relay([cli, "sch", "erc", "--format", "json", "--severity-all",
                   "-o", str(Path(out) / "erc.json"), str(_scratch(ins))])


def cmd_netlist(cli: str, ins: list[str], out: str) -> None:
    run_and_relay([cli, "sch", "export", "netlist", "--format", "kicadsexpr",
                   "-o", str(Path(out) / "netlist.net"), str(_scratch(ins))])


def cmd_export_gerbers(cli: str, ins: list[str], out: str) -> None:
    """No `--layers` (KiCad plots the board's own stored set) and no `--no-protel-ext`
    (which would switch every per-layer extension away from the Protel-style ones)."""
    run_and_relay([cli, "pcb", "export", "gerbers", "-o", str(Path(out)) + "/", str(_scratch(ins))])


def cmd_export_drill(cli: str, ins: list[str], out: str) -> None:
    """No `--generate-map`, no `--generate-report` (its "Created on" stamp has no input
    to normalize), no `--excellon-separate-th`: the default writes one `<stem>.drl`."""
    run_and_relay([cli, "pcb", "export", "drill", "-o", str(Path(out)) + "/", str(_scratch(ins))])


def cmd_render(cli: str, ins: list[str], out: str) -> None:
    """One board layer, one schematic sheet, one symbol library, one footprint library --
    all the same verb. Everything is pinned for determinism: a single fixed layer for
    boards (`F.Cu`; the gerbers already cover every other layer byte-exactly), board-area
    page sizing, no drawing sheet, black and white."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch(ins)
    suffix = Path(ins[0]).suffix
    if suffix == ".kicad_pcb":
        run_and_relay([cli, "pcb", "export", "svg", "--layers", "F.Cu", "--page-size-mode", "2",
                       "--exclude-drawing-sheet", "--black-and-white",
                       "-o", str(out_dir / "render.svg"), str(src)])
    elif suffix == ".kicad_sch":
        # `sch export svg` takes a directory `-o` and writes `<stem>.svg` into it.
        run_and_relay([cli, "sch", "export", "svg", "--exclude-drawing-sheet",
                       "--black-and-white", "-o", str(out_dir) + "/", str(src)])
    elif suffix == ".kicad_sym":
        run_and_relay([cli, "sym", "export", "svg", "--black-and-white",
                       "-o", str(out_dir) + "/", str(src)])
    else:
        run_and_relay([cli, "fp", "export", "svg", "--black-and-white",
                       "-o", str(out_dir) + "/", str(src)])


def _export_board(cli: str, board: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = [
        [cli, "pcb", "export", "stats", "--format", "json", "-o", str(out_dir / "stats.json"), str(board)],
        [cli, "pcb", "export", "pos", "--format", "csv", "--side", "both", "--units", "mm",
         "-o", str(out_dir / "pos.csv"), str(board)],
        [cli, "pcb", "export", "ipcd356", "-o", str(out_dir / "ipcd356.d356"), str(board)],
        [cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
         "-o", str(out_dir / "drc.json"), str(board)],
    ]
    for step in steps:
        rc = _run_step(step)
        if rc != 0:
            _relay_and_exit(rc)


def _export_sch(cli: str, sheet: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = [
        [cli, "sch", "export", "netlist", "--format", "kicadsexpr",
         "-o", str(out_dir / "netlist.net"), str(sheet)],
        [cli, "sch", "erc", "--format", "json", "--severity-all",
         "-o", str(out_dir / "erc.json"), str(sheet)],
    ]
    for step in steps:
        rc = _run_step(step)
        if rc != 0:
            _relay_and_exit(rc)


def cmd_refill(cli: str, ins: list[str], out: str) -> None:
    """Zone fills RECOMPUTED by the tool, then handed back as a board.

    `pcb drc --refill-zones --save-board` rewrites the board it is given, in place, with
    every zone's fill recomputed from the zone outline and the surrounding copper -- so it
    runs on the scratch copy and the result is copied out under a fixed name. The DRC
    report itself is written beside the scratch board and discarded: `--refill-zones` is a
    flag of `pcb drc`, so a report is produced whether or not anyone wants it, and the
    `drc` extra already records that answer for cases that do.

    The runner projects the returned board down to its zone-fill geometry; this verb does
    no reduction of its own, so an implementation-under-test only ever has to produce a
    refilled board in KiCad's board format."""
    suffix = Path(ins[0]).suffix
    if suffix != ".kicad_pcb":
        print(f"refill: only applies to a board input, not {suffix or '(directory)'!r}",
              file=sys.stderr)
        sys.exit(2)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _scratch(ins)
    rc = _run_step([cli, "pcb", "drc", "--refill-zones", "--save-board", "--format", "json",
                    "-o", str(src.parent / "refill-drc.json"), str(src)])
    if rc != 0:
        _relay_and_exit(rc)
    shutil.copyfile(src, out_dir / "refilled.kicad_pcb")
    sys.exit(0)


def cmd_roundtrip(cli: str, ins: list[str], out: str) -> None:
    """Round-trip write-path testing. Exports the fixture into `<out>/original/`, then
    re-serializes a second scratch copy with `<kind> upgrade --force` and exports that
    into `<out>/roundtripped/`. The runner asserts the two directories match after
    normalization; nothing is recorded under `expected/`.

    DRC/ERC are part of each half deliberately: a dropped inline `(net_class ...)` block
    or a pad's vanished `(drill 0)` changes nothing in stats/pos/ipcd356, and only shows
    up as a different violation set."""
    out_dir = Path(out)
    suffix = Path(ins[0]).suffix

    if suffix == ".kicad_pcb":
        _export_board(cli, _scratch(ins), out_dir / "original")
        rt = _scratch(ins)
        rc = _run_step([cli, "pcb", "upgrade", "--force", str(rt)])
        if rc != 0:
            _relay_and_exit(rc)
        _export_board(cli, rt, out_dir / "roundtripped")
    elif suffix == ".kicad_sch":
        _export_sch(cli, _scratch(ins), out_dir / "original")
        rt = _scratch(ins)
        rc = _run_step([cli, "sch", "upgrade", "--force", str(rt)])
        if rc != 0:
            _relay_and_exit(rc)
        _export_sch(cli, rt, out_dir / "roundtripped")
    else:
        print(f"roundtrip: does not apply to {suffix or '(directory)'!r} input -- "
              f"kicad-cli 10.0.5 has no structured symbol/footprint export to compare",
              file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


_DISPATCH = {
    "parse-sch": cmd_parse_sch,
    "parse-pcb": cmd_parse_pcb,
    "parse-sym": cmd_parse_sym,
    "parse-fp": cmd_parse_fp,
    "drc": cmd_drc,
    "erc": cmd_erc,
    "netlist": cmd_netlist,
    "pos": cmd_pos,
    "stats": cmd_stats,
    "ipcd356": cmd_ipcd356,
    "render": cmd_render,
    "export-gerbers": cmd_export_gerbers,
    "export-drill": cmd_export_drill,
    "refill": cmd_refill,
    "roundtrip": cmd_roundtrip,
}


def main() -> int:
    verb, ins, out, fmt = parse_argv(sys.argv[1:])

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

    handler = _DISPATCH.get(verb)
    if handler is None:
        print(f"unsupported verb: {verb!r}", file=sys.stderr)
        return 127
    handler(cli, ins, out)
    return 0  # unreachable: every handler exits


if __name__ == "__main__":
    sys.exit(main())
