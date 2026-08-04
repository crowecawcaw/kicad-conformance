# Asserted coverage — the measurement discipline, and the mechanism that enforces it

> **"Aim to cover all meaningful lines and branches. Coverage is what we can assert and
> verify, not just run."** — the owner, 2026-08-03.

This document turns that sentence into something a machine checks. It is a **design
spec**, not a description of shipped code: nothing here is implemented yet.
[DL-0030](DECISIONS.md) and [DL-0031](DECISIONS.md) are the decisions; §7 is the
implementation brief.

**Status:** design accepted, unimplemented. Related: [`COVERAGE.md`](COVERAGE.md) (the
executed-line measurement this refines), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §11
(the manual version of this check, which this replaces), [DL-0006] (coverage is scheduled
from-source infra), [DL-0013] (the positive control — the same principle, applied to
rejection cases).

---

## 1. The problem, stated exactly

[`COVERAGE.md`](COVERAGE.md) §6.1 concedes the gap in one line:

> **Covered ≠ tested.** gcov records that a line *executed*, not that the suite *asserted*
> anything about its effect.

`netlist_exporter_xml.cpp` at 70.3% means the XML exporter ran. It does not mean one
field of its output is checked. Every percentage in COVERAGE.md §3 is an upper bound on
something we never measured.

There is a second, sharper failure this fixes. Today a case's falsifiability is
established **once, by hand**, by whoever wrote it —
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §11: *"Broke the input and watched it go
red."* That act leaves **no artifact**. Nothing records that it happened, nothing
re-checks it, and nothing notices when it stops being true. A case can rot into one that
passes no matter what KiCad does — because a normalizer got broader, because a comparator
lost a field, because the recorded answer was regenerated from a run that was already
wrong — and the suite will stay green through all of it. A green suite of inert cases is
the exact failure mode this project cares most about (COVERAGE.md §7's framing: a
plausible-looking non-answer).

This is not hypothetical. While this document was being written, a DRC case was found in
the tree whose committed `drc.json` contained **none of the findings its `concept`
described** — the board's pads carried net names that resolved to nothing, so the custom
rule the case existed to exercise never fired, and the recorded answer was two unrelated
`lib_footprint_issues` warnings. The case passed. It would have passed against any KiCad
that loads the board at all. (The case has since been changed by concurrent work, which is
itself the point: nothing recorded that it was inert, so nothing could report it.)

**The fix is to make that manual step a committed artifact and re-run it.**

---

## 2. The definition

### 2.1 Three states, not two

Every line (and branch) of KiCad is in exactly one state with respect to this suite:

| State | Meaning | Evidence |
|---|---|---|
| **unexecuted** | no case makes KiCad run it | gcov count `0` |
| **executed-only** | some case runs it, but no recorded answer demonstrably depends on it | gcov count `>0`, not in the asserted set |
| **asserted** | some case runs it **and** a change in what it computes shows up in a recorded answer | gcov count `>0` **and** credited by §4's attribution |

Today COVERAGE.md reports the first boundary only. The second boundary is the one the
owner's sentence is about.

### 2.2 The operational test

> A behaviour is **asserted** if a change in KiCad's behaviour there produces a change in
> some recorded answer.

That is the ideal. It is not directly executable — testing it literally means mutating
KiCad's source at that line, rebuilding (~25 min per mutant, COVERAGE.md §1) and re-running
the suite. For a 500k-line tree that is a rejected cost (§3.5).

What *is* executable is the **input-side proxy**:

> **A perturbation `P` of case `C`'s input is asserted iff running `C` with `P`
> substituted for its input, against `C`'s own recorded answers, **fails**.**

That is the whole definition, and it is deliberately expressed in the machinery that
already exists: no new comparator, no new answer format, no new verdict semantics. "The
case goes red" is exactly the check `python -m runner` already performs. The perturbation
mechanism is the contributor checklist's *"break the input and watch it go red"* — moved
out of a human's memory and into the repo.

Two corollaries fall straight out:

