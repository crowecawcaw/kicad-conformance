#!/usr/bin/env python3
"""Parse a `--verify-assertions` text log into JSON that
Tier 2 can join against. This is a **parser of stdout**, not a runner hook -- Tier 2 is
not allowed to touch `runner/` (owned by another concurrent agent), so it reads the same
text a human reviewer reads rather than adding a machine-readable mode to the CLI.

Input: the log produced by
  docker run ... kicad/kicad:10.0.5 python3 -m runner --verify-assertions suites/ > LOG

Output JSON shape:
{
  "cases": {
    "<case_dir posix>": {
      "concept": "...",
      "perturbations": [
        {"slug": "...", "status": "ASSERTED"|"INERT"|"INVALID-PERTURBATION"|"CRASH",
         "moved": ["summary", ...], "label": "semantic"|"byte-only"|null}
      ]
    }
  },
  "unasserted_cases": ["<case_dir>", ...]   # from the companion asserted-cases.txt
}

Usage:
  python3 tier1parse.py tier1-verify-full.log [asserted-cases.txt] > tier1-results.json
"""
from __future__ import annotations

import json
import re
import sys

CASE_HEADER_RE = re.compile(r"^suites/\S+$")
CONCEPT_RE = re.compile(r"^  concept: (.*)$")
# "  [ASSERTED]  slug  moved: a, b  [semantic]"   or   "  [INERT]  slug"
STATUS_RE = re.compile(
    r"^  \[(ASSERTED|INERT|INVALID-PERTURBATION|CRASH)\]\s+(\S+)"
    r"(?:\s+moved:\s+(.*?))?(?:\s+\[(semantic|byte-only)\])?\s*$"
)


def parse_log(text: str) -> dict:
    cases: dict = {}
    cur_case = None
    for line in text.splitlines():
        if CASE_HEADER_RE.match(line):
            cur_case = line.strip()
            cases[cur_case] = {"concept": "", "perturbations": []}
            continue
        m = CONCEPT_RE.match(line)
        if m and cur_case:
            cases[cur_case]["concept"] = m.group(1)
            continue
        m = STATUS_RE.match(line)
        if m and cur_case:
            status, slug, moved, label = m.groups()
            moved_list = [x.strip() for x in moved.split(",")] if moved else []
            cases[cur_case]["perturbations"].append({
                "slug": slug,
                "status": status,
                "moved": moved_list,
                "label": label,
            })
            continue
    return cases


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    log_path = argv[0]
    with open(log_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    cases = parse_log(text)

    unasserted: list[str] = []
    if len(argv) > 1:
        with open(argv[1], encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        # first line is the "N of M ..." summary; the rest are case paths.
        unasserted = [l for l in lines[1:] if l.startswith("suites/")]

    n_pert = sum(len(c["perturbations"]) for c in cases.values())
    n_asserted = sum(
        1 for c in cases.values() for p in c["perturbations"] if p["status"] == "ASSERTED"
    )
    print(
        f"parsed {len(cases)} cases with perturbations, {n_pert} perturbation outcomes, "
        f"{n_asserted} ASSERTED, {len(unasserted)} unasserted cases",
        file=sys.stderr,
    )
    json.dump({"cases": cases, "unasserted_cases": unasserted}, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
