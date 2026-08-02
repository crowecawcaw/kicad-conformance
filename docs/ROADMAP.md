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
  that proves each normalizer load-bearing. Includes CRLF→LF and the `.gbrjob` JSON-date
  normalizer; goldens are regenerated in the Docker Linux image and stored LF, with a
  `.gitattributes` marking `golden/**` as LF ([DL-0016]).
- **Verdict classifier + positive-control machinery** ([`DESIGN.md`](DESIGN.md) §3a,
  [DL-0013]): the runner distinguishes `OK` / `REJECT` / `CRASH` (signal / exit `>128`,
  detected portably — never hard-coded 139), and runs each `failure` case's `control` and
  requires it to reach `OK`. A crash is never a pass.
- **Cheap coverage proxy** ([`DESIGN.md`](DESIGN.md) §7a): the runner emits a
  `--coverage-proxy` report (CLI subcommand/flag surface + format-token / top-level
  s-expr-section coverage) with **zero KiCad rebuild**. This — not line-coverage — is
  M0's gap signal. Line-coverage is out of M0 entirely (see M6).
- **One worked `happy` + one `failure` case** in each of: `schematic-parse`,
  `board-parse`, `drc`, `gerber`. (Enough to exercise `exit`, `structured`, `golden-file`,
  and `golden-dir` comparison modes, plus the crash/positive-control path on the
  `board-parse` failure case.)
- **CI** (`.github/workflows/conformance.yml`): gating job in `kicad/kicad:10.0.5` with
  `LC_ALL=C.UTF-8`/`TZ=UTC`, path-filtered to `suites/` + `runner/` ([DL-0010]);
  non-gating `kicad/kicad:nightly` (10.99) tracking job.

**Exit criteria:** `python -m runner suites/` green locally and in the 10.0.5 Docker CI;
all four comparison modes demonstrated; determinism test proves ≥1 normalizer red-when-
disabled; the crash verdict and positive control are exercised by the `board-parse`
failure case; `--coverage-proxy` emits a CLI-surface + format-token report. **Line-coverage
is explicitly not part of M0.**

---

## M1 — Parse suites (schematic + board)

Deepen the `parse-*` / `upgrade` surface — the highest-value format-documentation work.

- `schematic-parse` and `board-parse`: happy (canonicalization goldens) + a broad set of
  `failure/` cases (unterminated s-expr, unknown token, bad layer count, missing required
  field, malformed UUID), each citing its `doc` section and carrying a positive control
  ([DL-0013]). Schematic failures assert `Failed to load schematic`; PCB failures may
  assert the `Expecting` position, and any oracle crash (the known 10.0.5 PCB parse
  segfault) is reported as `CRASH` + logged to the ledger, never as a green pass.
- Fixture provenance is settled ([DL-0011], accepted): hand-author small/failure fixtures,
  seed-and-`upgrade` larger happy ones, all GUI-free/CLI-reproducible; add the
  seed-and-upgrade `tools/` helper.
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

- `gerber`: `pcb export gerbers` with an **explicitly pinned** `--layers` set +
  `--no-protel-ext` → normalized RS-274X golden set (strip `G04` date/`TF.GenerationSoftware`
  headers **and** the `.gbrjob` JSON date). Cases exercising `--precision {5,6}`, `--no-x2`,
  layer selection, DNP handling. Layer set is a per-case parameter, not a default
  ([`DESIGN.md`](DESIGN.md) §2b).
- `drill`: `pcb export drill --generate-report --report-path <r>` → Excellon + report
  goldens (strip header date/version).
- At least one **multi-operation** case in the **`integration/`** suite ([DL-0017]): one
  board fixture feeding `parse-pcb` + `drc` + `export-gerbers`
  ([`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §5.3); confirm its `drc`/`gerber` checks
  appear in the generated per-verb coverage index.

**Exit criteria:** gerber + drill golden-dir comparison green with header normalization
proven load-bearing; multi-op `integration/` case demonstrates one input → multiple
goldens and surfaces in the per-verb coverage index.

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