- **A rejection case already has this.** Its `control` ([DL-0013]) is a perturbation:
  a defect-free variant that must produce a *different verdict* (`OK` where the case's
  input gives `REJECT`). The runner already refuses to pass a rejection case whose
  control does not flip. So rejection cases need no `perturb/` — they were always
  falsifiability-checked at run time, and happy cases never were. **This mechanism
  extends [DL-0013] from rejection cases to every case.**
- **The unit of assertion is a case + a perturbation**, not a case. A case with no
  perturbation asserts nothing that this mechanism can see, however good it is.

### 2.3 What this proves, and what it does not

**Proves.** For each `(case, perturbation)` pair that passes: the recorded answers are
*load-bearing* for at least the difference that perturbation introduces. The case cannot
be silently inert. If a normalizer, comparator or regeneration later erases the case's
sensitivity to that difference, the pair goes red and names the case.

**Does not prove.**

1. **Not "KiCad is correct there."** KiCad is the oracle ([DL-0004]); a recorded answer is
   what KiCad did, not what is right. Asserted coverage measures whether we would *notice*
   a change, not whether the current value is correct. The one exception is a case that
   also carries an independent cross-check (`summary-kicadxml`, [DL-0027]).
2. **Not per-line, rigorously.** §4 credits lines whose *execution profile* changed
   between the base and perturbed runs. A line can be credited coincidentally — control
   flow shifted upstream and it ran a different number of times — without its own computed
   value being observed. Credit is therefore an **upper bound** on assertion, exactly as
   executed-lines is an upper bound on tested. It is a strictly tighter bound than what we
   have now, and the report publishes the specificity of each perturbation (§4.4) so a
   weak credit is visible rather than hidden.
3. **Not equivalent to source mutation.** The proxy answers "does the answer depend on
   what this code does with *this* input?" not "would any change to this code be caught?"
   A line that is exercised only in its default/no-op configuration can be credited while
   an edit to it would go unnoticed.

Calling this out plainly is the point: the number this produces is smaller and more honest
than "% covered", and it is still an upper bound. §6 lists the rest of the limits.

---

## 3. The mechanism

### 3.1 On disk

A perturbation is **a copy of the case's input with something changed**, in a
`perturb/<slug>/` directory inside the case:

```
suites/board-parse/populated-board/
├── case.toml
├── board.kicad_pcb                       # the input
├── expected/
│   └── 10.0.5/                           # the answers
│       ├── summary.json
│       ├── render-F_Cu.svg
│       ├── gerbers/
│       └── drill/
└── perturb/
    ├── pad-to-other-net/
    │   └── board.kicad_pcb               # same board, C1 pad 2 moved to GND
    └── via-moved-1mm/
        └── board.kicad_pcb               # same board, the via shifted +1mm in X
```

**`case.toml` does not change. There is no new manifest key.** The common case stays:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

Five rules, all enforced by the runner:

1. **`perturb/<slug>/` is an overlay.** Any file in it whose name matches one of the case's
   declared inputs (`input`, or a member of `inputs`) replaces that input for this
   perturbation's run. Every other input is used unchanged — so a multi-sheet schematic
   case perturbs one sheet without copying the rest.
2. **A file whose name matches no declared input is an error** (`INVALID-PERTURBATION`).
   That is the typo guard; without it a misnamed file would silently make a perturbation
   that perturbs nothing.
3. **A perturbation must still load.** For a happy case, the perturbed input must be
   *accepted* by the oracle. A perturbation that simply breaks the file trivially "changes
   the answer" and asserts nothing — it is a rejection case wearing a disguise, and the
   runner reports `INVALID-PERTURBATION`, not `ASSERTED`.
4. **`<slug>` is the documentation.** It is a hyphenated phrase saying what was changed,
   in the same voice as a case slug (`pad-to-other-net`, `via-moved-1mm`,
   `layer-count-4-to-2`). There is no description field: the slug plus
   `diff board.kicad_pcb perturb/<slug>/board.kicad_pcb` — which the runner prints on
   failure — is a complete statement of the perturbation.
5. **Rejection cases must not have a `perturb/` directory.** They record no answers, so
   "the answer changed" is undefined; their `control` already plays this role (§2.2).
   The runner rejects the combination rather than silently ignoring it.

