"""The composite `model` verb (VALIDATION.md §4, DL-0022): one normalized JSON document
per input, merging several `kicad-cli` exports for a board or a schematic.

`build_board_model` and `build_schematic_model` are pure functions over already-read
export output (the adapter -- `runner/adapters/kicad.py`'s `cmd_model` -- is what
actually shells out to `kicad-cli` and reads the files back; this module never touches a
subprocess). They reuse the raw parsers in `runner/reduce.py` (`reduce_stats`,
`reduce_pos`, `reduce_ipcd356`, `reduce_netlist`, `reduce_netlist_kicadxml`) and rename/
reshape their output into the exact schema VALIDATION.md §4.1/§4.2 documents.

Every list here is content-sorted before being handed back (`nets`' member arrays,
`components[ref].pins`), because the eventual JSON-equality compare (`runner/engine.py`)
is sensitive to list order -- the "sorted keys, content-sorted lists" rule in
VALIDATION.md §4.0 is enforced HERE, not just cosmetically at serialization time.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from runner import reduce, sexpr


# --- board model (VALIDATION.md §4.1) ---------------------------------------------


def build_board_model(stats_json: dict, pos_csv_text: str, d356_text: str) -> dict:
    """Compose `pcb export stats` + `pcb export pos` + `pcb export ipcd356` into the
    board model. `stats_json` is the parsed raw `stats.json` (this function applies
    `reduce.reduce_stats`, which is where the excluded float areas/densities/
    min_track_clearance/width/height are dropped -- VALIDATION.md §4.1); `pos_csv_text`
    and `d356_text` are the raw text of `pos.csv` / `board.d356`.
    """
    stats = reduce.reduce_stats(stats_json)
    pos = reduce.reduce_pos(pos_csv_text)
    ipc = reduce.reduce_ipcd356(d356_text)

    placement = {
        ref: {
            "value": row["val"],
            "package": row["package"],
            "x": row["x"],
            "y": row["y"],
            "rotation": row["rot"],
            "side": row["side"],
        }
        for ref, row in pos.items()
    }

    nets = {
        name: sorted(f"{ref}.{pad}" for ref, pad in members)
        for name, members in ipc["nets"].items()
    }

    return {
        "kind": "board",
        "has_outline": stats["board"]["has_outline"],
        "min_track_width": stats["board"]["min_track_width"],
        "min_drill_diameter": stats["board"]["min_drill_diameter"],
        "counts": {
            "footprints": stats["footprints"],
            "pads": stats["pads"],
            "vias": stats["vias"],
        },
        "drill_holes": stats["drill_holes"],
        "placement": placement,
        "nets": nets,
    }


# --- schematic model (VALIDATION.md §4.2) -----------------------------------------


def _field_value(field_form: list) -> str:
    """A `(field (name "Footprint") "value")` s-expr field's trailing bare atom, if
    present (no value present -> `""`, matching `Footprint`'s unset-field encoding)."""
    for item in field_form[1:]:
        if isinstance(item, str):
            return item
    return ""


def _sexpr_components(root: list) -> dict:
    comps_form = sexpr.find_one(root, "components")
    result: dict[str, dict] = {}
    if comps_form is None:
        return result
    for comp in sexpr.find_all(comps_form, "comp"):
        ref_form = sexpr.find_one(comp, "ref")
        ref = ref_form[1] if ref_form else ""
        value_form = sexpr.find_one(comp, "value")
        value = value_form[1] if value_form else ""

        part = ""
        libsource = sexpr.find_one(comp, "libsource")
        if libsource is not None:
            part_form = sexpr.find_one(libsource, "part")
            part = part_form[1] if part_form else ""

        footprint = ""
        fields_form = sexpr.find_one(comp, "fields")
        if fields_form is not None:
            for f in sexpr.find_all(fields_form, "field"):
                name_form = sexpr.find_one(f, "name")
                if name_form and name_form[1] == "Footprint":
                    footprint = _field_value(f)

        sheet = ""
        sheetpath = sexpr.find_one(comp, "sheetpath")
        if sheetpath is not None:
            names_form = sexpr.find_one(sheetpath, "names")
            sheet = names_form[1] if names_form else ""

        pins: list[str] = []
        units_form = sexpr.find_one(comp, "units")
        if units_form is not None:
            for unit in sexpr.find_all(units_form, "unit"):
                pins_form = sexpr.find_one(unit, "pins")
                if pins_form is not None:
                    for pin in sexpr.find_all(pins_form, "pin"):
                        num_form = sexpr.find_one(pin, "num")
                        if num_form:
                            pins.append(num_form[1])

        result[ref] = {
            "value": value,
            "part": part,
            "footprint": footprint,
            "sheet": sheet,
            "pins": sorted(pins),
        }
    return result


def _xml_components(root: ET.Element) -> dict:
    result: dict[str, dict] = {}
    components_el = root.find("components")
    if components_el is None:
        return result
    for comp_el in components_el.findall("comp"):
        ref = comp_el.get("ref", "")
        value_el = comp_el.find("value")
        value = (value_el.text or "") if value_el is not None else ""

        libsource_el = comp_el.find("libsource")
        part = libsource_el.get("part", "") if libsource_el is not None else ""

        footprint = ""
        fields_el = comp_el.find("fields")
        if fields_el is not None:
            for field_el in fields_el.findall("field"):
                if field_el.get("name") == "Footprint":
                    footprint = field_el.text or ""

        sheetpath_el = comp_el.find("sheetpath")
        sheet = sheetpath_el.get("names", "") if sheetpath_el is not None else ""

        pins: list[str] = []
        units_el = comp_el.find("units")
        if units_el is not None:
            for unit_el in units_el.findall("unit"):
                pins_el = unit_el.find("pins")
                if pins_el is not None:
                    for pin_el in pins_el.findall("pin"):
                        num = pin_el.get("num")
                        if num is not None:
                            pins.append(num)

        result[ref] = {
            "value": value,
            "part": part,
            "footprint": footprint,
            "sheet": sheet,
            "pins": sorted(pins),
        }
    return result


def build_schematic_model(netlist_text: str, fmt: Optional[str] = None) -> dict:
    """Compose `sch export netlist` (either interchange format) into the schematic
    model. `fmt` is `"kicadxml"` or `None`/`"kicadsexpr"` (the default) -- VALIDATION.md
    §4.2's cross-format-fairness proof: both must produce the IDENTICAL model.
    """
    fmt = fmt or "kicadsexpr"
    if fmt == "kicadxml":
        root = ET.fromstring(netlist_text)
        components = _xml_components(root)
        nets = reduce.reduce_netlist_kicadxml(netlist_text)
    else:
        forms = sexpr.parse_all(netlist_text)
        root = forms[0]
        components = _sexpr_components(root)
        nets = reduce.reduce_netlist(netlist_text)

    return {
        "kind": "schematic",
        "components": components,
        "nets": nets,
    }
