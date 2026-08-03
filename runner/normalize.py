"""The normalization layer (DESIGN.md §4).

Each function strips exactly one *observed* source of run-to-run noise and says so in
its docstring, per the "honesty rule" (§4): no normalizer is added for output that is
already byte-stable, and every normalizer here is exercised by the determinism self-test
(`runner/determinism.py`) so a normalizer that stops mattering is noticed.

Environment pinning (LC_ALL=C.UTF-8, TZ=UTC) is done by the engine when it invokes the
adapter (DESIGN §4) — that removes a whole class of drift *before* it is ever written to
a file, so it is not duplicated here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# --- CRLF -> LF -----------------------------------------------------------------
# DL-0016: kicad-cli's native-Windows build writes CRLF; the Docker-Linux build (== CI)
# writes LF. Expected files are committed LF-canonical, so every text compare normalizes CRLF
# to LF first, whichever platform produced the bytes being compared.


def normalize_crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


# --- s-expr (sch/pcb/sym/fp upgrade output) --------------------------------------
# Observed (KiCad 10.0.5): `(generator_version "10.0")` is the kicad-cli *build's* own
# stamp, not the file-format compatibility key. It changes on every point release even
# when the canonical file content is otherwise identical, so it would churn the expected file
# on every kicad-cli patch bump for no semantic reason. We deliberately KEEP
# `(version YYYYMMDD)` (the real compatibility key, DESIGN §4) and strip only
# `generator_version`.
_GENERATOR_VERSION_RE = re.compile(rb'[ \t]*\(generator_version "[^"]*"\)\r?\n?')


def normalize_sexpr(data: bytes) -> bytes:
    data = normalize_crlf(data)
    return _GENERATOR_VERSION_RE.sub(b"", data)


# --- Gerber (RS-274X) -------------------------------------------------------------
# Observed (KiCad 10.0.5, `pcb export gerbers`): every plot stamps its own wall-clock
# creation time twice — once as a machine-readable attribute (`%TF.CreationDate,...*%`)
# and once in a human comment (`G04 Created by KiCad (PCBNEW <ver>) date ...*`) — for
# otherwise byte-identical geometry. Confirmed by running the same export twice 2s
# apart: only these two lines (plus the mirrored date in `%TF.GenerationSoftware`'s
# sibling .gbrjob, handled separately below) differed.
_GERBER_CREATION_DATE_RE = re.compile(rb"%TF\.CreationDate,[^*]*\*%")
_GERBER_CREATED_BY_RE = re.compile(rb"G04 Created by KiCad[^\n]*\*")


def normalize_gerber(data: bytes) -> bytes:
    data = normalize_crlf(data)
    data = _GERBER_CREATION_DATE_RE.sub(b"%TF.CreationDate,NORMALIZED*%", data)
    data = _GERBER_CREATED_BY_RE.sub(b"G04 Created by KiCad NORMALIZED*", data)
    return data


# --- Gerber job file (.gbrjob) -----------------------------------------------------
# The .gbrjob is JSON, not RS-274X text, and carries its OWN copy of the creation date
# under `Header.CreationDate` (observed key path on 10.0.5 — DESIGN.md's table names
# `GeneralSpecs/CreationDate`, which was not what 10.0.5 actually emits; this is a
# documented correction, see the runner README). This is a separate normalizer from the
# gerber one above because it's a different file/format with the same underlying date.
def normalize_gbrjob(data: bytes) -> bytes:
    data = normalize_crlf(data)
    obj = json.loads(data.decode("utf-8"))
    header = obj.get("Header")
    if isinstance(header, dict):
        if "CreationDate" in header:
            header["CreationDate"] = "NORMALIZED"
        gs = header.get("GenerationSoftware")
        if isinstance(gs, dict) and "Version" in gs:
            gs["Version"] = "NORMALIZED"
    # Re-serialize deterministically. This is a normalizing re-encode (not a literal
    # byte pass-through of kicad-cli's own formatting) but both sides of every compare
    # go through this same function, so it stays a faithful, content-only comparison.
    return (json.dumps(obj, indent=2, sort_keys=False) + "\n").encode("utf-8")


# --- Excellon drill (.drl) ---------------------------------------------------------
# Observed (KiCad 10.0.5, `pcb export drill`, run twice 2s apart, on both the populated
# and the empty-holes fixture): exactly two lines carry a wall-clock timestamp, mirroring
# the gerber date pair above but in Excellon's own comment syntax (VALIDATION.md §7.3,
# DL-0026). Nothing else in the file (tool table, hit records, `M48`/`M30` framing) moved.
_DRILL_HEADER_DATE_RE = re.compile(rb"(; DRILL file KiCad [^\n]* date )\S+")
_DRILL_TF_CREATIONDATE_RE = re.compile(rb"(; #@! TF\.CreationDate,)[^\r\n]*")


def normalize_drill(data: bytes) -> bytes:
    data = normalize_crlf(data)
    data = _DRILL_HEADER_DATE_RE.sub(rb"\1NORMALIZED", data)
    data = _DRILL_TF_CREATIONDATE_RE.sub(rb"\1NORMALIZED", data)
    return data


# --- DRC / ERC JSON ---------------------------------------------------------------
# See runner/reduce.py: for `structured` checks the "normalizer" and the "reduction"
# are the same step (DL-0014) — the expected file stores the reduced form directly, there is
# no separate normalize-then-store. Kept out of this module to avoid a false split.


# --- SVG (L3 render, VALIDATION.md §4.2/§4.3, DL-0021) -----------------------------
# Observed (KiCad 10.0.5, `pcb export svg` / `sch export svg`, run twice 1s apart): the
# ONLY run-to-run difference is the `<title>` line (`SVG Image created as <filename>
# date <ISO-timestamp>` — both the scratch-copy filename and the wall-clock date leak
# in). Path geometry and fills are byte-stable. `<desc>` was NOT observed to vary in
# that probe (`Image generated by PCBNEW ` / `Image generated by Eeschema-SVG `, stable
# per exporter) — it is normalized anyway because VALIDATION.md/DL-0021 explicitly
# names it alongside `<title>` (a `sym`/`fp` export's `<desc>` may embed a per-symbol
# name that *does* vary case-to-case, even if not run-to-run within one case), so this
# is a documented, not invented, normalizer.
_SVG_TITLE_RE = re.compile(rb"<title>.*?</title>", re.DOTALL)
_SVG_DESC_RE = re.compile(rb"<desc>.*?</desc>", re.DOTALL)


def normalize_svg(data: bytes) -> bytes:
    data = normalize_crlf(data)
    data = _SVG_TITLE_RE.sub(b"<title>NORMALIZED</title>", data)
    data = _SVG_DESC_RE.sub(b"<desc>NORMALIZED</desc>", data)
    return data


# --- Dispatch by output kind -------------------------------------------------------
# Every extension `pcb export gerbers` actually writes for a layer file (VALIDATION.md
# §7.1, DL-0026) -- Protel-style per-layer extensions where KiCad has one (.gtl/.gbl/...),
# else the generic `.gbr` (margin, courtyard, user layers, ...).
_GERBER_LAYER_SUFFIXES = (
    ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gtp", ".gbp",
    ".gta", ".gba", ".gm1", ".gbr",
)

_BY_SUFFIX = {
    ".kicad_sch": normalize_sexpr,
    ".kicad_pcb": normalize_sexpr,
    ".kicad_sym": normalize_sexpr,
    ".kicad_mod": normalize_sexpr,
    ".gbrjob": normalize_gbrjob,
    ".drl": normalize_drill,
    ".svg": normalize_svg,
}
_BY_SUFFIX.update({suffix: normalize_gerber for suffix in _GERBER_LAYER_SUFFIXES})


def normalize_for(path: Path, data: bytes) -> bytes:
    """Pick a normalizer by file suffix. Anything without a specific normalizer above
    (`.net`, `.csv`, `.rpt`, `.pos`, ...) still gets CRLF->LF (DL-0016) — that one
    applies to every text expected file — but no other rewriting: per the honesty rule (§4),
    we do not invent a normalizer for a kind of drift we have not observed."""
    normalizer = _BY_SUFFIX.get(path.suffix, normalize_crlf)
    return normalizer(data)