Perturbation inputs are fixtures and follow every fixture rule: hand-authored and minimal
([DL-0011]), LF via `.gitattributes` ([DL-0016]), and named exactly as the input they
replace ([DL-0026] — gerber output embeds the input's filename, so a perturbation named
anything else would "change the answer" for a reason that has nothing to do with the
board).

### 3.2 The runner mode

```bash
scripts/run.sh --verify-assertions                 # whole suite
scripts/run.sh --verify-assertions suites/drc/     # scoped, same as a normal run
```

`--verify-assertions` is an **alternate mode**, structurally identical to the existing
`--determinism-check` (`runner/cli.py:run_determinism_mode`): it replaces the normal run
rather than adding to it, takes the same `PATHS`, and is not something the default
invocation does.

Per case, per perturbation, the runner:

1. builds the input set (case inputs, overlaid per §3.1 rule 1);
2. runs the case's answer generation against that input set, into scratch;
3. compares the result to the case's **committed** `expected/<version>/` answers using the
   unmodified comparators;
4. **requires at least one comparison to differ.**

Statuses, printed per perturbation:

| Status | Meaning | Build |
|---|---|---|
| `ASSERTED` | ≥1 recorded answer differs — the case is falsifiable by this perturbation | green |
| `INERT` | the perturbed input produces byte-identical answers | **red** |
| `INVALID-PERTURBATION` | perturbed input not accepted by the oracle, or overlays a filename that is not an input, or a `perturb/` on a rejection case | **red** |
| `CRASH` | oracle terminated by signal / exit >128 on the perturbed input | **red** ([DL-0013]) |

And per case:

| Status | Meaning | Build |
|---|---|---|
| `UNASSERTED-CASE` | a happy case with no `perturb/` directory | green, **counted** (§3.4) |

`ASSERTED` prints **which** answers moved, because not all movements are worth the same:

```
suites/board-parse/populated-board
  concept: A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via.
  [ASSERTED]       pad-to-other-net       moved: summary.json, gerbers/ (2 files)   [semantic]
  [ASSERTED]       via-moved-1mm          moved: gerbers/ (3 files), drill/         [byte-only]
  [INERT]          silk-text-recased      moved: nothing
      perturbed input differs from the case input by 1 line:
      -   (property "Reference" "R1" ...
      +   (property "Reference" "r1" ...
      but every recorded answer is identical. Either the case does not assert this
      behaviour, or the perturbation is semantically a no-op. Adjudicate; do not delete.
```

**`[semantic]` vs `[byte-only]`** is the ecosystem-mode distinction from
[DL-0015]/[DL-0026]: `gerbers/` and `drill/` are byte answers that report `INFO`, never
`FAIL`, against a non-KiCad adapter. A perturbation that moves *only* those asserts
nothing outside KiCad-regression mode. The runner labels it; it does not fail it (a
gerber-only assertion is still a real KiCad-regression assertion). The gap report (§4)
counts the two separately.

**Short-circuit ordering.** Answers are generated cheapest-and-likeliest-first and
generation stops at the first difference (§5.2): the case's `extra` answers (they exist
because the case is *about* them), then `summary.json`, then the render, then `gerbers/`
and `drill/`. `INERT` is the only outcome that pays for the full set.

### 3.3 What is deliberately not in the syntax

- **No "which answer must move" field.** It would go stale on the first regeneration and
  is a knob per [DL-0025]'s standard. The runner reports what moved; the reviewer reads it.
- **No expected-inert perturbations.** "This change must *not* alter the answer" is a real
  and useful assertion (canonicalization, whitespace, token order) — and it is a **second
  case with the same expected answers**, not a perturbation. Adding an inert flag would put
  two opposite meanings behind one directory name.
- **No recorded answers for the perturbed run.** We assert only *that* the answer moved,
  never *to what*. Recording the perturbed answer doubles the repo and is, precisely, a
  second case. This is the single biggest cost control in the design (§5).
- **No description/rationale field.** The slug and the diff.

### 3.4 Adoption without turning the tree red

The suite has 77 cases and none has a `perturb/` directory. Requiring one immediately
would fail every case on day one.

