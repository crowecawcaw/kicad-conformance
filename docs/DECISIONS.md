# Decision log

ADR-style, lightweight, numbered `DL-NNNN`, **append-only** (supersede, don't rewrite).
Each entry: status · context · decision · rationale · consequences. Statuses:
`accepted`, `proposed` (awaiting owner ratification), `superseded`.

Cross-referenced from [`DESIGN.md`](DESIGN.md), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md),
[`ROADMAP.md`](ROADMAP.md).

---

## DL-0001 — Primary target KiCad 10.0.5; version-parametric layout
**Status:** accepted (owner decision)

**Context.** KiCad 11 is not released (research 2026-08-02); the `master` dev line
reports `10.99` via nightlies, and 11.0 stable is expected ~Q1 2027. The suite needs a
concrete, stable oracle now.

**Decision.** Target **KiCad 10.0.5** (newest stable patch) as the primary and only
gating oracle. Make the layout version-parametric: goldens live under
`golden/<version>/`, CI pins `kicad/kicad:10.0.5`, and a non-gating `kicad/kicad:nightly`
(10.99) job tracks the moving 11 target. Nothing is gated on 11.

**Rationale.** 10.0.5 is a stable, reproducible, Docker-pinnable release; the nightly 11
is a moving target unsuitable for gating. Version-parametric goldens mean 11 slots in as
`golden/11.0.0/` when its tags publish, with no fixture changes.

**Consequences.** Goldens churn on each pinned-`kicad-cli` bump (managed by the
regenerate flow + version subdirs). A second gating version can be added later without
restructuring.

---

## DL-0002 — Runner language: Python 3.11+, standard library only
**Status:** accepted

**Context.** The runner must shell out to `kicad-cli`/Docker, run in CI, and stay easy
for EDA contributors and AI agents to read/extend. Options weighed: Python+pytest vs a
Rust/Go single-binary CLI ([`DESIGN.md`](DESIGN.md) §8).

**Decision.** A small **Python 3.11+ runner using only the stdlib** (`tomllib`,
`subprocess`, `json`, `pathlib`). Canonical entrypoint `python -m runner`; `pytest` is an
optional local-dev wrapper, not the contract. No third-party runtime dependency.

**Rationale.** Lowest contributor/agent friction; Python already ships in the
`kicad/kicad` Docker image and on every CI runner; `tomllib` removes the PyYAML
dependency; the runner is I/O-bound on `kicad-cli` so Rust/Go's speed edge is
irrelevant. openjd's reference harness set the Python precedent.

**Consequences.** No single static binary to distribute (acceptable — Python is
omnipresent in this context). The adapter boundary being a subprocess contract
([DL-0007]) means this choice does **not** impose Python on any implementation-under-test.

---

## DL-0003 — Test-case format: per-case directory + tiny TOML manifest (hybrid)
**Status:** accepted

**Context.** openjd encodes expectations purely in filenames because every case is the
same operation. KiCad has diverse verbs, multi-file outputs, per-version goldens, and a
requirement that one input drive multiple checks — none expressible in a filename.

**Decision.** A case is a **directory** with a small **`case.toml`** manifest, the
fixture(s), and an optional `golden/<version>/` tree. Suite, polarity, and concept are
still encoded in the **directory path + slug** to keep openjd's docs-as-tests property.
TOML, not YAML.

**Rationale.** The manifest is the minimum needed to name a verb, an expectation, and a
golden; the common single-check case is ~4 lines. TOML gives comments, is
whitespace-insensitive, and matches the surrounding ecosystem (Cargo/pcb use TOML).
Directory-encoded suite/polarity keeps a listing readable as a coverage map.

