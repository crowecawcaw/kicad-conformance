"""The cheap coverage proxy (DESIGN.md §7a) -- CLI-surface + format-token bookkeeping
over files the suite already has, with zero KiCad rebuild. This is what M0 relies on
for gap-finding; heavy line-coverage is a separate, later, scheduled effort (§7b, M6).

Two tiers:

- CLI-surface coverage: which adapter verbs (== kicad-cli subcommands, per
  `runner/verbs.py`) the discovered cases actually exercise, and which extra flags
  (`args =`) show up. Unexercised verbs are the gap list.
- Format-token coverage: which top-level s-expr sections (e.g. `(net ...)`,
  `(footprint ...)`, `(zone ...)`) and which format `(version YYYYMMDD)` epochs appear
  across the fixtures + goldens the suite carries.

This is honestly a proxy (DESIGN §7a): it shows which surfaces are *touched*, not which
KiCad source lines *run*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from runner import sexpr
from runner.manifest import Case
from runner.verbs import VERB_TABLE

# A hand-maintained reference list of top-level sections worth watching for, per format
# (DESIGN §7a names `lib_symbols`, `net`, `footprint`, `zone` as examples). This is
# necessarily incomplete -- it is a proxy, not a spec -- and is meant to be extended as
# the suite grows past M0.
_SCH_SECTIONS_OF_INTEREST = {
    "lib_symbols", "wire", "junction", "label", "global_label", "hierarchical_label",
    "no_connect", "bus", "bus_entry", "sheet", "sheet_instances", "symbol", "text",
    "polyline", "image",
}
_PCB_SECTIONS_OF_INTEREST = {
    "layers", "setup", "net", "footprint", "zone", "via", "segment", "gr_line",
    "gr_rect", "gr_circle", "gr_arc", "gr_poly", "dimension", "group", "embedded_fonts",
}

_VERSION_TOKEN_RE = re.compile(r"^\s*\(version\s+(\d{8})\)\s*$", re.MULTILINE)


@dataclass
class CoverageReport:
    exercised_verbs: set[str] = field(default_factory=set)
    exercised_flags: dict[str, set[str]] = field(default_factory=dict)
    sch_sections_seen: set[str] = field(default_factory=set)
    pcb_sections_seen: set[str] = field(default_factory=set)
    format_versions_seen: set[str] = field(default_factory=set)

    def unexercised_verbs(self) -> list[str]:
        return sorted(set(VERB_TABLE) - self.exercised_verbs)

    def unexercised_sch_sections(self) -> list[str]:
        return sorted(_SCH_SECTIONS_OF_INTEREST - self.sch_sections_seen)

    def unexercised_pcb_sections(self) -> list[str]:
        return sorted(_PCB_SECTIONS_OF_INTEREST - self.pcb_sections_seen)

    def render(self) -> str:
        lines = ["Coverage proxy (DESIGN.md §7a -- CLI surface + format tokens; not source-line coverage)", ""]
        lines.append(f"Verbs exercised ({len(self.exercised_verbs)}/{len(VERB_TABLE)}): "
                      f"{', '.join(sorted(self.exercised_verbs)) or '(none)'}")
        gaps = self.unexercised_verbs()
        lines.append(f"Verbs NOT exercised (gap list): {', '.join(gaps) or '(none)'}")
        if self.exercised_flags:
            lines.append("Extra flags (`args =`) seen, per verb:")
            for verb in sorted(self.exercised_flags):
                lines.append(f"  {verb}: {', '.join(sorted(self.exercised_flags[verb]))}")
        lines.append("")
        lines.append(f"Schematic top-level sections seen: {', '.join(sorted(self.sch_sections_seen)) or '(none)'}")
        lines.append(f"  not yet exercised (gap list): {', '.join(self.unexercised_sch_sections()) or '(none)'}")
        lines.append(f"Board top-level sections seen: {', '.join(sorted(self.pcb_sections_seen)) or '(none)'}")
        lines.append(f"  not yet exercised (gap list): {', '.join(self.unexercised_pcb_sections()) or '(none)'}")
        lines.append(f"Format `(version YYYYMMDD)` tokens seen: {', '.join(sorted(self.format_versions_seen)) or '(none)'}")
        return "\n".join(lines)


def build_coverage_report(cases: list[Case]) -> CoverageReport:
    report = CoverageReport()
    for case in cases:
        for check in case.checks:
            report.exercised_verbs.add(check.op)
            if check.args:
                report.exercised_flags.setdefault(check.op, set()).update(check.args)
        # Scan every text file under the case dir (fixtures + goldens) for top-level
        # sections and format-version tokens. Best-effort: unreadable/binary files are
        # skipped rather than failing the whole report.
        for path in case.path.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix
            if suffix not in (".kicad_sch", ".kicad_pcb"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in _VERSION_TOKEN_RE.finditer(text):
                report.format_versions_seen.add(m.group(1))
            try:
                forms = sexpr.parse_all(text)
            except sexpr.ParseError:
                continue  # malformed-on-purpose failure fixtures don't parse; that's fine
            if not forms:
                continue
            root = forms[0]
            if not isinstance(root, list) or not root:
                continue
            sections = sexpr.top_level_sections(root)
            if suffix == ".kicad_sch":
                report.sch_sections_seen.update(sections & _SCH_SECTIONS_OF_INTEREST)
            elif suffix == ".kicad_pcb":
                report.pcb_sections_seen.update(sections & _PCB_SECTIONS_OF_INTEREST)
    return report