- `UNASSERTED-CASE` is **counted and printed, not failed**. `--verify-assertions` prints
  `N of M happy cases carry no perturbation` and writes that count to
  `tools/coverage/out/asserted-cases.txt`.
- CI gates on the **ratchet**: the count of `UNASSERTED-CASE` may not increase. New cases
  ship with a perturbation; old ones are backfilled as they are touched.
- `INERT` and `INVALID-PERTURBATION` are **hard failures from day one** — they can only
  appear if someone wrote a perturbation, and a wrong perturbation is worse than none.

### 3.5 Alternatives considered

| Alternative | Why not |
|---|---|
| **Mutate the *answer* instead of the input** — corrupt a field of `expected/…/summary.json` and require the comparator to report a difference. | It tests the **comparator**, not the case. It would pass for every case in the suite regardless of whether any KiCad behaviour is observed, because the property being tested ("the diff engine notices a changed byte") is global. Worth having **once**, as a runner self-test over the four comparison kinds — not per case, and not as the answer to the owner's question. |
| **Coverage-diff per case only** — run every case in isolation under gcov and attribute executed lines to cases. | Gives attribution but not assertion: it re-slices the same "this ran" data we already have. It is not rejected so much as **subsumed** — it is exactly the attribution half of §4, and it is useless for this purpose without the answer-moved half. |
| **Source-level mutation testing of KiCad** (mutate `pcb_io_kicad_sexpr_parser.cpp`, rebuild, re-run). | The gold standard, and the thing our proxy approximates. Rejected on cost: ~25 min per mutant rebuild (COVERAGE.md §1) against a 500k-line tree. Even 50 hand-picked mutants is a 20+ hour run for a signal that must be regenerated on every KiCad bump. Kept on the shelf as a *targeted* tool: if one subsystem's asserted number is ever disputed, a dozen hand-placed mutants in that file settle it. |
| **Metamorphic / directional assertions** — "translating the board +1 mm translates every gerber coordinate by +1 mm." | Strictly stronger than "something moved", and a natural later upgrade layered on the same `perturb/` files (a perturbation could grow an optional relation). Deliberately not now: it needs a relation language, which is the largest possible knob, and the cheap version already catches the failure we actually have (inert cases). |
| **Require a perturbation per *answer*, not per case.** | Turns a one-sentence case into a matrix. The gap report (§4) already shows which answers are doing no work, at suite scale, without pushing that bookkeeping into every case directory. |

---

## 4. The gap report — "executed but nothing asserts it"

This is the artifact that answers the owner's question. It is **Tier 2**: it needs the
gcov-instrumented image and joins the existing scheduled coverage job ([DL-0006]), not the
per-push gate.

### 4.1 Per-case counter isolation (the one new piece of infrastructure)

Today `tools/coverage/run-suite.sh` accumulates counters for the *whole* suite into one
pool (1850 `.gcda` files, COVERAGE.md §2). Attribution needs them per run. libgcov
supports this directly:

```bash
GCOV_PREFIX=/coverage/raw/<case>/<run>   GCOV_PREFIX_STRIP=<depth-of-/src/build>
```

set per `kicad-cli` invocation. Every invocation of one run writes into the same bucket;
buckets are collected with `gcov -i` (JSON intermediate) into one per-line count map per
run. This is the only genuinely new tooling; everything else composes existing scripts.

### 4.2 The algorithm

```
for each case C:
    base[C]        = per-line execution counts of C's normal run          (instrumented)
    for each perturbation P of C:
        pert[C,P]  = per-line execution counts of C's run with P
        moved[C,P] = the Tier-1 result: did any recorded answer differ?   (§3.2)
        kind[C,P]  = "semantic" | "byte-only" | "render-only"             (§3.2)

executed = { L : sum over C of base[C][L] > 0 }

credited[C,P] = { L : base[C][L] > 0  and  base[C][L] != pert[C,P][L] }   if moved[C,P]
              = {}                                                        otherwise

asserted          = union of credited[C,P] over all C,P
asserted_semantic = union of credited[C,P] where kind[C,P] == "semantic"

GAP = executed \ asserted
```

Three properties of this definition worth stating, because each is a decision:

