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

---

### DIV-0003 -- `pcb drc`'s `unconnected_items` (ratsnest) can report a different pairing across identical runs when 3+ same-net endpoints are mutually unconnected

- **Status:** open (confirmed KiCad 10.0.5 nondeterminism; **not** pinned by a
  `known_divergence` marker and **no case is currently affected** -- see below)
- **Affected cases:** none committed today. Found while proving the `refill-zones`/
  `parity` extras ([DL-0036]/[DL-0038]): both reuse `reduce_drc`'s full
  `violations`/`unconnected_items`/`schematic_parity` shape, and a throwaway case built on
  a copy of `suites/board-parse/populated-board`'s board (reused only because it was
  convenient, not because the case concept needed it) exposed this.
- **The defect, empirically isolated:** `board-parse/populated-board`'s board has four
  mutually-unconnected same-net endpoints (a dangling track, a dangling via, and two
  pads) with no unambiguous "closest pair." Running `pcb drc --format json --units mm
  --severity-all` (**no** `--schematic-parity`, **no** `--refill-zones` -- plain `drc`)
  on the identical bytes, repeatedly, occasionally reports a *different* pairing in
  `unconnected_items`: e.g. one run pairs the dangling track with a PTH pad, another run
  pairs the same track with the dangling via instead, while every other field
  (`violations`, `schematic_parity`, and the OTHER two `unconnected_items` entries) stays
  identical. Sampled directly (bypassing the adapter/reduction entirely, raw
  `kicad-cli` JSON): flipped on roughly 1 run in 5-7 across repeated small samples. This
  is very likely hash-map/set iteration order in KiCad's ratsnest/connectivity code being
  sensitive to ASLR or allocator state across process starts, not anything
  environment/adapter-side -- reproduced with plain `kicad-cli` invocations, no Python in
  the loop.
- **Why this is not fixable by the reduction.** `runner/reduce.py`'s
  `_reduce_violation_list` already sorts violations and their items by *content* (never
  by UUID), which handles *ordering* nondeterminism. This is not an ordering problem --
  the *membership* of which two items get reported together in one `unconnected_items`
  entry differs between runs. There is no canonical sort that makes two different
  pairings compare equal; a reduction cannot repair a report that names a different fact
  each time.
- **Why no case is currently affected.** No committed case sets `extra = ["drc"]` (or the
  new `refill-zones`/`parity`) on a board shaped like this (3+-way mutually-unconnected
  same-net ambiguity). `suites/drc/unconnected-items` (and every other `suites/drc/*`
  case) was authored with a single, unambiguous unconnected pair per concept, which was
  independently confirmed clean across repeat runs while investigating this entry.
- **Why this is not a strict xfail.** Same reasoning as DIV-0002: no case asserts
  anything about this board's `unconnected_items`, so there is nothing to pin a
  `known_divergence` to. This entry exists so the next case author reaches for a fixture
  with an unambiguous DRC result, and re-runs `--determinism-check` more than once before
  trusting a green result on anything using `extra = ["drc"]`/`["refill-zones"]`/
  `["parity"]`.
- **Upstream action:** not yet filed -- needs a minimal, purpose-built reproducer (a hand
  written board with exactly 3 mutually-unconnected same-net endpoints) rather than reusing
  `populated-board`, so the report doesn't drag in unrelated fixture history. **TODO.**
- **Resolution path:** if a future case ever needs to assert `unconnected_items` on a
  board with this shape, this entry must be resolved (or the case redesigned to avoid the
  ambiguity) first -- do not paper over it with a wider tolerance in the reduction.

---

### DIV-0004 -- `pcb upgrade --force` silently deletes an inline `(net_class ...)` board block

- **Status:** open (confirmed KiCad 10.0.5 defect, tracked as an answer-scoped strict xfail, [DL-0040])
- **Case:** [`suites/board-parse/board-netclasses`](../suites/board-parse/board-netclasses/case.toml) (`extra = ["drc", "roundtrip"]`, `known_divergence.answer = "roundtrip"`)
- **Input:** `board.kicad_pcb` -- a hand-authored board with one `(net_class Tight ...)`
  block giving `NET_A` a tighter clearance (0.5mm) than the board's hardcoded default
  (0.2mm); see the case's own extensive `case.toml` commentary for why this fixture is
  hand-authored and never run through `upgrade --force` for its own committed answers.
