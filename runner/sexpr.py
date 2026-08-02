"""A minimal S-expression reader for KiCad's `.kicad_*` files and `kicadsexpr` netlists.

This is intentionally not a full KiCad-format model — it is just enough to (a) find
top-level sections for the coverage proxy (DESIGN.md §7a) and (b) walk the `netlist`
export's `(nets ...)` tree for the structured reduction (DESIGN.md §3b). Quoted strings
are unquoted; everything else (parens aside) is an opaque atom string. A parenthesized
form parses to a Python ``list`` whose first element is normally the form's tag.

Stdlib only (DL-0002) — no external parser library.
"""
from __future__ import annotations

from dataclasses import dataclass


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


def top_level_sections(form: list) -> set[str]:
    """Given a parsed root form like `['kicad_pcb', ['version', '...'], ['net', ...], ...]`,
    return the set of top-level section tags (DESIGN §7a format-token coverage)."""
    sections = set()
    for item in form[1:]:
        if isinstance(item, list) and item and isinstance(item[0], str):
            sections.add(item[0])
    return sections


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