- **`base[C][L] > 0` is required.** A line that runs *only* under the perturbation is not
  part of what the recorded answers cover, so it is not credited. `asserted ⊆ executed`
  holds by construction, and the report can never claim more assertion than coverage.
- **Count inequality, not boolean coverage.** A line that runs 4 times in the base and 7
  times perturbed *did behave differently*. Requiring a 0↔n transition would throw away
  most of the signal in loop-heavy parser and plotter code.
- **`asserted_semantic` is tracked separately** because a line asserted only by a
  `gerbers/` byte answer is not asserted for any implementation but KiCad
  ([DL-0015]/[DL-0026]). The suite's cross-implementation claim rests on the semantic
  number.

Branches use the identical rule against gcov's branch counters (`taken` per branch id),
with the caveat in §6.7.

### 4.3 The artifacts

Written alongside the existing report in `tools/coverage/out/report/`:

| File | Contents |
|---|---|
| `asserted.json` | per file: `line_total`, `line_covered`, `line_asserted`, `line_asserted_semantic`, plus the same four for branches |
| `asserted-gap.md` | **the report the owner reads** (§4.4) |
| `asserted-credit.json` | per `(case, perturbation)`: the credited line count, the answers that moved, and the credit fan-out (§4.4) |

`asserted-gap.md` mirrors COVERAGE.md's structure so the two read together:

1. **Headline table** — COVERAGE.md §3's subsystem rollup with two columns added:

   | bucket | lines | covered | **asserted** | **asserted (semantic)** |
   |---|---:|---:|---:|---:|
   | `io/board` | 41261 | 2198 | *n* | *n* |
   | … | | | | |

   The gap between column 3 and column 4 is the entire point of the document.

2. **The gap list** — files and functions with `covered > 0` and `asserted == 0`, sorted
   by covered-line count descending. This is a *fully mechanical* sibling of COVERAGE.md
   §5's hand-written gap list, and it names a different kind of gap: §5 says "write a case
   that reaches this code", this says **"a case already reaches this code and nothing
   would notice if it changed."** The second is cheaper to fix — usually one perturbation
   in a case that already exists.

3. **Answers doing no work** — for each answer kind (`summary.json`, `render-*.svg`,
   `gerbers/`, `drill/`, each `extra`), how many perturbations moved it. An answer that no
   perturbation in the whole corpus ever moves is a candidate for deletion, and that is a
   finding this project should want.

4. **Cases with no perturbation** — the `UNASSERTED-CASE` list from §3.4, so the two
   halves are in one place.

### 4.4 Credit specificity — keeping the number honest

A perturbation that shifts a board 1 mm will change execution counts across the whole
plot pipeline and credit thousands of lines on the strength of one changed answer. That
is over-crediting, and an unlabelled asserted-% would quietly inflate.

`asserted-credit.json` therefore records **fan-out** — `|credited[C,P]|` — per
perturbation, and `asserted-gap.md` prints the distribution (median, p90, max) plus the
top ten perturbations by fan-out. **A perturbation with a fan-out above the corpus p90 is
flagged `low-specificity`.** No automatic penalty: the flag exists so the reviewer knows
which credits are load-bearing and which are spray. Small, surgical perturbations (one
pad's net; one token's value) are what the flag pushes authors toward, and they are also
the better documentation.

---

## 5. Cost, and when it runs

### 5.1 Measured baseline

On this workstation, against `kicad/kicad:10.0.5` via `scripts/run.sh` (Docker Desktop /
Windows; Linux CI is faster):

| what | measured |
|---|---|
| 1 board case | **14.3 s** |
| 3 board cases | **17.0 s** |
| 12 board cases | **1 min 26 s** |
| ⇒ fixed overhead (container start + adapter identity) | **~12 s** |
| ⇒ marginal cost, board case | **~6.2 s** |
| whole 77-case suite, gcov-instrumented, on the CI-shaped Linux run | **4 min 46 s** (COVERAGE.md §2) |

A board case is the expensive kind: six `kicad-cli` invocations (`stats`, `pos`, `ipcd356`
→ `summary.json`; `svg`; `gerbers`; `drill`).

### 5.2 What a perturbation costs

