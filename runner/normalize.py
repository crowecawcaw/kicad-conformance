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
# writes LF. Goldens are committed LF-canonical, so every text compare normalizes CRLF
# to LF first, whichever platform produced the bytes being compared.


def normalize_crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


# --- s-expr (sch/pcb/sym/fp upgrade output) --------------------------------------
# Observed (KiCad 10.0.5): `(generator_version "10.0")` is the kicad-cli *build's* own
# stamp, not the file-format compatibility key. It changes on every point release even
# when the canonical file content is otherwise identical, so it would churn the golden
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


# --- DRC / ERC JSON ---------------------------------------------------------------
# See runner/reduce.py: for `structured` checks the "normalizer" and the "reduction"
# are the same step (DL-0014) — the golden stores the reduced form directly, there is
# no separate normalize-then-store. Kept out of this module to avoid a false split.


# --- Dispatch by output kind -------------------------------------------------------
_BY_SUFFIX = {
    ".kicad_sch": normalize_sexpr,
    ".kicad_pcb": normalize_sexpr,
    ".kicad_sym": normalize_sexpr,
    ".kicad_mod": normalize_sexpr,
    ".gbr": normalize_gerber,
    ".gbrjob": normalize_gbrjob,
}


def normalize_for(path: Path, data: bytes) -> bytes:
    """Pick a normalizer by file suffix. Anything without a specific normalizer above
    (`.net`, `.csv`, `.rpt`, `.pos`, ...) still gets CRLF->LF (DL-0016) — that one
    applies to every text golden — but no other rewriting: per the honesty rule (§4),
    we do not invent a normalizer for a kind of drift we have not observed."""
    normalizer = _BY_SUFFIX.get(path.suffix, normalize_crlf)
    return normalizer(data)
