"""Canonical reductions for `compare = "structured"` checks (DESIGN.md §3b, DL-0014).

For DRC/ERC and netlist, a byte compare is meaningless: formatting, ordering, and
internal IDs (UUIDs, net codes) carry no semantic weight. Each function here reduces
the raw kicad-cli output (parsed JSON for DRC/ERC, parsed `kicadsexpr` netlist text) to
a small, content-sorted, JSON-serializable structure. `--regenerate` writes exactly this
reduced structure as the golden (never the raw report) — see DL-0014 — and at compare
time the adapter's output is reduced the same way and checked for equality.

This module also carries the L2 reductions VALIDATION.md §3 adds: `reduce_stats`
(§3.3), `reduce_pos` (§3.4), `reduce_ipcd356` (§3.5), and `reduce_netlist_kicadxml`
(§3.1's cross-format extension of the existing `reduce_netlist`).
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from runner import sexpr


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


def reduce_netlist(text: str) -> dict[str, list[list[str]]]:
    """`sch export netlist --format kicadsexpr` -> `{net-name: sorted [[ref, pin], ...]}`.

    Ignores net `code` (an arbitrary sequence number), `class`, `pinfunction`/`pintype`
    (derived from the library, not the connectivity), and the netlist's own
    `(design (source ...) (date ...) (tool ...))` header, which embeds an absolute path
    and a wall-clock date (DESIGN §3b) — this is why netlist is always `structured`,
    never `golden-file`.
    """
    forms = sexpr.parse_all(text)
    root = forms[0]  # ['export', ['version', 'E'], ['design', ...], ...]
    nets_form = sexpr.find_one(root, "nets")
    result: dict[str, list[list[str]]] = {}
    if nets_form is None:
        return result
    for net in sexpr.find_all(nets_form, "net"):
        name_form = sexpr.find_one(net, "name")
        name = name_form[1] if name_form else ""
        members = []
        for node in sexpr.find_all(net, "node"):
            ref_form = sexpr.find_one(node, "ref")
            pin_form = sexpr.find_one(node, "pin")
            ref = ref_form[1] if ref_form else ""
            pin = pin_form[1] if pin_form else ""
            members.append([ref, pin])
        members.sort()
        result[name] = members
    return result


def reduce_netlist_kicadxml(text: str) -> dict[str, list[list[str]]]:
    """`sch export netlist --format kicadxml` -> the IDENTICAL `{net-name: sorted
    [[ref, pin], ...]}` shape `reduce_netlist` produces from `kicadsexpr` (VALIDATION.md
    §3.1). This is the cross-format-fairness proof: the net->node graph is a property of
    the schematic's *connectivity*, not of which interchange serialization carried it, so
    a second adapter may emit either format and be judged on the identical reduced graph.

    Drops the same metadata `reduce_netlist` drops (net `code`, `class`,
    `pinfunction`/`pintype`, and the `<design>` header's absolute path/date/tool) --
    here they are XML attributes/elements instead of s-expr fields, but the same
    semantic content, so they are dropped for the same reason.
    """
    root = ET.fromstring(text)
    nets_el = root.find("nets")
    result: dict[str, list[list[str]]] = {}
    if nets_el is None:
        return result
    for net_el in nets_el.findall("net"):
        name = net_el.get("name", "")
        members = [
            [node_el.get("ref", ""), node_el.get("pin", "")]
            for node_el in net_el.findall("node")
        ]
        members.sort()
        result[name] = members
    return result


# --- stats-json (VALIDATION.md §3.3) ----------------------------------------------


def reduce_stats(raw: dict) -> dict:
    """`pcb export stats --format json` -> canonical reduction.

    Drops the entire `metadata` object: `date` is wall-clock noise (confirmed the ONLY
    field that differs run-to-run), `generator` is the kicad-cli app-version string (not
    the compatibility key DL-0005 keeps), and `project`/`board_name` leak the adapter's
    scratch-copy filename. Keeps `board`/`pads`/`vias`/`components` verbatim -- every
    value is already a KiCad-printed fixed-precision string (`"0.2500 mm"`), so plain
    dict/string equality *is* the printed-quantum tolerance (DESIGN §3d), no float
    parsing needed. `drill_holes` is content-sorted (key = the full JSON-serialized
    entry) so hole ordering never matters.
    """
    drill_holes = raw.get("drill_holes", [])
    drill_holes_sorted = sorted(drill_holes, key=lambda h: json.dumps(h, sort_keys=True))
    return {
        "board": raw.get("board", {}),
        "pads": raw.get("pads", {}),
        "vias": raw.get("vias", {}),
        "components": raw.get("components", {}),
        "drill_holes": drill_holes_sorted,
    }


# --- pos / placement (VALIDATION.md §3.4) -----------------------------------------


def reduce_pos(text: str) -> dict[str, dict[str, str]]:
    """`pcb export pos --format csv --side both --units mm` -> `{refdes: {val, package,
    x, y, rot, side}}`.

    Rows are keyed by refdes (unique per board), so CSV row order is irrelevant. `x`/
    `y`/`rot` are kept as the PRINTED STRINGS (6 decimal places in mm = exactly 1 nm,
    KiCad's native integer board unit) -- string-exact compare on them *is* the
    printed-quantum tolerance the doc requires (VALIDATION §3.4), never a wider float
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


# --- ipcd356 -- board-side net graph (VALIDATION.md §3.5) -------------------------

# One IPC-D-356 "netlist" record (type 317 = through-hole/via feature that carries a
# drill spec, 327 = SMD feature, no drill). Verified against real kicad-cli 10.0.5
# output (`pcb export ipcd356`); columns, left to right: record type, net name
# (whitespace-padded, no fixed width relied on here), refdes+pad (or the literal `VIA`
# for a via feature), an optional drill-diameter spec ending in `P` immediately before
# the mandatory `A<NN>` access-layer code, then X/Y position (0.0001 inch units), pad
# size X/Y, rotation, and a trailing `S<n>` serial.
_IPCD356_RECORD_RE = re.compile(
    r"^(?P<rectype>317|327)"
    r"(?P<net>\S+)\s+"
    r"(?P<mid>.*?)\s*"
    r"(?:(?P<drill>[A-Z]+\d+)P)?"
    r"A(?P<access>\d{2})"
    r"X(?P<x>[+-]\d+)Y(?P<y>[+-]\d+)"
    r"X(?P<sx>\d+)Y(?P<sy>\d+)"
    r"R(?P<rot>\d+)S(?P<serial>\d+)\s*$"
)
# The "mid" field is either the literal `VIA` or `<refdes>    -<pad>` (refdes and pad
# number separated by whitespace + a hyphen).
_IPCD356_PAD_RE = re.compile(r"^(?P<ref>\S+?)\s*-(?P<pad>\S+)$")


def reduce_ipcd356(text: str) -> dict:
    """`pcb export ipcd356` -> two membership structures (VALIDATION.md §3.5):

    1. `nets`: `{net-name: sorted [[refdes, pad], ...]}` -- the BOARD's own
       connectivity, mirroring `reduce_netlist`'s shape so the two are directly
       comparable. A board that routes a pad to the wrong net diverges here even if
       the schematic netlist is right. `VIA` features contribute the net but no
       `(refdes, pad)` member (a via isn't a component pin).
    2. `testpoints`: `{"refdes:pad": {"x", "y", "access"}}` in the printed 0.0001-inch
       (0.1-mil) quantum straight off the file -- optional test-point geometry for
       cases that assert access-point placement.

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
        net = m.group("net")
        mid = m.group("mid").strip()
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
