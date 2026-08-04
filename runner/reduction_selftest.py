"""Guard against a `reduce_erc = reduce_drc`-shaped regression (found and fixed in
`runner/reduce.py`, 2026-08): a reduction whose alias/shape silently stops reflecting
its actual input, so every case exercising it "passes" no matter what the oracle really
reported -- "a test that cannot fail is not evidence" (ROADMAP.md), extended here to the
reduction functions themselves, not just the cases built on top of them.

This needs no adapter, no Docker, no `suites/` fixture, and no kicad-cli -- every input
below is a small hand-built structure in the exact real shape each `reduce_*`/
`build_*_summary` function consumes (verified against real `kicad-cli 10.0.5` output
where the shape was in doubt -- see `reduce.reduce_erc`'s docstring for the ERC case).
That is deliberate: it must be fast enough and dependency-free enough that nobody has an
excuse to skip it, and it must keep working even where Docker/kicad-cli is unavailable.

Each check feeds its reduction two DIFFERENT non-empty inputs (`_a`, `_b`) and one
EMPTY/no-findings input (`_empty`), then asserts:

  1. reduce(_a) != reduce(_empty)  -- a real finding doesn't collapse to the same
     result as no findings at all (the exact shape of the proven bug: `reduce_erc`
     silently returned the all-empty DRC shape for every real ERC report).
  2. reduce(_a) != reduce(_b)      -- the reduction is sensitive to *content*, not just
     to presence/absence (catches a reduction that always returns some fixed non-empty
     placeholder, which would pass check 1 while still asserting nothing real).

A reduction that cannot be made to fail either assertion is broken or asserting
nothing -- exactly the class DESIGN.md's "coverage is what we can assert and verify, not
just run" is about.
"""
from __future__ import annotations

from dataclasses import dataclass

from runner import reduce, summary


@dataclass
class SelftestOutcome:
    label: str
    ok: bool
    detail: str = ""


class _SelftestFailure(Exception):
    pass


def _check(label: str, condition: bool, detail: str) -> None:
    if not condition:
        raise _SelftestFailure(f"{label}: {detail}")


# --- DRC --------------------------------------------------------------------------

_DRC_EMPTY = {"violations": [], "unconnected_items": [], "schematic_parity": []}
_DRC_A = {
    "violations": [
        {"type": "clearance", "severity": "error",
         "items": [{"description": "Pad A", "pos": {"x": 1.0, "y": 2.0}, "uuid": "u-a"}]}
    ],
    "unconnected_items": [], "schematic_parity": [],
}
_DRC_B = {
    "violations": [
        {"type": "silk_over_copper", "severity": "warning",
         "items": [{"description": "Pad B", "pos": {"x": 5.0, "y": 6.0}, "uuid": "u-b"}]}
    ],
    "unconnected_items": [], "schematic_parity": [],
}


def _check_drc() -> None:
    empty, a, b = reduce.reduce_drc(_DRC_EMPTY), reduce.reduce_drc(_DRC_A), reduce.reduce_drc(_DRC_B)
    _check("drc", a != empty, f"a non-empty DRC report reduced to the same thing as an empty one: {a!r}")
    _check("drc", a != b, "two DRC reports with different violations reduced to the same result")
    _check("drc", len(a["violations"]) == 1, f"expected exactly one violation, got {a['violations']!r}")


# --- ERC -- the exact shape of the proven bug --------------------------------------
#
# `_ERC_A`'s only violation is NOT under a top-level "violations" key (there is none --
# see `reduce.reduce_erc`'s docstring) -- it is nested under `sheets[0].violations`. The
# original bug (`reduce_erc = reduce_drc`) called `raw.get("violations", [])`, which is
# absent here, so it would silently return `{"violations": [], ...}` for `_ERC_A` too,
# identical to `_ERC_EMPTY` -- exactly what `_check_drc_alias_regression` below re-checks
# by name, and what the plain `a != empty` assertion here already catches.

_ERC_EMPTY = {"sheets": [{"path": "/", "uuid_path": "/root", "violations": []}]}
_ERC_A = {
    "sheets": [
        {"path": "/", "uuid_path": "/root", "violations": [
            {"type": "pin_not_connected", "severity": "error",
             "items": [{"description": "Symbol U1 Pin 2", "pos": {"x": 1.0, "y": 2.0}, "uuid": "u-a"}]}
        ]},
    ],
}
_ERC_B = {
    "sheets": [
        {"path": "/", "uuid_path": "/root", "violations": []},
        {"path": "/sub/", "uuid_path": "/root/sub", "violations": [
            {"type": "pin_not_connected", "severity": "error",
             "items": [{"description": "Symbol U2 Pin 2", "pos": {"x": 3.0, "y": 4.0}, "uuid": "u-b"}]}
        ]},
    ],
}