A perturbation re-runs answer generation for one case: **≤ 1 case-cost**, and less in
practice because generation short-circuits at the first differing answer (§3.2). For a
board case, `summary.json`'s three exports are ~half the per-case cost, so a perturbation
that moves the summary — the overwhelmingly common shape — costs **~3 s**, about half a
board case. `INERT` is the expensive outcome, and it is a failure, so it is rare and
short-lived.

Projected, one perturbation per case, using the marginal figures above:

| corpus | normal run | `--verify-assertions` adds |
|---|---|---|
| 77 cases (today) | ~5 min | **~2–3 min** |
| 150 cases | ~10 min | **~4–6 min** |

For scale: the gating CI job **already** runs the suite twice — once normally, once as
`--determinism-check`. This adds roughly half a pass, not a doubling.

Tier 2 (§4) is a different order of cost entirely: it needs the instrumented image
(~25 min to build, cached) and runs every case *and every perturbation* under it, with
per-run counter dumps. Estimated 2–3× the 4 min 46 s instrumented suite run, i.e. **~15
min**, on top of the existing coverage job.

### 5.3 The recommendation

**Tier 1 (`--verify-assertions`): gating in CI, on every push and PR that touches
`suites/**`, `runner/**` or `adapters/**` — the same paths filter `ci.yml` already uses.
Not in the default `python -m runner suites/`.**

Why not always-on in the default run:

- The default run is the **inner loop** and the **ecosystem-mode entry point**
  (`--adapter ./my-adapter.sh`). A second implementation should be able to run the corpus
  without also paying for the suite's self-checks; its authors are not the audience for
  "is this case falsifiable."
- The repo already has exactly this shape and it works: `--determinism-check` is an
  alternate mode, CI-only, and nobody runs it by hand.

Why gating in CI rather than scheduled:

- The failure being prevented — *a case rots into one that cannot fail* — is **introduced
  by a commit that touches `suites/`**. Catching it at that commit names the author and
  the change. Catching it a week later on a schedule hands a stranger a bisect.
- It is fast enough (§5.2) to gate. A scheduled job is what you choose when the check is
  too slow to gate; this one is not.

Why not per-PR for Tier 2: the gap report needs the instrumented from-source build, which
[DL-0006] already decided is scheduled infrastructure and not a per-PR check. That decision
applies unchanged.

**Tier 2 (the gap report): scheduled, alongside the existing coverage job**, and
regenerated on any KiCad version bump. Its output is a document, not a gate — the same
status COVERAGE.md has today.

---

## 6. What this still cannot catch

Stated plainly, because a measurement whose limits are undocumented gets over-quoted —
the mistake COVERAGE.md §6.2 had to head off with "the global 8.6% is not a quality score."

1. **A wrong recorded answer.** If KiCad is wrong and we recorded it, every perturbation
   still moves it and everything reports `ASSERTED`. Asserted coverage measures
   *sensitivity*, never *correctness*. Correctness against KiCad is definitional
   ([DL-0004]); correctness of KiCad is [`DIVERGENCES.md`](DIVERGENCES.md)'s job.
2. **Behaviour that never reaches an output.** `kicad-cli` is our only window. A KiCad
   behaviour with no CLI-observable consequence cannot be asserted by any case, and its
   lines will sit in the gap list permanently. That is not a suite defect; the report must
   not be read as a to-do list without triage against COVERAGE.md §4a's
   legitimately-unreachable classification.
