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
# Two things move in these two, and only in these two: the ORDER of the `items[]` inside a
# single violation, and the ORDER of the finding arrays themselves.
#
# The items[] wobble was observed first, on suites/drc/holes-co-located, where two pads sit
# at the same point. That case was sampled eight times, seven of which agreed, and the
# conclusion recorded here was that "violation order itself never moved". That was wrong --
# eight runs was not enough. CI caught the same case emitting its two solder_mask_bridge
# findings (Front and Rear, both always present) in swapped order; re-sampled twelve times
# locally it produced Front-first eight times and Rear-first four. The geometry is
# symmetric across both mask layers, so nothing breaks the tie.
#
# So the finding arrays are sorted too. Order carries no meaning in any of them -- they are
# sets of findings that happen to be serialized as JSON lists. Note this cannot paper over
# DIV-0003, which is a MEMBERSHIP difference in unconnected_items rather than an ordering
# one; sorting is merely correct there, not a fix.
#
# Sorting needs structure, so these two files (and no others) are re-serialized. The
# textual redactions run FIRST, so the sort key never contains a freshly-minted UUID.
# kicad-cli emits these reports with 4-space indent and a trailing newline, which is what
# json.dumps reproduces here; recognition is by the `$schema` these reports carry and
# nothing else does.
_KICAD_SCHEMA_PREFIX = "https://schemas.kicad.org/"

# The finding arrays of the DRC and ERC reports, sorted wherever they appear.
_FINDING_ARRAYS = ("violations", "unconnected_items", "schematic_parity", "items")


def _sort_violation_items(node) -> None:
    if isinstance(node, dict):
        for key in _FINDING_ARRAYS:
            value = node.get(key)
            if isinstance(value, list):
                # Recurse FIRST: an inner items[] must already be sorted before it is used
                # as part of an outer violation's sort key, or the key is unstable.
                for entry in value:
                    _sort_violation_items(entry)
                value.sort(key=lambda i: json.dumps(i, sort_keys=True))
        for key, value in node.items():
            if key not in _FINDING_ARRAYS:
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


# --- Recomputed zone fills (the `refill` answer) -----------------------------------
# The `refill` answer's raw artifact is a whole board file: the fixture after KiCad has
# recomputed every zone's fill and written it back (`pcb drc --refill-zones
# --save-board`). Recording that board verbatim would pin the serializer -- indentation,
# key order, every unrelated block -- alongside the one thing the answer is about, so a
# board file is instead PROJECTED down to just the computed fill geometry.
#
# This is the only normalizer that reduces rather than redacts, and it is deliberate: the
# projection is defined here, in the suite, so every implementation is judged by the same
# reduction and an alternate adapter only has to hand back a refilled board in KiCad's
# board format. See docs/FORMAT.md for the recorded shape.
#
# Coordinates are emitted as the exact tokens the board file carries -- never reparsed as
# floats -- so the answer pins geometry to the last digit KiCad wrote.


def _sexpr_tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            yield text[i:min(j + 1, n)]  # quotes kept: tokens are re-emitted verbatim
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield text[i:j]
            i = j


def _sexpr_parse(text: str) -> list:
    """A minimal reader: nested lists of raw tokens. Deliberately not a validator -- the
    input here has already been accepted and rewritten by the tool under test."""
    root: list = []
    stack = [root]
    for tok in _sexpr_tokens(text):
        if tok == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif tok == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(tok)
    return root


def _kids(node: list, head: str) -> list:
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _kid(node: list, head: str):
    for c in _kids(node, head):
        return c
    return None


def _tail(node) -> str:
    """A node's arguments, re-emitted verbatim. `none` -- bare, so it can never collide
    with a real value, which always keeps its quotes -- means the board carries no such
    node at all: a rule-area zone has no `(net ...)`, for instance."""
    return " ".join(node[1:]) if node and len(node) > 1 else "none"


def _all_zones(node: list, found: list) -> list:
    """Every `(zone ...)` anywhere in the document, in document order. Recursive rather
    than top-level-only so a zone nested inside a footprint is never silently dropped from
    the answer."""
    for child in node:
        if isinstance(child, list) and child:
            if child[0] == "zone":
                found.append(child)
            else:
                _all_zones(child, found)
    return found


def project_zone_fills(data: bytes) -> bytes:
    """A board file -> the computed fill geometry of its zones, as stable text.

    Zones are reported in document order (the order the writer emitted them, which is
    itself part of the answer); each zone's identity line carries its net and its declared
    layer(s), and each `(filled_polygon ...)` its layer, its vertex count, any flag it
    carries (`island`, ...), and every vertex. Everything else in the board -- footprints,
    tracks, setup, uuids, the zone's authored outline -- is dropped."""
    doc = _sexpr_parse(normalize_crlf(data).decode("utf-8", errors="replace"))
    zones = _all_zones(doc, [])

    lines = [f"zones {len(zones)}"]
    for index, zone in enumerate(zones):
        layers = _kid(zone, "layers") or _kid(zone, "layer")
        lines.append(
            f"zone {index} net {_tail(_kid(zone, 'net'))} "
            f"layers {_tail(layers)}"
        )
        fills = _kids(zone, "filled_polygon")
        if not fills:
            lines.append("  no fill")
        for fill_index, fill in enumerate(fills):
            pts = _kid(fill, "pts") or []
            vertices = [c for c in pts[1:] if isinstance(c, list)]
            flags = sorted(
                c[0] for c in fill
                if isinstance(c, list) and c and c[0] not in ("layer", "pts")
            )
            header = (
                f"  fill {fill_index} layer {_tail(_kid(fill, 'layer'))} "
                f"points {len(vertices)}"
            )
            if flags:
                header += " flags " + " ".join(flags)
            lines.append(header)
            for vertex in vertices:
                lines.append("    " + " ".join(t for t in vertex if isinstance(t, str)))
    return ("\n".join(lines) + "\n").encode("utf-8")


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
    # The only artifact with a board suffix is the `refill` answer's refilled board; it is
    # projected to its zone-fill geometry, which is what `expected/<version>/zone-fills.txt`
    # holds. The two sides of that compare are normalized by their own suffixes -- the
    # projection on the way in, plain CRLF folding on the committed `.txt` -- so nothing
    # here has to be idempotent under re-application.
    ".kicad_pcb": project_zone_fills,
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