def _check_erc() -> None:
    empty, a, b = reduce.reduce_erc(_ERC_EMPTY), reduce.reduce_erc(_ERC_A), reduce.reduce_erc(_ERC_B)
    _check("erc", a != empty, f"a non-empty ERC report reduced to the same thing as an empty one: {a!r}")
    _check("erc", a != b, "two ERC reports with different sheets/violations reduced to the same result")
    _check("erc", len(a["violations"]) == 1, f"expected exactly one violation, got {a['violations']!r}")
    _check("erc", b["violations"][0]["sheet"] == "/sub/",
           f"violation on sub-sheet was not stamped with its sheet path: {b['violations'][0]!r}")
    # The regression this whole module exists to catch, named explicitly: `reduce_erc`
    # must not be (or behave like) `reduce_drc` -- DRC reads a top-level "violations"
    # key that ERC's report never has.
    _check("erc", reduce.reduce_erc is not reduce.reduce_drc,
           "reduce_erc has been aliased back to reduce_drc")


# --- stats --------------------------------------------------------------------------

_STATS_EMPTY = {"board": {}, "components": {}, "pads": {}, "vias": {}, "drill_holes": []}
_STATS_A = {
    "board": {"has_outline": True, "min_track_width": "0.25mm", "min_drill_diameter": "0.3mm", "area": 100.0},
    "components": {"tht": {"total": 1}, "smd": {"total": 2}, "unspecified": {"total": 0}, "total": {"total": 3}},
    "pads": {"through_hole": 2, "smd": 4},
    "vias": {"through": 1},
    "drill_holes": [{"count": 1, "shape": "Round"}],
}
_STATS_B = {
    "board": {"has_outline": False, "min_track_width": "0.15mm", "min_drill_diameter": "0.2mm", "area": 50.0},
    "components": {"tht": {"total": 0}, "smd": {"total": 0}, "unspecified": {"total": 0}, "total": {"total": 0}},
    "pads": {"through_hole": 0, "smd": 0},
    "vias": {"through": 0},
    "drill_holes": [],
}


def _check_stats() -> None:
    empty, a, b = reduce.reduce_stats(_STATS_EMPTY), reduce.reduce_stats(_STATS_A), reduce.reduce_stats(_STATS_B)
    _check("stats", a != empty, "a populated stats.json reduced to the same thing as an empty one")
    _check("stats", a != b, "two different stats.json reduced to the same result")
    _check("stats", a["footprints"]["total"] == 3, f"footprint total not carried through: {a['footprints']!r}")


# --- pos ------------------------------------------------------------------------------

_POS_EMPTY = "Ref,Val,Package,PosX,PosY,Rot,Side\n"
_POS_A = _POS_EMPTY + "R1,10k,R_0603,1.000000,2.000000,0,top\n"
_POS_B = _POS_EMPTY + "C1,100nF,C_0603,3.000000,4.000000,90,bottom\n"


def _check_pos() -> None:
    empty, a, b = reduce.reduce_pos(_POS_EMPTY), reduce.reduce_pos(_POS_A), reduce.reduce_pos(_POS_B)
    _check("pos", a != empty, "a pos.csv with one row reduced to the same thing as an empty one")
    _check("pos", a != b, "two pos.csv with different rows reduced to the same result")
    _check("pos", "R1" in a and "R1" not in b, f"refdes keying is not input-dependent: {a!r} vs {b!r}")


# --- ipcd356 --------------------------------------------------------------------------
#
# Lines in the real fixed grammar `reduce.reduce_ipcd356` parses (module docstring):
# 317/307 (through/blind-buried via or through-hole pad), 327 (SMD, no drill segment),
# 367 (netless NPTH -- contributes a testpoint but no net membership).

_IPC_EMPTY = ""
_IPC_A = (
    "317NET-A            VIA        MD0100PA01X+001000Y-001000X0100Y0100R000S1\n"
    "327NET-B            R1    -1          A02X+002000Y-002000X0200Y0200R000S2\n"
)
_IPC_B = "367N/C              H1          D0200UA00X+003000Y-003000X0300Y0300R000S3\n"


def _check_ipcd356() -> None:
    empty, a, b = reduce.reduce_ipcd356(_IPC_EMPTY), reduce.reduce_ipcd356(_IPC_A), reduce.reduce_ipcd356(_IPC_B)
    _check("ipcd356", a != empty, "a non-empty IPC-D-356 export reduced to the same thing as an empty one")
    _check("ipcd356", a != b, "two different IPC-D-356 exports reduced to the same result")
    _check("ipcd356", a["nets"].get("NET-B") == [["R1", "1"]], f"SMD (327) net membership missing: {a['nets']!r}")
    _check("ipcd356", "NET-A" in a["nets"] and a["nets"]["NET-A"] == [],
           f"a VIA-only (317) net should contribute the net with no member: {a['nets']!r}")
    _check("ipcd356", b["nets"] == {}, f"a netless NPTH (367) record must not appear in nets: {b['nets']!r}")
    _check("ipcd356", any(k.startswith("H1") for k in b["testpoints"]),
           f"a netless NPTH (367) record must still appear in testpoints: {b['testpoints']!r}")


