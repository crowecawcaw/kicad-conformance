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
  (the `parse-pcb-upgrade` verb -- **not** the default `parse-pcb` verb any more since
  [DL-0029] remapped `parse-pcb` to `pcb export stats`, which rejects this exact fixture
  gracefully; this case pins itself back to the old, crashing command via
  `known_divergence.probe = "parse-pcb-upgrade"` in `case.toml`, specifically so this
  entry keeps being exercised instead of going untested. DESIGN.md §2/§2's notes.)
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

---

### DIV-0002 -- `pcb export ipcd356` reads an uninitialised `int` on every via record

- **Status:** open (confirmed KiCad 10.0.5 defect; **not** pinned by a `known_divergence`
  marker -- see "Why this is not a strict xfail" below)
- **Affected cases:** every board fixture containing a via. As of this entry, seven:
  `suites/board-parse/{populated-board, blind-and-buried-vias, micro-via,
  four-layer-stackup, via-remove-unused-layers, zone-keepout-rule-area, board-netclasses}`.
  All seven record a `summary.json`, and `summary.json` is composed from `pcb export
  ipcd356` among others (`runner/summary.py:build_board_summary`), so all seven pass their
  board's via records through the defective code path on every run.
- **The defect, in KiCad's source** (read from the pinned build,
  `18fb9289ff0efdca53c0352ed81a0973f0a6b58c`):
  `pcbnew/exporters/export_d356.cpp` declares a plain `D356_RECORD rk;` in both the pad
  loop (line 113) and the via loop (line 200). The struct (`export_d356.h`) has **no member
  initialisers** -- `int soldermask;` among them. The **pad** path initialises the field
  before masking:

  ```cpp
  rk.soldermask = 3;                                        // line 151
  if( pad->GetLayerSet()[F_Mask] ) rk.soldermask &= ~1;     // line 154
  ```

  The **via** path never does, and goes straight to a read-modify-write:

  ```cpp
  if( via->IsTented( F_Mask ) ) rk.soldermask |= 1;         // line 231
  if( via->IsTented( B_Mask ) ) rk.soldermask |= 2;         // line 233
  ```

  and the value is printed verbatim at line 359 (`fprintf( aFile, "S%d\n", rk.soldermask )`).
  A via that is tented on neither side never has the field written at all.
- **Observed, release oracle** (`kicad/kicad:10.0.5`,
  `kicad-cli pcb export ipcd356 -o b.d356 board.kicad_pcb`): every via record ends `S3`.
  Checked on four of the affected fixtures, including three that contain **no pads at
  all**, which rules out the obvious "the value leaks from the previous loop's `rk`"
  explanation -- the stack slot simply happens to hold `3` in this binary:

  ```
  micro-via              317NET-1  VIA  MD0157PA00X+001969Y-003937X0236Y0000R000S3
  blind-and-buried-vias  307NET-1  VIA  MD0118PA01X+003150Y-003937X0236Y0000R000S3
  four-layer-stackup     317NET-1  VIA  MD0157PA00X+003937Y-003937X0315Y0000R000S3
  populated-board        317NET-1  VIA  MD0157PA00X+011811Y-007874X0236Y0000R000S3
  ```
- **Observed, instrumented oracle** (the gcov build, which compiles with
  `-ftrivial-auto-var-init=pattern`): the same boards emit `S-16843009` --
  `0xFEFEFEFF`, the pattern fill byte. This is what surfaced the defect
  ([`COVERAGE.md`](COVERAGE.md) §2b), and it is the proof that `3` is not computed.
- **Verdict: `S3` in KiCad 10.0.5 is luck, not specification.** It is a stable read of an
  uninitialised stack slot in this particular binary. It is not reproducible across builds,
  and a maintainer must not treat it as the correct IPC-D-356 soldermask code for a via.
  Note also that `3` means "not accessible on either side" (the comment above line 151):
  for a fully tented via that is coincidentally the right answer, and for an untented via
  it is the wrong one -- KiCad's own via path can only ever *set* bits, never clear them,
  so there is no initial value from which it computes a correct result for an untented via.
- **Why our answers are not currently wrong.** `runner/reduce.py`'s `reduce_ipcd356`
  **drops the trailing `S<n>` entirely** -- the reduction keys on net + refdes/pad
  membership and test-point geometry, and never compares the serial. So no committed
  `expected/**` file records an `S` value, and no recorded answer is invalid today.
- **Why our answers are nevertheless exposed.** `_IPCD356_RECORD_RE`
  (`runner/reduce.py:396`) requires `R(?P<rot>\d+)S(?P<serial>\d+)` -- a **non-negative**
  serial. On any build where the uninitialised read yields a negative number, the reducer
  raises `ValueError: unrecognized IPC-D-356 record: ...` and the case fails with
  `summary: adapter did not exit OK (returncode=1)`. That is exactly the six failures
  COVERAGE.md §2b reports against the instrumented build. **The suite's board summaries
  therefore depend on a KiCad uninitialised read happening to produce a non-negative
  integer.**
- **Why this is not a strict xfail.** [DL-0018]'s `known_divergence` marker pins a case
  whose *asserted behaviour* the oracle fails to deliver. No case here asserts anything
  about `S<n>`, so there is nothing to mark -- marking one would invent an assertion in
  order to have something to excuse. This entry exists so the fact is on the record, and
  so that anyone who later writes a case asserting IPC-D-356 soldermask codes (or widens
  `reduce_ipcd356` to compare the serial) finds out *before* recording an answer that
  cannot be reproduced on another build.
- **Upstream action:** file against KiCad -- initialise `D356_RECORD::soldermask` (or give
  the struct member initialisers). Filed: **TODO**.
- **Resolution path:** when a KiCad release initialises the field, re-record nothing (no
  answer changes) and flip this entry's **Status** to `resolved`, noting the version. If
  instead we decide the suite should assert the value, that is a new case *plus* widening
  `reduce_ipcd356` -- and it must not be done while this entry is open.