3. **Everything a normalizer strips.** Timestamps, `%TF.CreationDate`, the drill header
   date ([DL-0026]'s five normalizers) are unassertable *by design*. Their lines will show
   as executed-only forever, correctly.
4. **The strength of an assertion.** A perturbation that moves 200 fields of
   `summary.json` and one that moves a single digit both score `ASSERTED`. The mechanism
   is binary; only fan-out (§4.4) hints at quality.
5. **Coincidental credit.** §2.3.2 — a credited line may have run a different number of
   times for a reason unrelated to the answer that moved. Credit is an upper bound.
6. **The `INERT` ambiguity.** `INERT` means *either* "the case does not assert this"
   *or* "the perturbation was semantically a no-op." The runner cannot distinguish them.
   A human must adjudicate, and the tempting resolution — delete the perturbation — is the
   wrong one when the first reading is true.
7. **Branch assertion is weaker than line assertion.** COVERAGE.md §6.5: gcov branch data
   is polluted by exception edges (global branch coverage reads 5.8%). Branch numbers in
   `asserted.json` are indicative, and a branch-asserted percentage should not be quoted
   without that caveat.
8. **Nondeterminism would forge credit.** An answer that moved because KiCad is
   nondeterministic, not because the input changed, scores `ASSERTED` falsely. The
   existing `--determinism-check` is the control for this, and is the reason it must keep
   running.
9. **It measures; it does not author.** "Cover all meaningful lines" needs cases —
   COVERAGE.md §5's group list is still the work. This mechanism only stops the count from
   lying about what the cases we have are worth.
10. **The oracle build differs.** Tier 2 runs on the instrumented Debug image, which
    COVERAGE.md §2/§6.4 shows changes observable behaviour (missing parse messages;
    `-ftrivial-auto-var-init` exposing an uninitialised read). Tier 1's `ASSERTED`/`INERT`
    verdicts come from the **release** image and are authoritative; Tier 2's attribution is
    a mapping onto a different binary and inherits that caveat.

---

## 7. Implementation brief

Everything below is buildable from this document without further design.

**Tier 1 — `--verify-assertions` (do this first; it stands alone).**

1. `runner/manifest.py` — discover `perturb/<slug>/` for a case; validate rules §3.1.1–5;
   expose `case.perturbations` as `[(slug, {input_name: path})]`. No new `case.toml` keys.
2. `runner/engine.py` — factor the existing happy-case path so answer generation +
   comparison can be driven with a substituted input set and can **stop at the first
   differing answer**, in the order of §3.2. Reuse the comparators unchanged.
3. `runner/assertions.py` (new, sibling of `determinism.py`) — the per-case loop, the four
   perturbation statuses and the `UNASSERTED-CASE` count; renders the diff excerpt shown
   in §3.2 on `INERT`.
4. `runner/cli.py` — `--verify-assertions`, mutually exclusive with `--determinism-check`
   and `--regenerate`; a third `run_*_mode` function alongside the two that exist.
5. `.github/workflows/ci.yml` — one step in the existing gating job, after the determinism
   step; plus the ratchet check on the `UNASSERTED-CASE` count.
6. `docs/TEST_CASE_FORMAT.md` — a `perturb/` section and a checklist item replacing §11's
   "broke the input and watched it go red" with "committed the perturbation that proves
   it"; `README.md` "Contributing a case" step 6 likewise.

**Acceptance for Tier 1.** On `suites/board-parse/populated-board`, hand-author
`perturb/pad-to-other-net/` (move `C1` pad 2 to `GND`) and confirm `ASSERTED` naming
`summary.json`; hand-author a perturbation that only recases a silkscreen property and
confirm `INERT` and a red build; hand-author one that deletes a closing paren and confirm
`INVALID-PERTURBATION`, not `ASSERTED`.

**Tier 2 — the gap report.**

7. `tools/coverage/run-suite.sh` — per-run `GCOV_PREFIX` bucketing (§4.1) and a
   `--per-case` mode; keep the existing pooled mode, which COVERAGE.md's numbers depend on.
8. `tools/coverage/asserted.py` (new) — §4.2 over the per-run `gcov -i` JSON plus the
   Tier-1 result set; emits `asserted.json`, `asserted-credit.json`, `asserted-gap.md`.
9. `docs/COVERAGE.md` §3 — add the two asserted columns once real numbers exist. Do not
   add the columns before then; an empty column reads as zero.

**Acceptance for Tier 2.** `asserted ⊆ executed` holds for every file (assert it in the
tool, do not merely believe it); a file with a perturbation known to move `summary.json`
shows non-zero `line_asserted`; `pcbnew/exporters/export_d356.cpp` — reached by every via
case — is a useful smoke target because its via path is known-defective
([DIV-0002](DIVERGENCES.md)) and its behaviour is *not* recorded in any answer, so it
should appear in the gap list.
