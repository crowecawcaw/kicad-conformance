"""Per-format normalizers for recorded answers.

Each function strips exactly one *observed* source of run-to-run noise -- wall-clock
dates, embedded file paths, freshly-minted UUIDs -- and nothing else. Output verified
byte-stable across repeat runs gets no normalizer at all, because an identity normalizer
would imply a nondeterminism that does not exist. `--determinism-check` runs every answer
twice and is what keeps this file honest.

Locale/timezone pinning (LC_ALL=C.UTF-8, TZ=UTC) happens where the adapter is launched
(runner/adapter.py), which removes a class of drift before it is ever written to a file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# --- CRLF -> LF -----------------------------------------------------------------
# kicad-cli's native-Windows build writes CRLF; the Docker-Linux build (== CI) writes LF.
# Expected files are committed LF-canonical, so every text compare normalizes first.


def normalize_crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


# --- Volatile content shared by the structured text answers ------------------------
# Observed on KiCad 10.0.5 by running each export twice, seconds apart, on the same
# fixture and diffing:
#
#   stats.json    only `metadata.date` moved.
#   drc.json      only `date` moved.
#   erc.json      `date` moved, plus a violation item `uuid` minted fresh per run.
#   netlist.net   `(date ...)` moved; `(source ...)` and the `Sheetfile` property both
#                 carry the scratch directory's path; every `(tstamps "<uuid>")` was
#                 minted fresh.
#   pos.csv       byte-identical.
#   ipcd356.d356  byte-identical (no header date in this exporter).
#
# So three redactions cover all of it, and the same three apply to both the JSON reports
# and the s-expression netlist: ISO timestamps, UUIDs, and the directory part of an
# embedded `.kicad_*` path (the basename is kept -- it names which file, and is stable).
_ISO_DATE_RE = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_UUID_RE = re.compile(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_KICAD_PATH_RE = re.compile(rb'"[^"\n]*/([^"/\n]*\.kicad_[a-z]+)"')


def normalize_report(data: bytes) -> bytes:
    """`stats.json`, `netlist.net`, and the textual half of the DRC/ERC reports.
    Idempotent: the replacement text matches none of the three patterns, so
    re-normalizing a committed expected file is a no-op."""
    data = normalize_crlf(data)
    data = _ISO_DATE_RE.sub(b"NORMALIZED-DATE", data)
    data = _UUID_RE.sub(b"NORMALIZED-UUID", data)
    data = _KICAD_PATH_RE.sub(rb'"\1"', data)
    return data


# --- DRC / ERC reports -------------------------------------------------------------
# One more thing moves in these two, and only in these two: the ORDER of the `items[]`
# inside a single violation. Observed on suites/drc/holes-co-located, where two pads sit
# at the same point -- eight consecutive runs produced the same order seven times and the
# swapped order once. Violation order itself never moved across those runs, and neither
# did anything in stats.json/pos.csv/ipcd356.d356/netlist.net, so the sort is scoped to
# exactly the list that was seen to wobble.
#
# Sorting needs structure, so these two files (and no others) are re-serialized. The
# textual redactions run FIRST, so the sort key never contains a freshly-minted UUID.
# kicad-cli emits these reports with 4-space indent and a trailing newline, which is what
# json.dumps reproduces here; recognition is by the `$schema` these reports carry and
# nothing else does.
_KICAD_SCHEMA_PREFIX = "https://schemas.kicad.org/"


def _sort_violation_items(node) -> None:
    if isinstance(node, dict):
        items = node.get("items")
        if isinstance(items, list):
            items.sort(key=lambda i: json.dumps(i, sort_keys=True))
        for value in node.values():
            _sort_violation_items(value)
    elif isinstance(node, list):
        for value in node:
            _sort_violation_items(value)


def normalize_json(data: bytes) -> bytes:
    data = normalize_report(data)
    obj = json.loads(data.decode("utf-8"))
    if not isinstance(obj, dict) or not str(obj.get("$schema", "")).startswith(_KICAD_SCHEMA_PREFIX):
        return data  # stats.json and anything else: nothing observed to reorder
    _sort_violation_items(obj)
    return (json.dumps(obj, indent=4, ensure_ascii=False) + "\n").encode("utf-8")


# --- Gerber (RS-274X) -------------------------------------------------------------
# Observed (`pcb export gerbers`): every plot stamps its wall-clock creation time twice,
# once machine-readable and once in a human comment, for otherwise identical geometry.
_GERBER_CREATION_DATE_RE = re.compile(rb"%TF\.CreationDate,[^*]*\*%")
_GERBER_CREATED_BY_RE = re.compile(rb"G04 Created by KiCad[^\n]*\*")


def normalize_gerber(data: bytes) -> bytes:
    data = normalize_crlf(data)
    data = _GERBER_CREATION_DATE_RE.sub(b"%TF.CreationDate,NORMALIZED*%", data)
    data = _GERBER_CREATED_BY_RE.sub(b"G04 Created by KiCad NORMALIZED*", data)
    return data


# --- Gerber job file (.gbrjob) -----------------------------------------------------
# JSON, not RS-274X, carrying its own copy of the creation date under `Header/
# CreationDate` (the key 10.0.5 actually emits). Re-serialized deterministically; both
# sides of every compare go through this same function.
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
    return (json.dumps(obj, indent=2, sort_keys=False) + "\n").encode("utf-8")


# --- Excellon drill (.drl) ---------------------------------------------------------
# Observed (`pcb export drill`): exactly two lines carry a wall-clock timestamp,
# mirroring the gerber date pair in Excellon's own comment syntax.
_DRILL_HEADER_DATE_RE = re.compile(rb"(; DRILL file KiCad [^\n]* date )\S+")
_DRILL_TF_CREATIONDATE_RE = re.compile(rb"(; #@! TF\.CreationDate,)[^\r\n]*")


def normalize_drill(data: bytes) -> bytes:
    data = normalize_crlf(data)
    data = _DRILL_HEADER_DATE_RE.sub(rb"\1NORMALIZED", data)
    data = _DRILL_TF_CREATIONDATE_RE.sub(rb"\1NORMALIZED", data)
    return data


# --- SVG (render answers) ----------------------------------------------------------
# Observed (`pcb/sch/sym/fp export svg`): the only run-to-run difference is the `<title>`
# line, which embeds both the scratch-copy filename and the wall-clock date. `<desc>` did
# not vary run-to-run but does embed the exporter/symbol name, so it is normalized too.
_SVG_TITLE_RE = re.compile(rb"<title>.*?</title>", re.DOTALL)
_SVG_DESC_RE = re.compile(rb"<desc>.*?</desc>", re.DOTALL)


def normalize_svg(data: bytes) -> bytes:
    data = normalize_crlf(data)
    data = _SVG_TITLE_RE.sub(b"<title>NORMALIZED</title>", data)
    data = _SVG_DESC_RE.sub(b"<desc>NORMALIZED</desc>", data)
    return data


# --- Dispatch by output kind -------------------------------------------------------
# Protel-style per-layer gerber extensions where KiCad has one, else the generic `.gbr`.
_GERBER_LAYER_SUFFIXES = (
    ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gtp", ".gbp",
    ".gta", ".gba", ".gm1", ".gbr",
)

# Numbered inner-copper layers (`In1.Cu`, `In2.Cu`, ...) have no fixed Protel name, so
# KiCad names them `.g1`, `.g2`, ... -- recognized by shape so any layer count is covered.
_GERBER_INNER_COPPER_RE = re.compile(r"^\.g\d+$")

_BY_SUFFIX = {
    ".gbrjob": normalize_gbrjob,
    ".drl": normalize_drill,
    ".svg": normalize_svg,
    ".json": normalize_json,
    ".net": normalize_report,
}
_BY_SUFFIX.update({suffix: normalize_gerber for suffix in _GERBER_LAYER_SUFFIXES})


def normalize_for(path: Path, data: bytes) -> bytes:
    """Pick a normalizer by file suffix. Anything without one (`.csv`, `.d356`) still
    gets CRLF->LF, which applies to every text answer, but no other rewriting."""
    suffix = path.suffix
    if suffix in _BY_SUFFIX:
        return _BY_SUFFIX[suffix](data)
    if _GERBER_INNER_COPPER_RE.match(suffix):
        return normalize_gerber(data)
    return normalize_crlf(data)