# --- netlist (kicadsexpr / kicadxml) ---------------------------------------------------

_NET_SEXPR_EMPTY = '(export (version "E"))'
_NET_SEXPR_A = (
    '(export (version "E") (nets '
    '(net (code "1") (name "NetA") (node (ref "R1") (pin "1")) (node (ref "R2") (pin "2")))'
    '))'
)
_NET_SEXPR_B = (
    '(export (version "E") (nets '
    '(net (code "1") (name "NetB") (node (ref "U1") (pin "3")))'
    '))'
)
_NET_XML_A = '<export><nets><net name="NetA"><node ref="R1" pin="1"/><node ref="R2" pin="2"/></net></nets></export>'
_NET_XML_EMPTY = "<export><nets></nets></export>"


def _check_netlist() -> None:
    empty, a, b = (
        reduce.reduce_netlist(_NET_SEXPR_EMPTY),
        reduce.reduce_netlist(_NET_SEXPR_A),
        reduce.reduce_netlist(_NET_SEXPR_B),
    )
    _check("netlist", a != empty, "a netlist with one net reduced to the same thing as an empty one")
    _check("netlist", a != b, "two netlists with different nets reduced to the same result")
    _check("netlist", a.get("NetA") == ["R1.1", "R2.2"], f"net membership not carried through: {a!r}")

    xml_empty, xml_a = reduce.reduce_netlist_kicadxml(_NET_XML_EMPTY), reduce.reduce_netlist_kicadxml(_NET_XML_A)
    _check("netlist-kicadxml", xml_a != xml_empty,
           "a kicadxml netlist with one net reduced to the same thing as an empty one")
    _check("netlist-kicadxml", xml_a == a,
           f"kicadsexpr and kicadxml of the same net graph reduced differently: {a!r} vs {xml_a!r}")


# --- summary (board + schematic) -------------------------------------------------------


def _check_board_summary() -> None:
    empty = summary.build_board_summary(_STATS_EMPTY, _POS_EMPTY, _IPC_EMPTY)
    a = summary.build_board_summary(_STATS_A, _POS_A, _IPC_A)
    b = summary.build_board_summary(_STATS_B, _POS_B, _IPC_B)
    _check("summary(board)", a != empty, "a populated board summary reduced to the same thing as an empty one")
    _check("summary(board)", a != b, "two different board exports produced the same board summary")


def _check_schematic_summary() -> None:
    empty = summary.build_schematic_summary(_NET_SEXPR_EMPTY)
    a = summary.build_schematic_summary(_NET_SEXPR_A)
    b = summary.build_schematic_summary(_NET_SEXPR_B)
    _check("summary(sch)", a != empty, "a populated schematic summary reduced to the same thing as an empty one")
    _check("summary(sch)", a != b, "two different netlists produced the same schematic summary")
    # Cross-format fairness (DESIGN.md §3b/§4.2): the kicadxml reader must compose to the
    # IDENTICAL summary for the identical underlying net graph.
    xml_a = summary.build_schematic_summary(_NET_XML_A, fmt="kicadxml")
    _check("summary(sch)", xml_a == a,
           f"kicadsexpr and kicadxml summaries of the same schematic differed: {a!r} vs {xml_a!r}")


_CHECKS = [
    ("drc", _check_drc),
    ("erc", _check_erc),
    ("stats", _check_stats),
    ("pos", _check_pos),
    ("ipcd356", _check_ipcd356),
    ("netlist", _check_netlist),
    ("summary(board)", _check_board_summary),
    ("summary(sch)", _check_schematic_summary),
]


def run_reduction_selftest() -> list[SelftestOutcome]:
    """Run every reduction guard and return one outcome per reduction family. Never
    raises -- a failing check is reported as `ok=False` with the assertion detail."""
    outcomes: list[SelftestOutcome] = []
    for label, fn in _CHECKS:
        try:
            fn()
        except _SelftestFailure as e:
            outcomes.append(SelftestOutcome(label, ok=False, detail=str(e)))
        except Exception as e:  # pragma: no cover - a bug in the check itself, not the reduction
            outcomes.append(SelftestOutcome(label, ok=False, detail=f"{label}: selftest itself raised {e!r}"))
        else:
            outcomes.append(SelftestOutcome(label, ok=True, detail=f"{label}: input-dependent, non-trivial"))
    return outcomes
