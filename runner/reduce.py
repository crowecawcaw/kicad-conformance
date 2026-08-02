"""Canonical reductions for `compare = "structured"` checks (DESIGN.md §3b, DL-0014).

For DRC/ERC and netlist, a byte compare is meaningless: formatting, ordering, and
internal IDs (UUIDs, net codes) carry no semantic weight. Each function here reduces
the raw kicad-cli output (parsed JSON for DRC/ERC, parsed `kicadsexpr` netlist text) to
a small, content-sorted, JSON-serializable structure. `--regenerate` writes exactly this
reduced structure as the golden (never the raw report) — see DL-0014 — and at compare
time the adapter's output is reduced the same way and checked for equality.
"""
from __future__ import annotations

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
