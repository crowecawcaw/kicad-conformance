# Divergence ledger

This is the checked-in **divergence ledger** [DL-0009](DECISIONS.md) refers to: the
place where a known non-conformance is triaged and tracked instead of hidden. Two
distinct things can diverge from what the suite expects, and this ledger covers both:

- **The reference oracle itself (KiCad/`kicad-cli`) can fail the suite's own, stricter
  standard.** KiCad-as-oracle means KiCad's behavior *is* the recorded correct answer --
  but that does not mean KiCad is bug-free. A crash is never conformant behavior
  ([DL-0013]), even when the crashing tool is the one defining every other answer. When
  this happens, the paired case still documents and asserts the *desired* behavior (a
  graceful rejection); it is marked with a `known_divergence` ([DL-0018]) so the suite
  stays green without either (a) quietly downgrading the assertion to match the bug, or
  (b) leaving `main` permanently red over a bug that is not this repo's to fix.
- **A second adapter (a non-KiCad implementation-under-test) can diverge from the
  KiCad-recorded answer.** That is the second-adapter use of this ledger ([DL-0009],
  [DL-0018]): each entry gets a verdict of "KiCad's answer is right, fix the tool" or
  "the suite is wrong." No entries of this kind exist yet -- the second adapter has not
  landed.

**Why "strict xfail" and not just a skip.** A `known_divergence` marker is not a
permanent excuse. It is scored strictly: today's declared-bad verdict (e.g. `CRASH`)
counts as `XFAIL` and keeps the build green, but the moment the oracle stops reproducing
it -- returns a clean `OK`/`REJECT` instead -- the check `XPASS`es and **fails the
build**, with a message pointing back here. That forces this ledger to be updated (and
the `case.toml` marker removed) instead of silently rotting into a stale note nobody
revisits. See [DL-0018] and `docs/TEST_CASE_FORMAT.md` §8 for the schema, and
`runner/engine.py`'s `_score_known_divergence` for the implementation.

---

## Entries

### DIV-0001 -- `pcb upgrade` segfaults on a truncated board instead of exiting gracefully

- **Status:** open (known oracle bug, tracked as a strict xfail)
- **Case:** [`suites/board-parse/rejects-unterminated-sexpr`](../suites/board-parse/rejects-unterminated-sexpr/case.toml)
- **Input:** `board.kicad_pcb` -- a minimal board whose `(version 20240108` s-expression is
  missing its closing paren (the file ends mid-`(generator "pcbnew")` with no terminating
  `)` for the outer `kicad_pcb` form).
- **Command (adapter mapping):** `pcb upgrade --force <scratch-copy-of-board.kicad_pcb>`
  (the `parse-pcb` verb, DESIGN.md §2).
- **Expected (desired) behavior:** a graceful, bounded non-zero exit (`REJECT`,
  DESIGN.md §3a) with an `Expecting …` parse-position message on stderr -- exactly what
  the *positive control* (`control.kicad_pcb`, the same board with the paren restored)
  proves this check can distinguish, and exactly what a well-formed PCB loader failure
  path elsewhere in the suite exhibits.
- **Observed behavior (KiCad 10.0.5, both native and `kicad/kicad:10.0.5` Docker Linux):**
  `kicad-cli` prints the *correct* message --
  `Failed to load board: Expecting '(' in '…', line 2, offset 1.` (offset/line vary
  slightly by exact truncation point) -- and then **segfaults** (`SIGSEGV`; exit 139 on
  native Windows' fatal-exception-status equivalent, `-11`/`WIFSIGNALED` on Docker Linux).
  Reproduced via the adapter directly (`adapters/kicad.py parse-pcb …`), which
  observes and re-signals the identical `SIGSEGV`, and via the runner's own classifier
  (`runner/engine.py`'s `Verdict`/`classify`), which reports `CRASH`.
- **KiCad version:** 10.0.5 (`kicad-cli version --format about`, pinned per [DL-0001]).
- **Verdict per this reproduction:** confirmed KiCad bug, not a harness/suite defect --
  the message is right, only the process termination is wrong. Filed upstream: **TODO**
  (tracking placeholder in `case.toml`'s `known_divergence.tracking`; update both this
  entry and the marker once an upstream issue number exists).
- **Resolution path:** the case's `known_divergence` marker makes this an `XFAIL` (green)
  until a KiCad release rejects the same fixture cleanly. At that point the check
  `XPASS`es, the gating build goes red with a "known divergence no longer reproduces"
  message, and the fix is: remove the `[known_divergence]` table from
  `suites/board-parse/rejects-unterminated-sexpr/case.toml`, flip this entry's
  **Status** to `resolved`, and note the fixed KiCad version here.
