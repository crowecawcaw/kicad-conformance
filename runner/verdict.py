"""The three-way OK / REJECT / CRASH verdict (DESIGN.md §3a, DL-0013).

A malformed input can make the oracle *crash* rather than reject cleanly (observed:
KiCad 10.0.5 `pcb upgrade` on a truncated board prints a good `Expecting '('` message
and then segfaults). A naive "non-zero = rejected" rule would silently pass an
`outcome="error"` case on a crash. So termination is classified into three outcomes, and
a CRASH is never a pass — not for `happy`, not for `failure`.

Detection is portable: never hard-code the literal 139. On POSIX, Python's
`subprocess` reports a negative `returncode` when the child was killed by a signal
(`returncode == -signum`, i.e. `WIFSIGNALED`); we also treat any `returncode > 128`
as crash-equivalent (the 128+signal convention some shells surface, and the
Windows-fatal-exception-status case), as a defensive belt-and-suspenders rule.

Adapter/child-process note: the runner's direct subprocess child is the *adapter*, not
kicad-cli (DL-0007 — the adapter contract is itself a subprocess boundary). If
kicad-cli is signaled, the adapter process must re-raise that same signal against
itself (see `runner/adapters/kicad.py`) so the signal is still visible as a negative
`returncode` on the adapter -- the runner's direct child -- rather than being silently
absorbed into a normal adapter exit code. That is what makes this classifier meaningful
through the adapter indirection layer.
"""
from __future__ import annotations

import enum


class Verdict(enum.Enum):
    OK = "OK"
    REJECT = "REJECT"
    CRASH = "CRASH"


def classify(returncode: int) -> Verdict:
    if returncode == 0:
        return Verdict.OK
    if returncode < 0:
        return Verdict.CRASH
    if returncode > 128:
        return Verdict.CRASH
    return Verdict.REJECT
