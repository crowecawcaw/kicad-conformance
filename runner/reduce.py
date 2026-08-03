"""Canonical reductions for the JSON-comparison ops (`model`, `drc`, `erc`, `netlist`,
`pos`, `ipcd356`, `stats` — DESIGN.md §3b).

For DRC/ERC and netlist, a byte compare is meaningless: formatting, ordering, and
internal IDs (UUIDs, net codes) carry no semantic weight. Each function here reduces
the raw kicad-cli output (parsed JSON for DRC/ERC, parsed `kicadsexpr`/`kicadxml`
netlist text, `stats.json`, `pos.csv`, `board.d356`) to a small, content-sorted,
JSON-serializable structure. `--regenerate` writes exactly this reduced structure as the
expected file (never the raw report — DL-0014) — and at compare time the adapter's
output is reduced the same way and checked for equality.

`runner/summary.py`'s `build_board_summary`/`build_schematic_summary` compose these same
functions into the single `summary` document (DESIGN.md §3b); the functions here also
back the standalone opt-in extras (`pos`, `ipcd356`, `stats`, `netlist` — §5), emitted
un-merged.

Also home to a minimal S-expression reader (`parse_all`/`find_one`/`find_all`) for
KiCad's `.kicad_*` files and `kicadsexpr` netlists -- just enough to walk the `netlist`
export's `(nets ...)` tree for `reduce_netlist`, below, and for `summary.py`'s component
walk. Not a full KiCad format model. Quoted strings are unquoted; everything else
(parens aside) is an opaque atom string. A parenthesized form parses to a Python
``list`` whose first element is normally the form's tag. Stdlib only (DL-0002).
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


@dataclass
class ParseError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def _tokenize(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "(" or c == ")":
            yield c
            i += 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            yield "".join(buf)
            i = j + 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in "()":
            j += 1
        yield text[i:j]
        i = j


def parse_all(text: str) -> list:
    """Parse every top-level form in `text`, returning a list of forms."""
    toks = list(_tokenize(text))
    pos = 0

    def parse_form():
        nonlocal pos
        if pos >= len(toks):
            raise ParseError("unexpected end of input")
        tok = toks[pos]
        pos += 1
        if tok == "(":
            items = []
            while True:
                if pos >= len(toks):
                    raise ParseError("unterminated s-expression")
                if toks[pos] == ")":
                    pos += 1
                    return items
                items.append(parse_form())
        if tok == ")":
            raise ParseError("unexpected ')'")
        return tok

    forms = []
    while pos < len(toks):
        forms.append(parse_form())
    return forms


def find_all(form, tag: str) -> list:
    """Direct children of `form` (a parsed list) whose head atom equals `tag`."""
    return [
        item
        for item in form[1:]
        if isinstance(item, list) and item and item[0] == tag
    ]


def find_one(form, tag: str):
    """First direct child of `form` whose head atom equals `tag`, or None."""
    for item in form[1:]:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def _item_sort_key(item: dict) -> tuple:
    pos = item.get("pos") or {}
    return (item.get("description", ""), pos.get("x"), pos.get("y"))


def _violation_sort_key(v: dict) -> tuple:
    items_key = tuple(sorted((_item_sort_key(i) for i in v.get("items", []))))
    return (v.get("type", ""), v.get("severity", ""), items_key)


def _reduce_violation_list(raw_list: list) -> list:
    out = []
    for v in raw_list:
        items = sorted(
            (
                {"description": i.get("description", ""), "pos": i.get("pos")}
                for i in v.get("items", [])
            ),
            key=_item_sort_key,
        )
        out.append(
            {
                "type": v.get("type", ""),
                "severity": v.get("severity", ""),
                "items": items,
            }
        )
    out.sort(key=_violation_sort_key)
    return out


def reduce_drc(raw: dict) -> dict:
    """DRC JSON (`pcb drc --format json`) -> canonical reduction.

    Drops `date`, `kicad_version`, `source` (an absolute/relative input path),
    `$schema`, `coordinate_units`, `ignored_checks`, `included_severities` — none of
    that is the finding, all of it is run/environment metadata. Sorts violations and
    each violation's `items[]` by *content*, never by UUID (DESIGN §3b: "some
    violation-item UUIDs are minted fresh each run").
    """
    return {
        "violations": _reduce_violation_list(raw.get("violations", [])),
        "unconnected_items": _reduce_violation_list(raw.get("unconnected_items", [])),
        "schematic_parity": _reduce_violation_list(raw.get("schematic_parity", [])),
    }


# ERC JSON (`sch erc --format json`) shares the same top-level violations/severity/items
# shape as DRC in KiCad 10.x, so the same reduction applies. Kept as a distinct name so
# a future divergence in the ERC report shape doesn't silently corrupt DRC's reduction.
reduce_erc = reduce_drc


def reduce_netlist(text: str) -> dict[str, list[str]]:
    """`sch export netlist --format kicadsexpr` -> `{net-name: sorted ["REFDES.PIN", ...]}`
    (DESIGN.md §3b.2) -- the identical member shape the board model's `nets` uses
    (`"REFDES.PAD"`), so the two are directly comparable.

    Ignores net `code` (an arbitrary sequence number), `class`, `pinfunction`/`pintype`
    (derived from the library, not the connectivity), and the netlist's own
    `(design (source ...) (date ...) (tool ...))` header, which embeds an absolute path
    and a wall-clock date.
    """
    forms = parse_all(text)
    root = forms[0]  # ['export', ['version', 'E'], ['design', ...], ...]
    nets_form = find_one(root, "nets")
    result: dict[str, list[str]] = {}
    if nets_form is None:
        return result
    for net in find_all(nets_form, "net"):
        name_form = find_one(net, "name")
        name = name_form[1] if name_form else ""
        members = []
        for node in find_all(net, "node"):
            ref_form = find_one(node, "ref")
            pin_form = find_one(node, "pin")
            ref = ref_form[1] if ref_form else ""
            pin = pin_form[1] if pin_form else ""
            members.append(f"{ref}.{pin}")
        result[name] = sorted(members)
    return result


def reduce_netlist_kicadxml(text: str) -> dict[str, list[str]]:
    """`sch export netlist --format kicadxml` -> the IDENTICAL `{net-name: sorted
    ["REFDES.PIN", ...]}` shape `reduce_netlist` produces from `kicadsexpr`
    (DESIGN.md §3b.2). This is the cross-format-fairness proof: the net->node graph is
    a property of the schematic's *connectivity*, not of which interchange serialization
    carried it, so a second adapter may emit either format and be judged on the identical
    reduced graph.

    Drops the same metadata `reduce_netlist` drops (net `code`, `class`,
    `pinfunction`/`pintype`, and the `<design>` header's absolute path/date/tool) --
    here they are XML attributes/elements instead of s-expr fields, but the same
    semantic content, so they are dropped for the same reason.
    """
    root = ET.fromstring(text)
    nets_el = root.find("nets")
    result: dict[str, list[str]] = {}
    if nets_el is None:
        return result
    for net_el in nets_el.findall("net"):
        name = net_el.get("name", "")
        members = [
            f'{node_el.get("ref", "")}.{node_el.get("pin", "")}'
            for node_el in net_el.findall("node")
        ]
        result[name] = sorted(members)
    return result


# --- stats-json (DESIGN.md §3b.1) ----------------------------------------------


def reduce_stats(raw: dict) -> dict:
    """`pcb export stats --format json` -> canonical reduction (DESIGN.md §3b.1).

    Drops the entire `metadata` object: `date` is wall-clock noise (confirmed the ONLY
    field that differs run-to-run), `generator` is the kicad-cli app-version string (not
    the compatibility key), and `project`/`board_name` leak the adapter's scratch-copy
    filename.

    From `board`, keeps only the three **echoed input values** (`has_outline`,
    `min_track_width`, `min_drill_diameter`) and drops the **computed float geometry**
    (`area`, `front_copper_area`, `back_copper_area`, `front_footprint_area`,
    `back_footprint_area`, `front_component_density`, `back_component_density`,
    `min_track_clearance`, `width`, `height`) -- DESIGN.md §3b.1's explicit rule: keep
    counts and echoed input values, drop computed geometry that two conformant
    implementations could legitimately round differently.

    `components` -> `footprints`: KiCad's own key is `components` (a board's placed
    footprints), and each of `tht`/`smd`/`unspecified`/`total` is itself a
    `{front, back, total}` object -- the model keeps only the aggregate `.total` (the
    front/back split is redundant with `placement`'s per-footprint `side`).

    `pads`/`vias` are kept verbatim -- every value is already a KiCad-printed
    fixed-precision string or integer count, so plain dict/string equality *is* the
    printed-quantum tolerance, no float parsing needed. `drill_holes` is content-sorted
    (key = the full JSON-serialized entry) so hole ordering never matters.
    """
    board = raw.get("board", {})
    components = raw.get("components", {})
    drill_holes = raw.get("drill_holes", [])
    drill_holes_sorted = sorted(drill_holes, key=lambda h: json.dumps(h, sort_keys=True))

    def _total(key: str) -> int:
        return components.get(key, {}).get("total", 0)

    return {
        "board": {
            "has_outline": board.get("has_outline"),
            "min_track_width": board.get("min_track_width"),
            "min_drill_diameter": board.get("min_drill_diameter"),
        },
        "pads": raw.get("pads", {}),
        "vias": raw.get("vias", {}),
        "footprints": {
            "tht": _total("tht"),
            "smd": _total("smd"),
            "unspecified": _total("unspecified"),
            "total": _total("total"),
        },
        "drill_holes": drill_holes_sorted,
    }


# --- pos / placement (DESIGN.md §3b.1) -----------------------------------------


def reduce_pos(text: str) -> dict[str, dict[str, str]]:
    """`pcb export pos --format csv --side both --units mm` -> `{refdes: {val, package,
    x, y, rot, side}}`.

    Rows are keyed by refdes (unique per board), so CSV row order is irrelevant. `x`/
    `y`/`rot` are kept as the PRINTED STRINGS (6 decimal places in mm = exactly 1 nm,
    KiCad's native integer board unit) -- string-exact compare on them *is* the
    printed-quantum tolerance the doc requires (DESIGN.md §3b.1), never a wider float
    band. No byte normalizer is needed upstream of this (the CSV has no timestamp
    header, confirmed byte-identical run-to-run) -- the reduction is the only
    transform, per the honesty rule (DESIGN §4).
    """
    reader = csv.DictReader(io.StringIO(text))
    result: dict[str, dict[str, str]] = {}
    for row in reader:
        ref = row["Ref"]
        result[ref] = {
            "val": row["Val"],
            "package": row["Package"],
            "x": row["PosX"],
            "y": row["PosY"],
            "rot": row["Rot"],
            "side": row["Side"],
        }
    return result


# --- ipcd356 -- board-side net graph (DESIGN.md §3b.1) -------------------------

# One IPC-D-356 "netlist" record. Verified against real kicad-cli 10.0.5 output (`pcb
# export ipcd356`), by exporting every board fixture in `suites/` and tabulating the
# distinct record-type prefixes actually emitted (plus two dedicated probe boards built
# for this, one with an NPTH mounting hole, one with a blind/buried via). Four record
# types were observed, no others:
#
#   - 317 -- a through-hole (plated) feature that carries a net: a through via, or a
#     through-hole pad. `mid` is the literal `VIA`, or `<refdes>  -<pad>`.
#   - 327 -- an SMD feature: no drill segment at all, always carries a net. `mid` is
#     `<refdes>  -<pad>`.
#   - 307 -- a blind or buried via (plated, spans a layer pair narrower than the full
#     stack): same shape as 317 (`mid` is always the literal `VIA`), just a distinct
#     type code. Verified: `307NET-1            VIA        MD0118PA01X+003150Y-003937X0236Y0000R000S3`.
#   - 367 -- a netless feature: an NPTH (`np_thru_hole`, unplated) mounting hole with no
#     electrical net. `net` is the literal `N/C` (IPC-D-356's "no connect" marker, not a
#     real net name) and `mid` is a bare refdes (`H1`), no `-<pad>` suffix. Verified:
#     `367N/C              H1          D0315UA00X+001969Y-001969X0591Y0000R000S0`.
#
# Columns, left to right: record type, net name (whitespace-padded, no fixed width
# relied on here), refdes+pad (or the literal `VIA`, or a bare refdes for a netless
# feature), an optional drill spec -- `<letter-prefix><digits>` followed by a plating
# code, `P` (plated: 317/307's vias and through-hole pads) or `U` (unplated: 367's NPTH
# holes) -- immediately before the mandatory `A<NN>` access-layer code, then X/Y position
# (0.0001 inch units), pad size X/Y, rotation, and a trailing `S<n>` serial.
_IPCD356_RECORD_RE = re.compile(
    r"^(?P<rectype>307|317|327|367)"
    r"(?P<net>\S+)\s+"
    r"(?P<mid>.*?)\s*"
    r"(?:(?P<drill>[A-Z]+\d+)[PU])?"
    r"A(?P<access>\d{2})"
    r"X(?P<x>[+-]\d+)Y(?P<y>[+-]\d+)"
    r"X(?P<sx>\d+)Y(?P<sy>\d+)"
    r"R(?P<rot>\d+)S(?P<serial>\d+)\s*$"
)
# The "mid" field is either the literal `VIA` or `<refdes>    -<pad>` (refdes and pad
# number separated by whitespace + a hyphen).
_IPCD356_PAD_RE = re.compile(r"^(?P<ref>\S+?)\s*-(?P<pad>\S+)$")


def reduce_ipcd356(text: str) -> dict:
    """`pcb export ipcd356` -> two membership structures (DESIGN.md §3b.1):

    1. `nets`: `{net-name: sorted [[refdes, pad], ...]}` -- the BOARD's own
       connectivity, mirroring `reduce_netlist`'s shape so the two are directly
       comparable. A board that routes a pad to the wrong net diverges here even if
       the schematic netlist is right. `VIA` features (rectype 317 or 307 -- through,
       blind, or buried) contribute the net but no `(refdes, pad)` member (a via isn't
       a component pin). A netless feature (rectype 367 -- an NPTH mounting hole) is
       NOT a member of `nets` at all: its `net` field is IPC-D-356's `N/C` ("no
       connect") marker, not a real net name, and inventing a `"N/C"` net would wrongly
       claim every NPTH hole on a board is electrically the same net.
    2. `testpoints`: `{"refdes:pad": {"x", "y", "access"}}` in the printed 0.0001-inch
       (0.1-mil) quantum straight off the file -- optional test-point geometry for
       cases that assert access-point placement. A netless (367) feature's geometry is
       still real board data, so it IS recorded here (keyed by its bare refdes, pad
       ""), just kept out of `nets`.

    The trailing `S<n>` serial and rotation are dropped entirely (never compared) --
    per DESIGN §4's IPC-D-356 normalizer note, the serial is stable on VIA records on
    some boards but not guaranteed to be, and the reduction keys on net+refdes/pad
    membership, which is robust either way.
    """
    net_graph: dict[str, list[list[str]]] = {}
    testpoints: dict[str, dict[str, object]] = {}
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("P ") or line.strip() == "999":
            continue  # parameter header lines / EOF sentinel, not netlist records
        m = _IPCD356_RECORD_RE.match(line)
        if not m:
            raise ValueError(f"unrecognized IPC-D-356 record: {line!r}")
        rectype = m.group("rectype")
        mid = m.group("mid").strip()
        if rectype == "367":
            # Netless feature (e.g. an np_thru_hole/NPTH mounting hole): `net` is the
            # literal `N/C`, not a real net -- do not add it to net_graph at all. `mid`
            # is a bare refdes (no `-<pad>`), but reuse the same ref/pad split for
            # uniformity (falls through to (mid, "") since there's no hyphen).
            pad_m = _IPCD356_PAD_RE.match(mid)
            ref, pad = (pad_m.group("ref"), pad_m.group("pad")) if pad_m else (mid, "")
            testpoints[f"{ref}:{pad}"] = {
                "x": int(m.group("x")),
                "y": int(m.group("y")),
                "access": m.group("access"),
            }
            continue
        net = m.group("net")
        net_graph.setdefault(net, [])
        if mid == "VIA":
            continue
        pad_m = _IPCD356_PAD_RE.match(mid)
        ref, pad = (pad_m.group("ref"), pad_m.group("pad")) if pad_m else (mid, "")
        net_graph[net].append([ref, pad])
        testpoints[f"{ref}:{pad}"] = {
            "x": int(m.group("x")),
            "y": int(m.group("y")),
            "access": m.group("access"),
        }
    for members in net_graph.values():
        members.sort()
    return {"nets": net_graph, "testpoints": testpoints}


def describe_structured_mismatch(expected: Any, actual: Any) -> list[str]:
    """Characterize *how* a structured compare differs (DESIGN §3b: "residue is
    characterized, not hidden") instead of just reporting an opaque "differs"."""
    notes: list[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_keys, actual_keys = set(expected), set(actual)
        if expected_keys != actual_keys:
            missing = expected_keys - actual_keys
            extra = actual_keys - expected_keys
            if missing:
                notes.append(f"missing keys: {sorted(missing)}")
            if extra:
                notes.append(f"unexpected extra keys: {sorted(extra)}")
        for key in sorted(expected_keys & actual_keys):
            if expected[key] != actual[key]:
                if isinstance(expected[key], list) and isinstance(actual[key], list):
                    if len(expected[key]) != len(actual[key]):
                        notes.append(
                            f"{key!r}: count differs "
                            f"(expected {len(expected[key])}, got {len(actual[key])})"
                        )
                    else:
                        notes.append(f"{key!r}: membership differs")
                else:
                    notes.append(f"{key!r}: differs")
    else:
        notes.append("differs")
    return notes or ["differs (no further detail available)"]
