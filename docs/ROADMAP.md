# Roadmap

Milestones for building out kicad-conformance. Realistic, incremental, oracle-first.
Context: [`DESIGN.md`](DESIGN.md), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md),
[`DECISIONS.md`](DECISIONS.md). Primary oracle throughout: **KiCad 10.0.5** ([DL-0001]).

Each milestone is "done" only when its cases are **green against real `kicad-cli` 10.0.5
in the Docker CI job** — a case that hasn't run against KiCad is not evidence.

---

## M0 — Harness + one worked example per core suite + CI green

The skeleton the rest hangs on. Small, but exercises the whole pipeline end to end.

- **Runner** (`runner/`, Python 3.11 stdlib, [DL-0002]): walk `suites/`, parse
  `case.toml`, invoke the adapter, normalize, decide pass/fail, report per-case glyphs +
  rolled-up counts. `--regenerate` and `--adapter` flags.
- **Reference adapter** (`runner/adapters/kicad`): `kicad-cli` discovery
  (`KICAD_CLI`→PATH→install dirs, newest-numeric-first), verb mapping ([`DESIGN.md`](DESIGN.md) §2),
  scratch-copy for in-place `upgrade`, records `version --format about`.
- **Normalization layer** ([`DESIGN.md`](DESIGN.md) §4) + a **run-twice determinism test**
  that proves each normalizer load-bearing.
- **One worked `happy` + one `failure` case** in each of: `schematic-parse`,
  `board-parse`, `drc`, `gerber`. (Enough to exercise `exit`, `structured`, `golden-file`,
  and `golden-dir` comparison modes.)
- **CI** (`.github/workflows/conformance.yml`): gating job in `kicad/kicad:10.0.5` with
  `LC_ALL=C.UTF-8`/`TZ=UTC`, path-filtered to `suites/` + `runner/` ([DL-0010]);
  non-gating `kicad/kicad:nightly` (10.99) tracking job.

**Exit criteria:** `python -m runner suites/` green locally and in the 10.0.5 Docker CI;
all four comparison modes demonstrated; determinism test proves ≥1 normalizer red-when-
disabled.

---

## M1 — Parse suites (schematic + board)

Deepen the `parse-*` / `upgrade` surface — the highest-value format-documentation work.

- `schematic-parse` and `board-parse`: happy (canonicalization goldens) + a broad set of
  `failure/` cases (unterminated s-expr, unknown token, bad layer count, missing required
  field, malformed UUID), each citing its `doc` section.
- Ratify fixture provenance ([DL-0011]) and add the seed-and-upgrade `tools/` helper.
- Begin the format-doc-citation index: every case's `doc =` field, surfaced in reports.

**Exit criteria:** parse suites cover the documented top-level tokens of `.kicad_sch` and
`.kicad_pcb` for happy + representative failure; goldens for 10.0.5 committed.

---

## M2 — Netlist + ERC

First `structured` semantic-reduction suites.

- `netlist`: `sch export netlist --format kicadsexpr` reduced to net→{(refdes,pin)}
  membership ([`DESIGN.md`](DESIGN.md) §3b). Happy cases (shared pin, multi-net) + failure (unresolved
  connection).