**Consequences.** Slightly more ceremony than openjd's bare files, but it scales to
multi-verb/multi-golden cases. The manifest schema is the authoring contract
([`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §4), documented independently of the runner
so alternate runners don't drift.

---

## DL-0004 — Goldens are per-KiCad-version, oracle-authored, regenerable
**Status:** accepted — **renamed by [DL-0023]**: everything below still holds, but the
directory is `expected/<version>/` and the artifact is called an *expected file*, not a
"golden".

**Context.** Rich outputs (gerbers, drill, upgraded s-expr, DRC JSON) need a reference to
compare against. That reference must represent KiCad's actual behavior at a version.

**Decision.** Goldens live at `golden/<kicad-version>/` inside each case, are **generated
by the reference `kicad-cli`** (never hand-written) via `python -m runner --regenerate`,
and are keyed by **oracle version, not adapter**. A second adapter compares its output
against the same KiCad golden.

**Rationale.** A hand-written golden encodes a human's belief; a generated one encodes
KiCad's behavior — only the latter is a conformance reference. Per-version subdirs let
multiple KiCad versions coexist. Oracle-authored goldens are the core principle of
KiCad-as-oracle: the reference tool defines the correct answer, never a human.

**Consequences.** Goldens must be regenerated and diff-reviewed on each pinned-version
bump or fixture `(version YYYYMMDD)` bump. Repo carries generated artifacts under version
control (acceptable — they're the reference).

---

## DL-0005 — Normalization: pin the environment + strip enumerated nondeterminism
**Status:** accepted

**Context.** `kicad-cli` output carries build/time/identity noise (timestamps,
`generator_version`, fresh UUIDs, gerber/Excellon headers, locale-formatted numbers).
Comparisons must ignore noise without hiding real differences.

**Decision.** (a) Run every adapter call with `LC_ALL=C.UTF-8`, `TZ=UTC`, from a fixed
cwd — removing locale/timezone/path drift at the source. (b) Apply per-output-kind
normalizers stripping the enumerated sources ([`DESIGN.md`](DESIGN.md) §4). (c) **Keep**
the format `(version YYYYMMDD)` token (re-baseline on bump, don't strip). (d) Add **no**
normalizer where output is provably byte-stable; name-and-exclude irreducibly
nondeterministic fixtures. (e) Prove each normalizer load-bearing via a run-twice
determinism test.

**Rationale.** Adopts prior clean-room-engine normalization/determinism findings and this
project's own CLI-research determinism list. The "no identity normalizer" and "prove it
goes red" rules keep the layer honest.

**Consequences.** A maintained normalizer table that must be revisited on KiCad bumps
(new output kinds, changed headers). Some fixtures are permanently excluded and counted.

---

## DL-0006 — Line coverage is scheduled from-source infra, not a per-PR check
**Status:** accepted

**Context.** The development loop wants KiCad *source* line coverage to find suite gaps.
KiCad has no turnkey coverage mode; it needs a from-source Debug `-O0 --coverage` build
dominated by OpenCASCADE — ~30-90 min on a strong machine, plausibly 2-4+ h on a hosted
2-vCPU runner.

**Decision.** Build instrumented KiCad **once per pinned revision on a self-hosted/beefy
runner**, run the whole suite once, merge with `lcov`/`gcovr`, and publish a gap report.
Cadence: **weekly / per-KiCad-bump**, never on the PR hot path. Uncovered modules become
new-case backlog.

**Rationale.** Honest about cost — instrumented KiCad is heavy and fragile
(OCC/wx/gcov-version matching). Per-PR is infeasible; scheduled is genuinely useful for
gap-finding.

**Consequences.** Needs self-hosted CI (available per the owner's infra). Coverage is
advisory infra, decoupled from the gating suite. Lands in M6.

---

## DL-0007 — Adapter contract is a language-agnostic subprocess protocol
**Status:** accepted

**Context.** Implementations-under-test are heterogeneous: KiCad (C++ CLI), a Rust engine
(pcb), possibly a web viewer. Goal #2 requires the same corpus drive all of them.

**Decision.** The adapter is an **executable** invoked as
`<adapter> <verb> --in <path…> --out <dir> [flags]`; the runner inspects exit code,
captured stdout/stderr, and files written under `--out`. Capability verbs are declared in
data; unsupported verbs skip-and-count. The reference adapter wraps `kicad-cli`.

**Rationale.** A subprocess boundary is the lowest common denominator every tool
satisfies, and it decouples the runner language (Python, [DL-0002]) from the
implementation language. Directly generalizes openjd's two-verb CLI adapter.

**Consequences.** Adapters do a little glue (scratch-copy for in-place `upgrade`,
`-o` handling for `fp`/`sym`). The verb protocol is a documented contract that must stay
stable as suites grow.

---

## DL-0008 — Comparison model: exit / structured / golden-file, plus printed-quantum tolerance
**Status:** **superseded in part** — the `golden-file`/`golden-dir` mode is deleted by
[DL-0024], and the `compare` field that selected a mode is deleted by [DL-0023]
(comparison now follows from the verb). The `exit` mode, the semantic reduction, and the
printed-quantum / no-pre-authorized-bands rule survive unchanged.

**Context.** Different outputs need different comparison strength: exit polarity for
parse/failure, semantic membership for DRC/ERC/netlist, normalized text diff for
gerbers/drill/s-expr, and (if geometry is in scope) numeric tolerance.

**Decision.** Three primary modes — `exit` (polarity + optional stderr substring),
`structured` (canonical semantic reduction: netlist net→node membership; DRC/ERC sorted
violation set), `golden-file`/`golden-dir` (byte-exact after normalization) — plus a
**printed-quantum** tolerance for any numeric export (tolerance = the precision the
export prints; **no pre-authorized tolerance bands**).

**Rationale.** Three comparison forms match the three kinds of output (polarity,
semantic, formatted text), and the printed-quantum tolerance follows the principle that a
pre-approved tolerance band silently absorbs a real bug. Substring/structural matching
pins the observable contract without over-fitting to KiCad's exact formatting, so a second
adapter can conform.

**Consequences.** Each verb specifies its reduction/normalization, documented so a second
adapter emits a comparable shape. `structured` failures are bucketed (names-only vs
membership vs count), not just "differs."

---

## DL-0009 — Two corpora: curated `suites/` (committed) + real-world `corpus/` (gitignored); plus a divergence ledger
**Status:** accepted

**Context.** Two different needs: (a) small, hand-authored, single-concept cases for
documentation/agents; (b) thousands of real projects to drive the coverage sweep and
broad regression. And a place to record where a second adapter diverges from KiCad.

**Decision.** Keep them separate. **`suites/`** is committed, curated, hand-authored.
**`corpus/`** holds a committed `manifest.toml` (pinned SHA + SPDX + `permissive` flag
per project) and a **gitignored `projects/`** — downloaded, never redistributed,
regenerable, disk-guardrailed — under a simple corpus policy (accept any project with a
LICENSE; reject only license-less repos). A checked-in **divergence ledger** triages each
known second-adapter failure with a per-entry verdict ("KiCad/golden right, fix the tool"
vs "suite wrong"), after openjd's `OPENJD_TEST_RESULTS.md`.

**Rationale.** Curated cases must stay small and readable; coverage needs breadth. Mixing
them would bloat the docs corpus and muddy the coverage map. The ledger lets the suite be
stricter than any single tool without hiding regressions.

**Consequences.** Two ingestion paths (author-a-case vs `corpus sync` from manifest).
Ledger needs periodic reconciliation so it doesn't rot into a dumping ground.

---

## DL-0010 — CI: gate on `kicad/kicad:10.0.5` Docker; non-gating nightly job
**Status:** accepted

**Context.** CI must run against real KiCad, reproducibly, and also watch the moving 11
target.

**Decision.** Gating job runs in the `kicad/kicad:10.0.5` container (pin by digest for
strongest reproducibility), `LC_ALL=C.UTF-8`/`TZ=UTC` set, path-filtered to `suites/` +
`runner/`. A separate **non-gating** `kicad/kicad:nightly` (10.99) job reports drift.
Record `kicad-cli version --format about` in every run.

**Rationale.** Docker pins an exact patch with no apt state; nightly tracking surfaces
KiCad-11 breakage early without blocking PRs. Mirrors openjd's per-implementation,
path-filtered CI.

**Consequences.** Nightly job will go red as 11 evolves; kept non-gating and triaged into
the divergence ledger. Coverage runs on a separate schedule ([DL-0006]).

---

## DL-0011 — Fixture provenance for curated cases: hand-author small, generate large
**Status:** accepted (default ratified; owner may revisit)

**Context.** Curated `suites/` fixtures can be (a) hand-authored minimal s-expr, or (b)
generated by `kicad-cli` from seeds. Malformed `failure/` fixtures essentially must be
hand-authored (to place the exact defect); realistic `happy/` boards are tedious to
hand-write.

**Proposed decision.** Hand-author minimal fixtures for `parse-*` and all `failure/`
cases (full control over the exact tokens under test, tiny, self-documenting), and derive
larger `happy/` board/schematic fixtures by upgrading minimal seeds through
`kicad-cli … upgrade --force` so they are guaranteed-valid KiCad artifacts, committing the
upgraded form as the fixture.

**Rationale.** Failure cases need surgical control a generator can't give; large valid
fixtures are more reliably produced by KiCad itself. But hand-authoring a plausible-yet-
malformed s-expr is real labor, and "seed then upgrade" adds a provenance step — the
owner should confirm this split before M1 fixtures are written.

**Consequences.** A documented per-suite provenance convention; a small `tools/` helper to
seed-and-upgrade. The owner may revisit the split as the corpus grows.

**Ratified default (2026-08-02).** Accepted with this default: curated fixtures are
**hand-authored when small** (all `failure/` and minimal `parse-*` cases, for surgical
control of the exact defect/token under test) and **seed-and-`upgrade`** when larger
(realistic `happy/` boards/schematics derived by `kicad-cli … upgrade --force` from a
committed minimal text seed, committing the upgraded form). The previously-open question
is resolved: **every committed fixture must be reproducible without the KiCad GUI** — i.e.
CLI-reproducible from a committed text seed, with **no GUI-only artifacts**. This keeps the
whole corpus regenerable and reviewable. The owner may revisit.

---

## DL-0012 — STEP / 3D export conformance: defer, pending ratification
**Status:** accepted — **deferred** (out of scope for now; owner may revisit)

**Context.** The owner's suite list centers on parsing, DRC, ERC, gerber, drill, netlist,
libs. STEP/3D (`export step`, `render`) are attractive but are the **least deterministic**
outputs (OCC ISO-10303 timestamp + author + non-stable tessellation/entity ordering),
need OpenCASCADE, and sometimes a display (`xvfb-run`).

**Proposed decision.** **Defer** STEP/3D conformance out of the initial milestones. If
pursued later, compare **geometrically** (bounding box / mesh) at a printed-quantum
tolerance, never byte-exact, and gate it behind an opt-in suite. The verb `export-step`
is reserved in the adapter contract but unused until ratified.

**Rationale.** 3D adds the most nondeterminism and the heaviest dependency for the least
diffable output; the high-value conformance surfaces (parse, DRC/ERC, gerber, drill,
netlist) don't need it. Better to ship those solidly first.

**Consequences.** `step`/`render` stay out of M0-M7; a later milestone can add an opt-in
`step/` suite with geometric comparison.

**Ratified (2026-08-02): accepted = deferred.** STEP/3D conformance is **out of scope for
now**. If pursued later, comparison is **geometry-only** (bounding box / mesh) at a
printed-quantum tolerance, never byte-exact, behind an opt-in suite. The `export-step` verb
stays reserved-but-unused. The owner may revisit.

---

## DL-0013 — Crash is a distinct verdict and never a pass; every failure case needs a positive control
**Status:** accepted

**Context.** Empirically (KiCad 10.0.5), a truncated board makes `pcb upgrade` print a
good `Expecting '('` message and then **segfault** (exit 139 native Windows / `SIGSEGV`
Docker Linux). 139 is non-zero, so a naïve "non-zero = rejected" rule silently passes an
`expect="error"` case on a **crash** — building the PCB `failure/` corpus on a KiCad bug.
Separately, the schematic loader emits only `Failed to load schematic` for *every* defect,
so stderr cannot prove which defect fired.

**Decision.** (a) The runner classifies each invocation as **`OK` / `REJECT` / `CRASH`**.
`CRASH` = killed by a signal, or exit code `> 128` (128 + signal; on Windows a fatal-
exception status), detected **portably** — never by the literal 139. A `CRASH` is reported
as its own verdict and is **never a pass**, for `happy` or `failure`; `expect="error"` is
satisfied only by a `REJECT` (a bounded, graceful non-zero exit). (b) **Every `failure`
case carries a positive control** (`control` field): a defect-free variant the runner runs
through the same check and requires to reach `OK`. A case whose control does not flip to
`OK` is reported **not-evidence**, never passed.

**Rationale.** Exit *polarity* is reliable; exit *code* and stderr *content* are not
(platform-dependent crash codes; undiscriminating schematic message). The crash verdict
stops a KiCad bug from laundering into "conformant"; the positive control replaces the
stderr-can't-provide "fails for the right reason" guarantee with an executable one.

**Consequences.** Known oracle crashes (the 10.0.5 PCB parse segfault) are filed upstream
and recorded in the divergence/known-issues ledger; the paired PCB failure case asserts
the real `Expecting` substring so a future clean rejection conforms without an edit. The
`control` field and the classifier land in M0.

---

## DL-0014 — `structured` goldens store a canonical reduction, not the raw report
**Status:** accepted (clarifies [DL-0008]) — **renamed by [DL-0023]**: the stored
canonical reduction is now called the *expected file* and lives in `expected/<version>/`;
the principle (store the reduction, never the raw report) is unchanged and is why
`model.json` is what it is.

**Context.** Early wording was incoherent: DESIGN §5 called DRC "structured, no stored
golden — derived from the golden JSON," while the schema stored `golden = "drc.json"`. It
was unclear whether contributors commit the raw KiCad JSON or a reduction.

**Decision.** For a `structured` check the committed golden is the **canonical reduction**
(e.g. `drc.reduced.json`, or the net→node map) produced by `--regenerate` applying the
per-verb reduction to the oracle output — **not** the raw KiCad report. At compare time the
runner reduces the adapter's output the same way and asserts **membership equality**.

**Rationale.** Storing the reduction makes the golden self-describing, diffs review as
semantic changes, and a second adapter is judged on exactly the reduced shape it must
emit. The raw report's formatting/ordering/IDs are noise the reduction already discards.

**Consequences.** Regenerate writes the reduced form; the schema's `golden` field is
required for `structured` too and documents the reduction it names.

---

## DL-0015 — Byte goldens are a KiCad-regression signal; cross-adapter conformance is the semantic subset
**Status:** superseded by [DL-0024], then **partially REINSTATED by [DL-0026]**. The
analysis below stands. Its scoping rule — *byte answers are a KiCad-version-regression
signal; a second implementation is judged on the semantic subset* — is **current policy
again**, but only for **fabrication output** (`gerbers/`, `drill/`). Its application to
re-serialized `.kicad_pcb`/`.kicad_sch` bytes stays deleted, because the summary
([DL-0028]) is a strictly better comparator for those. See [DL-0026] §"Why this is not a
repeat of the mistake".

**Context.** `golden-file`/`golden-dir` compares pin KiCad's *exact formatting* (token
order, whitespace, aperture numbering, comment style). A clean-room second adapter emits
valid-but-differently-formatted output and would "diverge" on essentially every
upgrade/gerber golden for reasons that are **not bugs**, drowning the divergence ledger in
formatting non-findings.

**Decision.** Classify the compare modes by what they measure. **`structured`/semantic
compares + exit polarity + error substrings are the cross-adapter conformance signal** and
are how a second adapter is judged. **`golden-file`/`golden-dir` byte compares are a KiCad
self-consistency / version-regression tool**, primarily meaningful for the KiCad adapter.
A second adapter runs the **semantic subset** of the byte-golden verbs (parse both sides,
compare the model), not the byte compare; a formatting-only diff that reduces to an
identical semantic model is **auto-classified formatting-only** and kept out of conformance
findings and the ledger.

**Rationale.** Goal #2 (one corpus, many implementations) is genuinely delivered for the
portable subset and is aspirational for byte goldens until they have a semantic reduction.
Being explicit prevents the M7 ledger from rotting into a formatting-diff dump.

**Consequences.** The second-adapter path (M7) runs the semantic subset by default; byte
goldens gate the KiCad adapter across version bumps. Some upgrade/gerber verbs need a
structural reduction defined before they cross-check a foreign tool.

---

## DL-0016 — Goldens are Docker-Linux-authored and stored LF; normalize line endings
**Status:** accepted — **renamed by [DL-0023]**: read `golden/**` as `expected/**`
throughout. The Docker-Linux-authored, stored-LF rule is unchanged.

**Context.** CI compares inside the `kicad/kicad:10.0.5` **Docker (Linux)** image, but a
contributor may develop on native Windows, where `kicad-cli` writes **CRLF** (and can leak
`\` path separators into messages). A Windows-regenerated text golden would mismatch a
Linux-CI run on line endings alone.

**Decision.** (a) Normalize **CRLF↔LF** for every text golden and store **LF** in the repo.
(b) **Committable goldens are regenerated inside the Docker Linux image** — `--regenerate`
is run in the container so the bytes are platform-canonical; a Windows-native regenerate is
fine for local iteration only. (c) A **`.gitattributes`** marks `golden/**` (and text
fixtures) as LF so git does not re-mangle them on checkout. Linux is the canonical golden
platform.

**Rationale.** Removes a whole class of false diffs (line endings, path separators) that
would otherwise make every text golden fail across the Windows-dev / Linux-CI split.

**Consequences.** Contributors on Windows regenerate via Docker for anything they commit.
The `.gitattributes` and CRLF→LF normalizer land in M0.

---

## DL-0017 — Multi-verb cases live in a dedicated `integration/` suite; per-verb coverage via a generated index
**Status:** **SUPERSEDED by [DL-0022].** The `integration/` suite is retired and its one
case deleted: the composite `model` verb *is* "one input, many projections", so a case
that validates a whole board no longer spans several verbs and simply lives in its input's
own suite. The generated per-verb coverage index survives (it is part of the coverage
proxy, DESIGN §7a) and is now the only mechanism this entry contributes.

**Context.** The design's headline virtue is "the directory listing IS the coverage map."
A multi-operation case (one board feeding `parse-pcb` + `drc` + `export-gerbers`) placed
under `board-parse/` hides its DRC/gerber checks from someone browsing `drc/` or `gerber/`,
defeating that property for exactly the verbs hardest to enumerate.

**Decision.** Keep **single-verb cases the norm** in each verb suite (so those listings
stay pure coverage maps), and put **multi-verb cases in a dedicated `suites/integration/`
suite**. The runner emits a **generated per-verb coverage index** (`--coverage-proxy`,
DESIGN §7a) that lists **every** `[[check]]` by its `op` regardless of directory, so an
`integration/` case's `drc`/`gerber` checks still appear under those verbs.

**Rationale.** The simplest mechanism that keeps every verb suite's directory listing an
honest coverage map while still allowing the owner's "one input, several outputs" cases —
the generated index restores cross-suite visibility instead of relying on the tree alone.

**Consequences.** New suite `integration/{happy,failure}/`; TEST_CASE_FORMAT §1/§2, the
README repo-map, and ROADMAP M4 updated. The coverage index is part of the M0 coverage
proxy.

---

## DL-0018 — Known-oracle-divergence declaration is a strict-xfail layer, not a skip
**Status:** accepted

**Context.** `board-parse/failure/0001-unterminated-sexpr` makes `kicad-cli` 10.0.5
print the correct `Expecting …` message and then segfault (`CRASH`, [DL-0013]) -- a
confirmed KiCad bug, not a harness defect. A `CRASH` is never a pass, so as originally
authored this one case made `python3 -m runner suites/` exit non-zero forever, on a bug
that is not this repo's to fix. A brand-new public repo cannot ship with a
permanently-red gating build: that destroys CI's regression signal (a real regression
introduced later would be indistinguishable from the pre-existing red). Two options were
weighed: (a) silently loosen the case (e.g. drop `expect="error"` to tolerate the crash,
or add a `skip_reason`), or (b) declare the divergence as data and reinterpret the
already-expected bad verdict without touching the OK/REJECT/CRASH classifier itself.
Option (a) either hides a real bug behind a passing case or removes the case from the
suite's coverage map entirely -- both violate goal #1 (documentation) and DL-0013's
"a crash is never a pass." Option (b), modeled on OpenJD's and this project's own
divergence-ledger precedent ([DL-0009]), keeps the assertion honest (the case still
declares the *desired* graceful rejection) while keeping the build green.

**Decision.** A case (or an individual `[[check]]`) may declare a `known_divergence`
table: `reason` (required, one line), `kind` (required, e.g. `"crash"`), `tracking`
(optional, an upstream issue URL/id or a `"TODO: file upstream"` placeholder). Semantics
are **strict xfail**, applied as a layer on top of the existing OK/REJECT/CRASH verdict
(DESIGN.md §3a) -- it never changes what the verdict *is*:

- If the actual verdict matches the declared `kind` (e.g. `CRASH` for `kind = "crash"`),
  the check is scored **`XFAIL`** ("known divergence") -- not a failure; the build stays
  green.
- If the same check instead comes back clean (`OK`/graceful `REJECT` -- the oracle got
  fixed), that is an **`XPASS`** -- and XPASS **fails the build** with a message pointing
  at `docs/DIVERGENCES.md`, because a strict xfail that can silently rot is not
  evidence of anything. This is deliberately *stricter* than a conventional pytest-style
  `xfail` (which usually tolerates an XPASS quietly) -- see the OpenJD prior art and
  [DL-0009]'s "a test that can't fail is not evidence" thread.
- A case with no `known_divergence` behaves exactly as before this decision; `XFAIL`/
  `XPASS` are new, separately-counted verdicts alongside `PASS`/`FAIL`/`CRASH`/`SKIP`/
  `NOT-EVIDENCE`/`NEEDS-REGEN` in the summary.
- The positive control ([DL-0013]) is unaffected and still required/run: `known_divergence`
  only reinterprets the *main* check's already-bad, already-declared verdict, never the
  control's.

The one existing case this applies to (`board-parse/failure/0001-unterminated-sexpr`)
keeps its `expect="error"`, `error_contains = "Expecting"`, and positive control exactly
as before -- only a `[known_divergence]` table (`kind = "crash"`) is added, and its
inline `concept` is trimmed back to the one-sentence desired-behavior statement, with the
bug narrative moved to the new checked-in ledger, `docs/DIVERGENCES.md`.

**Rationale.** The runner's OK/REJECT/CRASH classifier ([DL-0013]) must stay a single
source of truth for what actually happened -- inventing a fourth raw verdict, or
special-casing the classifier per-case, would blur that. Scoring the divergence as a
*presentation* layer on top keeps the classifier pure while still letting a case be
simultaneously honest (it still says what KiCad *should* do) and non-blocking (it doesn't
red the build over a bug filed upstream). Strictness (XPASS fails loudly) is what
prevents this from degenerating into a permanent, unreviewed skip -- exactly the failure
mode a `skip_reason` or a loosened assertion would have produced silently.

**Consequences.** `runner/manifest.py` parses `known_divergence` (case-level default,
check-level override, mirroring the existing `control` resolution pattern);
`runner/engine.py` scores it after the positive control passes; `runner/cli.py` treats
`XFAIL` as non-failing and `XPASS` as failing in the per-case rollup. `docs/DIVERGENCES.md`
is the checked-in ledger DL-0009 anticipated but had not yet been created; entries there
must be kept current with the `tracking` field (upstream issue) once filed.

---

## DL-0019 — L2 (semantic extraction) and L3 (vector render) are first-class comparators
**Status:** **superseded in part** — the *substance* (compare meaning, not bytes; derive it
from interchange exports; reuse the reduction machinery) is now the core of the design, but
the *packaging* changed twice over: [DL-0022] composes the individual L2 projections into a
single `model`, [DL-0024] deletes the L1 rung entirely, and the L0–L3 ladder vocabulary is
retired with it (three named comparison kinds — exit, model, render — need no ladder). The
per-projection `case.toml` examples in this entry are obsolete; see
[`VALIDATION.md`](VALIDATION.md).

**Context.** M0 validates a parser by exit polarity — *did it load* (DESIGN §3a) — plus L1
byte goldens (KiCad-regression) and the embryonic `structured` reductions for DRC/ERC/
netlist. That does not answer *did it load into the **right model***: a tool can exit 0 and
still mis-net a pad, drop a via, or mis-rotate a footprint. The owner asked for two richer
layers, implementation-fair across tools.

**Decision.** Formalize a four-rung **comparator ladder** (VALIDATION.md §1): **L0** exit ·
**L1** canonical-serialize (KiCad-regression) · **L2** semantic extraction/interchange ·
**L3** vector render (SVG). L2 and L3 are added as first-class comparators. **L2** derives a
normalized, structured projection of *meaning* (connectivity, counts, geometry, placement)
from an interchange export and compares by **membership/field** — implemented by extending
the existing `structured` mode and `runner/reduce.py` (new `reduce_stats`, `reduce_pos`,
`reduce_ipcd356`; a `kicadxml` reader for `reduce_netlist`). **L3** exports SVG and compares
the drawn geometry (new `image` compare mode). One fixture yields many projections, each an
independent comparator; a projection's cross-impl fairness tracks how much it captures
*meaning* vs. KiCad's byte-formatting.

**Rationale.** L2/L3 are portable (they measure meaning, not bytes), so they — not L1 — are
the cross-adapter conformance signal ([DL-0015]), while reusing the whole M0 machinery
(scratch-copy, explicit `-o`, canonical-reduction goldens [DL-0014], membership compare,
determinism self-test). Grounded empirically on `kicad-cli` 10.0.5: `pcb export stats`
(only `metadata.date` nondeterministic), `pcb export pos --format csv` and `pcb export
ipcd356` (both byte-identical run-to-run), and netlist recoverable from *both* `kicadsexpr`
and `kicadxml` (cross-format fairness).

**Consequences.** New verbs `export-stats`, `export-ipcd356`, `export-svg-{pcb,sch,sym,fp}`,
and the promoted `export-pos` (VALIDATION.md §5.1); a new `image` compare mode and an `svg`
normalizer; new `reduce_*` functions. Goldens gain `*.reduced.json` (L2) and reference SVG/
PNG (L3) forms. Build order in VALIDATION.md §8 (stats → pos → ipcd356 → svg).

---

## DL-0020 — Gerber geometry is NOT reduced structurally; board copper is covered by stats+pos+ipcd356+SVG
**Status:** accepted — **but its safety net is gone.** The decision (do not build an
RS-274X structural reduction) stands. Its stated consequence — "the `gerber` suite keeps
its L1 `golden-dir` byte compare as a KiCad-version-regression signal" — is **void**: that
byte compare is deleted by [DL-0024], so gerber coverage is now **zero**, not "byte-only".
See [DL-0024] and [`VALIDATION.md`](VALIDATION.md) §7.

**Context.** An L2 for the `gerber` suite could be a structural RS-274X reduction (apertures
+ flashes/draws with per-layer coordinates). Whether that is worth building was left as an
explicit call for VALIDATION.md.

**Decision.** **Do not build a Gerber structural reduction.** Board copper *meaning* is
instead covered by the composition of **stats** (copper areas, track/via/pad counts, min
widths), **pos** (placement), **ipcd356** (net→pad connectivity + access-point geometry),
and **L3-SVG** (the drawn copper geometry). The `gerber` suite keeps its L1 `golden-dir`
byte compare as a KiCad-version-regression signal only.

**Rationale.** A faithful RS-274X reducer is a second rasterizer's worth of work (aperture
macros, `%LP` polarity, `G36/G37` regions, arc interpolation, step-and-repeat, `%FS`
coordinate format), and its cross-impl output is as formatting-sensitive as the byte golden
it was meant to improve on — two conformant plotters legitimately decompose a pad or track
differently. The four projections above already localize every copper defect that matters
(wrong count/net/placement/area), and L3-SVG captures the geometry *fairly* (a raster is
decomposition-blind).

**Consequences.** **Honest gap:** a bug that corrupts RS-274X output while leaving the
`.kicad_pcb` model intact (a plotter-only aperture bug) is caught only by the L1 byte golden
(KiCad-regression, not cross-impl) and by L3-SVG — there is no portable Gerber-*native* L2.
Acceptable since gerber is a fab-output verb whose cross-impl story [DL-0015] already scopes
to the semantic subset; a Gerber reducer can be added later if a concrete second-adapter
need appears.

---

## DL-0021 — SVG L3: hybrid normalized-SVG-exact (KiCad-regr.) + pinned-`resvg` raster (cross-impl); audited tolerance
**Status:** accepted — unchanged in substance. Renamed by [DL-0022]/[DL-0023]: the four
`export-svg-*` verbs are one `render` verb dispatching on the input suffix, the `image`
compare mode is gone (comparison follows from the verb), and "L3" is just "render".

**Context.** L3 needs a deterministic SVG comparison in CI. Empirically the
`kicad/kicad:10.0.5` image ships **no SVG rasterizer** (probed: `rsvg-convert`, `resvg`,
`inkscape`, `cairosvg`, ImageMagick, `dvisvgm`, `pdftocairo` all MISSING; only ghostscript,
which is PS/PDF-only, and Pillow, which cannot rasterize SVG, are present). Separately,
KiCad's own `pcb export svg` output is deterministic **except** its `<title>` line
(filename + wall-clock date); path geometry is byte-stable run-to-run.

**Decision.** A **hybrid**, reconciled with the "no pre-authorized tolerance bands"
principle: **(a) KiCad-vs-KiCad regression** → normalize the `<title>`/`<desc>` and compare
the SVG **byte-exact after normalization** (no rasterizer, zero tolerance) — the default L3
comparator; determinism pinned via `--black-and-white --page-size-mode 2
--exclude-drawing-sheet` + `LC_ALL=C.UTF-8`/`TZ=UTC`. **(b) Cross-implementation** →
rasterize both SVGs with a **pinned `resvg`** (exact version, added to the CI image) at fixed
DPI/white-background, and pixel/SSIM-diff under an **explicit, per-case, documented threshold
that must be shown load-bearing** (perturb the geometry by one quantum → comparator goes
red, or the threshold is dead and removed — same rule as a normalizer, DESIGN §4a).

**Rationale.** KiCad's SVG being already-exact (§4.2) means the regression case needs no
renderer and gets a *stronger, cheaper* exact vector compare with no threshold — the ideal
for the "no silent band" rule. Rasterization is reserved for the genuinely-different case (a
clean-room tool's valid-but-differently-structured SVG, which exact-match would over-fit
like an L1 golden, [DL-0015]). `resvg` is chosen over `rsvg-convert`/Inkscape/cairosvg
because it is a single static binary with bundled fonts and CPU-deterministic rendering — no
cairo/pango/fontconfig/system-font variance to make pixels drift across machines; ghostscript
cannot read SVG at all.

**Consequences.** L3 goldens are the reference **SVG** (mode a, per KiCad version) and/or a
reference **PNG** (mode b). The `image` compare mode + `svg` normalizer land with L3 (build
step 4a); pinning `resvg` and the raster/SSIM path land with the second adapter (M7, step
4b) since KiCad-only conformance never rasterizes. Diff report: textual SVG diff (a) or
diff-image + %pixels + SSIM (b).

---

## DL-0022 — One composite `model` answer per case is the default; single projections are opt-in
**Status:** accepted (owner decision, 2026-08-02) — supersedes [DL-0017]; supersedes the
per-projection packaging of [DL-0019]. **Its manifest surface is superseded by [DL-0025]**
(the `op = "model"` / `[[check]]` shape is gone; answers now follow from the input's file
type) and **its filename by [DL-0028]** (`model.json` → `summary.json`). The principle —
one input, one composite answer, projections opt-in — is unchanged and is what [DL-0025]
takes to its conclusion. Read "model" throughout this entry as "summary".

**Context.** The design shipped under [DL-0019] gave every projection of a board its own
case: `board-parse/happy/0002-populated-board-stats` (inventory),
`0003-board-net-graph` (connectivity), `0004-fcu-render` (drawing) and
`placement/happy/0001-two-footprint-placement` (placement) were **four case directories
holding four byte-identical copies of one `board.kicad_pcb`** (verified: all four hash
`86f28fe9c64df93856f5de7ff446c9a9`), each asserting exactly one projection. Meanwhile the
one case that did span several verbs, `integration/happy/0001-board-parse-drc-gerber`,
used a *different*, near-empty board — so its `stats`/`pos`/`ipcd356`/`svg` checks all
asserted **emptiness**, validating nothing interesting. The owner's review was blunt: the
suite is fragmented and jargon-heavy, and the mental model should be *"each test case is
one schematic or board as input then one output that fully validates the parsing and
processing."*

**Decision.** Add a **`model` verb** that internally invokes whichever `kicad-cli` exports
it needs and merges them into **one normalized JSON document per input**
(`expected/<version>/model.json`). `model` is the **default check for every happy board
and schematic case**: one input, one answer, one check. The board model is composed from
`pcb export stats` (integer counts and the drill-hole table), `pcb export pos`
(placement) and `pcb export ipcd356` (net-to-pad connectivity); the schematic model from
`sch export netlist` (components + nets). **Individual projection verbs remain available**
(`pos`, `ipcd356`, `stats`, `netlist`, `render`, `drc`, `erc`) and are used when that
projection *is* the concept the case documents — in practice `render`, because drawn
geometry is the one thing the model does not capture. Composition happens **in the
adapter**, so a non-KiCad implementation emits `model.json` directly instead of imitating
three KiCad export formats. `model` dispatches on the input suffix and **does not apply to
libraries** (`kicad-cli` 10.0.5 offers no structured `.kicad_sym`/`.pretty` export —
verified: `sym export` and `fp export` both offer only `svg`), which use `render` instead.
The `integration/` suite is **retired** ([DL-0017] superseded): a multi-verb case was the
old way to say "one input, many outputs", and `model` is the new one.

**Rationale.** Fixture duplication was the real defect: four copies of one board meant a
fixture change had to be made four times, and no single case validated the board. Merging
the projections makes each case *stronger* (one file proves counts **and** placement
**and** connectivity **and** holes) and the repo *smaller* (11 cases → 7; 16 checks → 9).
A merged JSON document is also a better diff than four files: a pad moved to the wrong net
is one changed line. Empirically verified before adopting: the merged document is
**byte-identical run-to-run** for both board and schematic, and it is **falsifiable** —
deleting the board's only track, rotating a footprint by 45°, and moving one pad to
another net each produce a minimal, legible diff ([`VALIDATION.md`](VALIDATION.md) §4.6,
§4.7).

**Consequences.** New `runner/model.py` and a `cmd_model` in the adapter; the existing
`reduce_*` functions become its parsers and keep serving the standalone projections. The
model **excludes** `stats`' computed float areas and densities (owner's call: low
conformance value, high false-failure risk across implementations) and therefore records
routing only coarsely — a lost track shows up as `min_track_width` flipping to KiCad's
INT_MAX sentinel, and pad-within-footprint geometry is covered by `render`, not by the
model ([`VALIDATION.md`](VALIDATION.md) §7.3/§7.4). Four case directories and two suites
(`integration/`, `placement/`) are deleted; the migration is enumerated per case in
[`ROADMAP.md`](ROADMAP.md) M0.5.

---

## DL-0023 — `golden` → `expected`; drop `compare`; `expect` → `outcome` with a default
**Status:** accepted (owner decision, 2026-08-02) — renames [DL-0004]/[DL-0014]/[DL-0016];
removes the `compare` field introduced by [DL-0008]. **Partially superseded by [DL-0025]**:
the `compare` deletion stands and `expected/` stands, but `op`, `outcome` and the
`expected` *field* are themselves deleted — the fields this entry renamed no longer exist
to be named. The entry's reasoning (jargon is a defect; a field a case cannot get wrong is
better than a field with good documentation) is what [DL-0025] applies again.

**Context.** The owner's second objection was jargon. A contributor met "golden",
"structured", "reduced", "L2", "comparator ladder" and `compare = "golden-file"` before
meeting a single idea, and a manifest carried four fields (`op`, `expect`, `compare`,
`golden`) where two carry the information. "Golden file" is EDA/test-harness insider
vocabulary; nothing about the word says *recorded correct answer*.

**Decision.**
1. **`golden` → `expected`**, everywhere: the manifest field, and the directory
   `golden/<version>/` → `expected/<version>/`. The docs define the term once, in plain
   language: *an expected file is the recorded correct answer for one check — the output
   the reference tool produced when the case was written, generated once and frozen; other
   frameworks call it a snapshot, a baseline, or a golden file.*
2. **Delete `compare`.** How a check is compared follows from its `op`: `model`/`drc`/
   `erc`/`netlist`/`pos`/`ipcd356`/`stats` compare a normalized JSON document, `render`
   compares normalized SVG bytes, `parse-*` compares only the exit code. The field could
   only ever have let a case request a comparison its verb cannot perform.
3. **`expect` → `outcome`**, and make it **optional, defaulting to the directory
   polarity** (`happy/` → `"ok"`, `failure/` → `"error"`). `expect = "ok"` sitting beside
   `expected = "model.json"` read as two spellings of one thing; `outcome` says what it
   means. A happy case now writes no polarity field at all; a failure case writes
   `outcome = "error"` because in a failure case the polarity *is* the point. An explicit
   value that contradicts the directory is an authoring error the runner rejects.
4. **Retire the L0–L3 ladder vocabulary** in favour of three named kinds — **exit**,
   **model**, **render** — and drop the unused `tags` field. Expected-file names lose the
   `.reduced` infix (`drc.reduced.json` → `drc.json`): every expected file is a recorded
   normalized answer, so marking some of them "reduced" is noise.

The result is the owner's target shape — a typical case is `concept` + `doc` + `input` +
one three-line `[[check]]`.

**Rationale.** Every removed field is one fewer thing to explain, get wrong, or drift out
of sync with the verb. Defaults derived from the directory keep the "listing is the
coverage map" property doing real work instead of being decoration. Plain naming serves
goal #1 (documentation) and goal #3 (AI-agent readability) directly: a reader who has
never seen this repo can read a case without a glossary.

**Consequences.** A mechanical migration of every `case.toml` and of `runner/manifest.py`,
`engine.py`, `cli.py`; `case.golden_dir()` → `case.expected_dir()`; `.gitattributes` marks
`expected/**` as LF. Older decision entries keep their original wording (append-only) with
a rename note at the top. Cross-references in [DL-0004]/[DL-0014]/[DL-0016] to
`golden/**` should be read as `expected/**`.

---

## DL-0024 — Delete the byte-comparison layer; accept and document the gerber/drill gap
**Status:** accepted 2026-08-02 — **PARTIALLY SUPERSEDED by [DL-0026]** (2026-08-03).
Superseded [DL-0015]; voids the safety net [DL-0020] relied on; removed the L1 rung of
[DL-0019].

> **What still stands:** the deletion of the re-serialized-bytes comparison
> (`… upgrade --force` → canonical `.kicad_pcb`/`.kicad_sch`), the `upgrade` and `bom`
> verbs, and the general-purpose `golden-file`/`golden-dir` modes.
>
> **What is reversed:** the deletion of **gerber and drill** byte answers, and with it the
> "gerber and drill coverage is zero" gap this entry created. [DL-0026] restores them on
> **every board case**, using KiCad's own layer set, with five evidence-verified
> normalizers. The narrow directory-tree comparator comes back with them. The consequences
> paragraph below — "there is now nothing checking KiCad's fabrication output" — is **no
> longer true**; it was true for one day.

**Context.** The suite compared KiCad's re-serialized *bytes* in three places: the
canonical `.kicad_pcb`/`.kicad_sch` from `… upgrade --force`, the gerber file set, and the
drill file set (plus `pos`/`bom` as text). [DL-0015] had already established what those
comparisons measure — KiCad's exact formatting — and scoped them to
"KiCad-version-regression only, a second implementation is judged on the semantic subset".
That left a whole comparison layer, a `golden-file`/`golden-dir` mode, a directory-tree
comparator, four normalizers and two verbs (`upgrade`, `bom`) in the codebase whose
findings had to be *filtered back out* whenever they fired for a non-KiCad tool.

**Decision.** **Delete the byte layer entirely.** Specifically: the canonical
re-serialize comparison, the gerber `golden-dir` comparison, the drill byte comparison,
the `golden-file`/`golden-dir` modes themselves, the `upgrade` verb (it existed only to
feed them) and the `bom` verb (a BOM is the schematic model's `components` section by
another name). `parse-*` verbs **remain**, as **exit-polarity checks only** — which is
what `failure/` cases need, and on a happy case a passing `model` already proves the file
parsed, so a `parse-*` check beside it is redundant. The `render` (SVG) comparison is
**not** part of this deletion; it compares drawn geometry, not serialization, and stays.

**Rationale.** Prefer deleting dead machinery to keeping a vestigial path. A comparison
whose results must be suppressed for every tool except one is not carrying its weight, and
its existence pushed the design toward per-projection cases and a four-rung ladder that
the owner found fragmenting. Removing it makes the remaining story sayable in one
sentence: *one input, one recorded answer, and the answer is about meaning.*

**Consequences — the honest cost, stated up front.** This removes **all gerber and drill
coverage.** `suites/gerber/` and `suites/drill/` become empty, and they stay in the tree
as a visible reminder rather than being quietly deleted. Since [DL-0020] already ruled out
a portable Gerber-native structural reduction, there is now **nothing** checking KiCad's
fabrication output: a bug that corrupts RS-274X or Excellon output while leaving the
`.kicad_pcb` model intact is caught by no case in this suite. Partial mitigations, named
so nobody overestimates them: the model's `drill_holes` section still catches a dropped or
mis-sized *hole* (but records no hole positions), and a `render` of the copper layers
still shows the drawn geometry (but is not the plot). Two concrete ways back, in
[`VALIDATION.md`](VALIDATION.md) §7 and scheduled as [`ROADMAP.md`](ROADMAP.md) M4: (1)
byte-recorded answers for fab output only, explicitly labelled a KiCad-regression signal
(the header normalizers are already specified in DESIGN §4); or (2) rasterize gerbers to
images and compare pixels with a pinned renderer, which is fair across implementations.
The gap is recorded in `README.md`, `VALIDATION.md` §7, `DESIGN.md` §9 and the roadmap —
four places, deliberately, because a fabrication-facing conformance suite with no
fabrication-output coverage must not be discovered by accident.

---

## DL-0025 — A case's answers follow from the input's file type; `op` and `[[check]]` are deleted
**Status:** accepted (owner decision, 2026-08-03) — supersedes the manifest surface of
[DL-0022] and [DL-0023]; supersedes the `[[check]]` shape of [DL-0003]

**Context.** [DL-0022]/[DL-0023] cut the manifest down to this:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
input   = "board.kicad_pcb"

[[check]]
op       = "model"
expected = "model.json"
```

The owner read it and asked: **"What's `op`? What's `model`?"** That is the whole finding.
Two of the six lines were vocabulary a contributor had to acquire before writing anything,
and neither carried a decision the contributor was actually making. `op` selected from a
13-word verb list; `expected` named a file whose name the runner had just decided.

**Decision.** **Delete `[[check]]`, `op`, `expected`, `outcome` and `args`.** The runner
infers what to record from the **input file's suffix**, and records a fixed set — the
**standard answers** — that is the same for every case of that type:

| Input | Standard answers, in `expected/<version>/` |
|---|---|
| `.kicad_pcb` | `summary.json`, `render-F_Cu.svg`, `gerbers/`, `drill/` |
| `.kicad_sch` | `summary.json`, `render.svg` |
| `.kicad_sym` | `render/` |
| `.pretty` / `.kicad_mod` | `render/` |
| anything in `failure/` | none — exit code and stderr only |

The default case file is therefore, in full:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

**Rationale.** A contributor should learn **nothing** to add a case: drop in a board, write
one sentence, regenerate, read the diff. Every field removed here was a field a case could
get *wrong* — a mismatched `op`/`expected` pair, an `outcome` contradicting its directory,
an `args` that quietly made one case incomparable with its neighbours. A knob that has one
correct setting is not a knob; it is a way to be wrong. This is the same reasoning
[DL-0023] used to delete `compare`, applied until nothing is left to delete.

The fixed set also makes cases **comparable**. Previously two board cases could assert
different things, so "the board suite covers X" required reading every manifest. Now the
suite's coverage is a property of the case *count*.

**Consequences.**
- The verb vocabulary (`model`, `render`, `parse-pcb`, `parse-sch`, `parse-sym`,
  `parse-fp`, `export-gerbers`, `export-drill`, …) disappears from the contributor-facing
  surface entirely. It survives only inside the adapter, where it is an implementation
  detail. `parse-*` in particular is gone as a name: a `failure/` case runs the type's
  loader because that is the only thing you can do with a file that will not load.
- **Cases record more than they strictly need to.** `drc/happy/0001-clean-board` is about
  a DRC result and now also carries a summary, a render, gerbers and a drill file. This is
  accepted deliberately: the marginal cost is ~0.4 s and a few kB per answer, and the
  marginal benefit is that a regression anywhere in the board pipeline is caught by every
  board fixture in the repo rather than by the two that happened to opt in.
- **There is no per-case opt-out.** `schematic-parse/happy/0001-empty-root-sheet` gets a
  render of an empty sheet, which the previous revision had deliberately dropped as
  asserting nothing. It is now recorded again. That is the price of "no knobs", and it is
  the right price: one 700-byte SVG is cheaper than a field that lets every future case
  argue about what to skip.
- A case that genuinely needs an extra answer uses `extra` ([DL-0027]) — one line, one
  list, no table.

---

## DL-0026 — Gerbers and drill return as byte answers on every board, using KiCad's own layer set
**Status:** accepted (owner decision, 2026-08-03) — **partially supersedes [DL-0024]**;
**partially reinstates [DL-0015]**; closes [`ROADMAP.md`](ROADMAP.md) M4 by its option 1

**Context.** [DL-0024] deleted the byte-comparison layer wholesale, which took gerber and
drill coverage to **zero** and left a fabrication-facing conformance suite with nothing
checking fabrication output. That consequence was documented in four places rather than
fixed. The owner's instruction: *"Add gerbers and drill back, byte answers. Add them for
all boards."*

**Decision.** Every board case records, as standard answers:

- **`gerbers/`** — everything `kicad-cli pcb export gerbers -o <dir>` writes, compared as a
  directory tree: same filenames, every file byte-identical after normalization.
- **`drill/`** — everything `kicad-cli pcb export drill -o <dir>/` writes (one `.drl`),
  same comparison.

**No `--layers` is passed.** KiCad plots the layer set stored in the board, falling back to
its built-in default when the board has none. Verified: the populated fixture carries
`(pcbplotparams (layerselection 0x…_55555555_5755f5ff))` and plots **6 gerbers + a job
file**; the minimal fixture carries no `pcbplotparams` block and plots **20 gerbers + a job
file**. Each is stable run-to-run.

**Rationale for taking KiCad's set rather than pinning one.**
1. It is **what the fab receives**. A pinned `--layers F.Cu,B.Cu,Edge.Cuts` would compare
   an artifact nobody ships.
2. It removes a knob, which is the whole direction of [DL-0025]. A per-case layer list is
   a per-case argument.
3. It makes the layer *selection* itself part of the recorded answer. If a KiCad release
   changes which layers are plotted by default, the file list changes and the case goes
   red — a regression that a pinned list would hide by construction.
4. Varying per board is not a problem, because the answer is per board.

**The normalizers, re-derived from the binary** (the previous spec's list was inherited
from memory and was wrong in four places). Method: export twice, two seconds apart, in the
same container; diff; normalize exactly what moved. Five normalizers:

| # | File | Line |
|---|---|---|
| G1 | every gerber | `%TF.CreationDate,<ts>*%` |
| G2 | every gerber | `G04 Created by KiCad (PCBNEW <ver>) date <ts>*` |
| G3 | `.gbrjob` | JSON key `Header.CreationDate` |
| D1 | `.drl` | `; DRILL file KiCad <ver> date <ts>` |
| D2 | `.drl` | `; #@! TF.CreationDate,<ts>` |

And four normalizers the old spec called for that are **not written**:
`TF.GenerationSoftware` (gerber), `Header.GenerationSoftware` (`.gbrjob`) and
`TF.GenerationSoftware` (Excellon) are all **stable across runs** — they are version
strings, and leaving them intact makes every fab answer assert for free that it was
produced by the pinned KiCad; the **drill report's "Created on"** line has no input at all,
because the report requires `--generate-report` and the standard answers do not pass it.
Evidence for each in [`VALIDATION.md`](VALIDATION.md) §7.3. This is DESIGN §4a applied: a
normalizer must be shown load-bearing against the real binary, and four of the eight
inherited ones could not be.

**Why this is not a repeat of the mistake [DL-0024] corrected.** [DL-0024] was right that a
comparison whose findings must be suppressed for every tool but one is not carrying its
weight — *when a better comparison already covers the same ground*. That was true of the
re-serialized `.kicad_pcb` bytes: the summary compares the same file's meaning, exactly,
and fairly. It is **not** true of fab output. There is no semantic comparator for RS-274X
([DL-0020] ruled a structural reduction out as a second plotter's worth of engineering),
so a byte answer here duplicates nothing — it is the only thing in the suite that looks at
what a fab actually gets. The scoping from [DL-0015] therefore applies again, narrowly:
**in ecosystem mode `gerbers/` and `drill/` report `INFO`, never `FAIL`.** The
cross-implementation answer remains rasterize-and-compare ([DL-0021]), now an upgrade path
rather than a rescue.

**Consequences.**
- **Real new coverage, named:** track and pad *geometry* (the summary only saw tracks
  through `min_track_width`), hole *positions* (the summary's hole table has no
  coordinates), the default layer selection, and every plotter-side change between KiCad
  patch releases.
- A narrow **directory-tree comparator** returns to `runner/engine.py`, along with the
  gerber/Excellon normalizers, both of which [DL-0024] deleted. They come back scoped to
  two answer names, not as a general mode.
- **Repo cost:** 12 317 bytes for the 21-file set, 5 573 for the 7-file set. **Runtime
  cost:** +2 invocations ≈ +0.75 s per board case ([`VALIDATION.md`](VALIDATION.md) §9.4).
- **Input filenames become load-bearing.** Gerber filenames and the `%TF.ProjectId` line
  (whose GUID is the input filename's bytes) both embed the input's stem, verified. The
  runner must copy inputs to scratch under their original names, and case authors name
  board inputs `board.kicad_pcb`. Normalizing the project id instead was rejected: it
  would discard a real assertion to buy a freedom nobody needs.
- `suites/gerber/` and `suites/drill/` stop being "the documented empty gap" and become
  ordinary suites for cases specifically *about* fab output (an unusual aperture, an oval
  hole). Routine coverage is now every board case's job.
- The gap text in `README.md`, `VALIDATION.md` §7, `DESIGN.md` §9 and `ROADMAP.md` M4 is
  deleted, because it is no longer true.

---

## DL-0027 — Extras are a flat list of names; a failure case is four keys
**Status:** accepted (owner decision, 2026-08-03) — completes [DL-0025]

**Context.** Deleting `[[check]]` removes the place where the three surviving needs used to
live: opt-in projections (`drc`, `pos`, `ipcd356`, `stats`, `netlist`), the schematic
cross-format check (the same summary rebuilt from `kicadxml`), and failure-case assertions
(`outcome`, `error_contains`, `control`). All three must stay expressible, at the smallest
possible cost to the zero-boilerplate common case.

**Decision — extras.** One optional key, a flat list of strings:

```toml
extra = ["drc"]
```

Each name adds exactly one invocation, and **the name is the answer's filename**: `drc` →
`drc.json`, `pos` → `pos.json`, and so on. One entry, `summary-kicadxml`, adds no file — it
rebuilds `summary.json` from KiCad's XML netlist and compares it to the **same**
`summary.json`, which is the cross-format-fairness proof. Full table in
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §6.

**Decision — failure cases.** No `[[check]]`, no `outcome`, no `op`. The `failure/`
directory states the polarity; the type suffix states the loader. What remains is what the
case actually asserts:

```toml
concept = "A board whose (version ...) form is unterminated is rejected with a parse-position error."
input   = "board.kicad_pcb"
control = "control.kicad_pcb"
error_contains = "Expecting"
```

`error_contains` / `error_contains_any`, `control`, `skip_reason`, `min_kicad` and the
`[known_divergence]` table are **unchanged**, so DIV-0001's strict xfail
(`kind = "crash"`, the `Expecting` substring, the control board) is expressible verbatim in
meaning — it loses only the `[[check]]` wrapper and the `op`/`outcome` lines.

**Rationale.**
- A list of strings is the smallest thing that can express "and also this". It has no
  schema to learn, it diffs cleanly, and a typo in it is a runner error naming the valid
  set.
- **`error_contains` was deliberately not renamed.** It is already plain English that reads
  correctly with no prior knowledge, and shortening it (`rejects`, `message`, `says`) would
  trade clarity for two saved characters. The win in this revision is removing four fields,
  not polishing the one that already worked.
- Keeping `outcome` "for explicitness" was rejected: it was stated in exactly the cases
  where the directory already said it, i.e. all of them.

**Consequences.** `case.toml` now has **twelve possible keys and three that a normal case
uses** (`concept`, `doc`, `input`). A failure case uses five. Nothing in the repo needs a
`[[check]]` block, and the parser can reject one with "checks are inferred from the input
type — see TEST_CASE_FORMAT.md §2" rather than silently honouring a stale manifest.

---

## DL-0028 — `model.json` → `summary.json`
**Status:** accepted (owner decision pending ratification, 2026-08-03) — renames the file
introduced by [DL-0022]

**Context.** The owner's question was "What's `op`? What's **`model`**?" [DL-0025] deletes
`op`. `model` survives as the name of the JSON document describing what the tool
understood — and the question is evidence that the name failed. A name that needs a
glossary entry at every point of first use is a name that is doing the glossary's job
badly.

**Decision.** Rename the file and the concept: **`summary.json`**, "the summary".

**Rationale.**
- **A reader guesses right on sight, and the guess is correct.** "Model" has four meanings
  in this domain (a 3D model, a data model, a mental model, a modelled component); the file
  is none of them. "Summary" has one, and it is the right one.
- **It is more accurate, not just plainer.** The document deliberately drops computed
  areas, densities, clearances, bounding boxes and all geometry
  ([`VALIDATION.md`](VALIDATION.md) §4.1). It *is* a summary. "Model" oversold it as
  complete.
- **The cost is two characters**, which is not what "isn't longer" was guarding against —
  the risk was trading a short jargon word for a long one (`semantic-projection.json`).
- The rename is free right now: [DL-0025] and [DL-0026] regenerate every answer file
  anyway.

**Consequences.** `runner/model.py` → `runner/summary.py`; `build_board_model` →
`build_board_summary`. Older entries in this log ([DL-0019], [DL-0022], [DL-0023],
[DL-0024]) say "model" and are **not** rewritten — this log is append-only. Read "model"
in any DL below 0025 as "summary", the same way [DL-0023] left "golden" in place and asked
readers to read "expected". The word "model" is now absent from the contributor-facing
surface entirely, since [DL-0025] deleted its other use as a verb name.