- **Command:** `pcb upgrade --force` on a scratch copy (the round-trip check's first
  half, `adapters/kicad.py`'s `cmd_roundtrip`/`_board_semantic_view`).
- **Expected (desired) behavior:** the re-serialized board's DRC result is unchanged --
  the `clearance` violation naming netclass `Tight` still fires, exactly as it does
  before round-tripping.
- **Observed behavior (KiCad 10.0.5, Docker Linux, verified directly for this feature,
  2026-08-05):** `pcb upgrade --force` exits 0 ("Successfully saved board file using the
  latest format") and silently drops the entire `(net_class ...)` block -- `grep -c
  net_class` on the re-serialized file returns `0`. Current (20260206) kicad-cli only
  ever *writes* netclass data into a project's `.kicad_pro` file; the inline board block
  is read-only/back-compat on the parse side (`parseNETCLASS`) and is never re-emitted.
  Re-running DRC on the re-serialized board (no `.kicad_pro` alongside it, matching the
  committed fixture) drops from **3 violations to 2**, and the `clearance` finding
  disappears entirely -- `NET_A` silently reverts to the board's 0.2mm default.
- **Why this is a strict xfail, not a plain failure.** The `roundtrip` invariant exists
  precisely to catch this; scoring it a bare `FAIL` would rot the gating build over a
  confirmed, already-understood KiCad bug that isn't this repo's to fix. `case.toml`'s
  `[known_divergence]` (`kind = "writer-data-loss"`, `answer = "roundtrip"`) scores it
  `XFAIL` instead -- every other answer on the same case (`summary.json`, `drc.json`,
  `render-F_Cu.svg`, gerbers, drill) is unaffected and still scored as an ordinary
  PASS/FAIL.
- **Verdict per this reproduction:** confirmed KiCad bug (silent data loss on a
  documented, still-parseable construct), not a harness/suite defect. Filed upstream:
  **TODO**.
- **Resolution path:** when a KiCad release preserves (or explicitly, loudly rejects) an
  inline `net_class` block through `upgrade --force`, the check `XPASS`es and fails the
  build. Fix: remove the `[known_divergence]` table from `board-netclasses/case.toml`,
  flip this entry's **Status** to `resolved`, and note the fixed version here.

---

### DIV-0005 -- `pcb upgrade --force` silently deletes a thru-hole pad's `(drill 0)`

- **Status:** open (confirmed KiCad 10.0.5 defect, tracked as an answer-scoped strict xfail, [DL-0040])
- **Case:** [`suites/drc/through-hole-pad-without-hole`](../suites/drc/through-hole-pad-without-hole/case.toml) (`extra = ["drc", "roundtrip"]`, `known_divergence.answer = "roundtrip"`)
- **Input:** `board.kicad_pcb` -- a `thru_hole` pad whose `(drill 0)` documents "no
  actual hole," which today reports the single, specific `through_hole_pad_without_hole`
  DRC finding.
- **Command:** `pcb upgrade --force` on a scratch copy (`cmd_roundtrip`/
  `_board_semantic_view`).
- **Expected (desired) behavior:** the re-serialized board still reports
  `through_hole_pad_without_hole`, unchanged.
- **Observed behavior (KiCad 10.0.5, Docker Linux, verified directly for this feature,
  2026-08-05):** `pcb upgrade --force` exits 0 and the `(drill 0)` token is gone from the
  re-serialized pad entirely (verified: `grep drill` on the output shows only an
  unrelated `(drillshape 1)` token from the board's via, no pad-level `drill` at all).
  DRC on the re-serialized board reports **2** violations of **different** types --
  `drill_out_of_range` and `padstack_invalid` -- and `through_hole_pad_without_hole` is
  gone. The pad's declared type is reinterpreted on reload once its explicit
  zero-diameter hole vanishes, so this is not merely a missing finding but a
  **different, wrong pair of findings standing in for the original one** -- the same
  "does not just miss things, it fabricates" shape [DL-0032]'s audit found for a
  different bug. `summary.json`'s `counts.pads` also moves (the pad's counted type
  changes), so this defect is visible in more than one projection, unlike DIV-0004/
  DIV-0006.
- **Why this is a strict xfail, not a plain failure.** Same reasoning as DIV-0004:
  `known_divergence` (`kind = "writer-data-loss"`, `answer = "roundtrip"`) scores this
  `XFAIL`, leaving every other answer on the case scored normally.
- **Verdict per this reproduction:** confirmed KiCad bug (silent, semantics-changing data
  loss). Filed upstream: **TODO**.
- **Resolution path:** when a KiCad release preserves `(drill 0)` (or an equivalent
  explicit "no hole" marker) through `upgrade --force`, the check `XPASS`es. Fix: remove
  the `[known_divergence]` table from `through-hole-pad-without-hole/case.toml`, flip
  this entry's **Status** to `resolved`, and note the fixed version here.

---

### DIV-0006 -- `sch upgrade --force` silently deletes `(bus_alias ...)` blocks, with no other observable trace

- **Status:** open (confirmed KiCad 10.0.5 defect, tracked as an answer-scoped strict xfail, [DL-0040])
- **Case:** [`suites/schematic-parse/schematic-bus-alias`](../suites/schematic-parse/schematic-bus-alias/case.toml) (`extra = ["roundtrip"]`, `known_divergence.answer = "roundtrip"`)
- **Input:** `sheet.kicad_sch` -- two symbols each driving a labelled stub net (`A`,
  `B`) into a bus via `bus_entry`s, a bus wire labelled `MYBUS`, and a top-level
  `(bus_alias "MYBUS" (members "A" "B"))` block.
- **Command:** `sch upgrade --force` on a scratch copy (`cmd_roundtrip`/
  `_sch_semantic_view`), which also builds a `bus_alias` census directly from the
  schematic's own s-expression text (see `DESIGN.md` §3e).
- **Expected (desired) behavior:** the re-serialized schematic still contains the
  `bus_alias` block, and the census (`{"MYBUS": ["A", "B"]}`) is unchanged.
- **Observed behavior (KiCad 10.0.5, Docker Linux, verified directly for this feature,
  2026-08-05, corroborating the independent finding already on record at
  `docs/UNDOCUMENTED.md` UD-17):** `sch upgrade --force` exits 0 and the `(bus_alias
  ...)` block is gone entirely (`grep -c bus_alias` on the re-serialized file: `0`).
  Unlike DIV-0004/DIV-0005, **nothing else moves at all**: `sch export netlist` on the
  two files is identical apart from the embedded filename/timestamp/UUIDs the summary
  already drops, and `sch erc --severity-all` reports the byte-identical 19 violations
  (including the same `bus_to_net_conflict`/`net_not_bus_member` findings that show the
  alias was never honoured by ERC even *before* round-tripping) on both files -- verified
  directly, not merely cited from UD-17. This is *why* the `roundtrip` invariant's
  schematic half includes the targeted `bus_alias` census (`DESIGN.md` §3e): a
  `summary`/`erc`-based comparison alone, however reduced, has no way to ever notice this
  specific loss, because the alias has no effect on either export whether present or
  absent.
- **Why this is a strict xfail, not a plain failure.** `known_divergence` (`kind =
  "writer-data-loss"`, `answer = "roundtrip"`) scores this `XFAIL`; the case's
  `summary.json`/`render.svg` are unaffected and scored normally.
- **Verdict per this reproduction:** confirmed KiCad bug -- round-trip data loss through
  a documented, still-parseable token, with no compensating signal anywhere in
  `kicad-cli`'s output. Filed upstream: **TODO**.
- **Resolution path:** when a KiCad release preserves `bus_alias` through `sch upgrade
  --force`, the check `XPASS`es. Fix: remove the `[known_divergence]` table from
  `schematic-bus-alias/case.toml`, flip this entry's **Status** to `resolved`, and note
  the fixed version here.