- `erc`: `sch erc --format json --severity-all` reduced to a sorted violation set.
  Happy (clean sheet, expected-violation reported) — an ERC finding is data, not a tool
  failure ([`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §6).

**Exit criteria:** netlist membership + ERC violation-set comparison green; residue
bucketing (names-only/count/membership) reported on any mismatch.

---

## M3 — DRC

- `drc`: `pcb drc --format json --units mm --severity-all` (no `--exit-code-violations`,
  no `--refill-zones`) reduced to a sorted `(rule-id, severity, sorted item locations)`
  set. Cases per rule class (clearance, unconnected, courtyard, silk-over-pad, …).
- Name-and-exclude any irreducibly nondeterministic DRC fixtures (wobbling violation set)
  with a `skip_reason`.

**Exit criteria:** representative DRC rule classes covered happy + failure; excluded
fixtures documented and counted.

---

## M4 — Gerber + drill (fabrication output)

First heavy `golden-dir` suites, and the first multi-operation cases.

- `gerber`: `pcb export gerbers` → normalized RS-274X golden set (strip `G04`
  date/`TF.GenerationSoftware` headers). Cases exercising `--precision {5,6}`, `--no-x2`,
  layer selection, DNP handling.
- `drill`: `pcb export drill --generate-report --report-path <r>` → Excellon + report
  goldens (strip header date/version).
- At least one **multi-operation** case: one board fixture feeding `parse-pcb` + `drc` +
  `export-gerbers` ([`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §5.3).

**Exit criteria:** gerber + drill golden-dir comparison green with header normalization
proven load-bearing; multi-op case demonstrates one input → multiple goldens.

---

## M5 — Symbol / footprint libraries (+ pos, bom)

- `symbol-lib`: `sym upgrade` / `sym export svg`; `footprint-lib`: `fp upgrade` /
  `fp export svg`. Handle the `-o` directory gotchas ([`DESIGN.md`](DESIGN.md) §2).
- Fill in `export-pos` (CSV) and `bom` (fixed field/sort spec) as `golden-file` suites.

**Exit criteria:** library upgrade/export suites green; pos/bom goldens stable.

---

## M6 — Coverage infrastructure

The gap-finding development loop ([DL-0006]).

- `tools/coverage/`: scripts to build instrumented KiCad from source
  (`gitlab.com/kicad/code/kicad`, Debug `-O0 --coverage`, `KICAD_BUILD_QA_TESTS=OFF`),
  run the whole suite once, merge with `lcov`/`gcovr` (GCC-version-matched), filter
  `/usr`+`thirdparty/`+`qa/`, emit HTML + cobertura summary.
- `.github/workflows/coverage.yml`: **scheduled weekly / per-KiCad-bump**, self-hosted/
  beefy runner, never on the PR path.
- Process: turn uncovered KiCad modules into a new-case backlog feeding M1-M5 growth.

**Exit criteria:** one full coverage run produces a gap report; ≥1 new case authored from
an identified gap, closing the loop.

---

## M7 — Second adapter (ecosystem) + divergence ledger

Prove goal #2: the same corpus drives a non-KiCad implementation.

- Implement a second adapter (candidate: the local `pcb` Rust engine) against the verb
  protocol ([`DESIGN.md`](DESIGN.md) §2).
- Run `suites/` through it against the **KiCad-authored goldens**; stand up the
  checked-in **divergence ledger** ([DL-0009]) with a per-entry verdict.
- CI: add the second-adapter job (may be non-gating initially).

**Exit criteria:** the identical `suites/` corpus runs through two adapters; every
second-adapter failure is triaged in the ledger, not hidden.

---

## Later / conditional

- **STEP / 3D conformance** — only if ratified ([DL-0012]); opt-in suite, geometric
  (bbox/mesh) comparison at printed-quantum tolerance, never byte-exact.
- **`import`** (`pcb import` from Altium/Eagle/… → `.kicad_pcb`) as a parse-target suite.
- **KiCad 11** — when `kicad/kicad:11.0.0` publishes (~Q1 2027): add the matrix entry,
  `--regenerate` to populate `golden/11.0.0/`, promote from non-gating when stable.
- **`corpus/` broad regression** — a scheduled run of the real-world corpus for
  round-trip/DRC stability beyond the curated cases.

---

## Standing rules (apply from M0)

- **Record the full invocation + oracle version + date beside every headline number.**
  "A figure without a corpus size is stale by construction." Re-run a subagent's headline
  measurement yourself, ideally by a different method (pcb's hard-won lesson).
- **A test that cannot fail is not evidence** — break what a new case covers and watch it
  go red before trusting its green.
- **Never background a long sweep** — run in the foreground, in chunks; a backgrounded
  child can outlive the turn and lose results.
- **Never record a number you did not just watch a command print.**
