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
**Status:** accepted

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
**Status:** accepted

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
**Status:** accepted (clarifies [DL-0008])

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
**Status:** accepted (scopes goal #2, refines [DL-0008]/[DL-0009])

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
**Status:** accepted

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
**Status:** accepted

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
**Status:** accepted (extends [DL-0008]/[DL-0014]/[DL-0015]; full spec in [`VALIDATION.md`](VALIDATION.md))

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
**Status:** accepted (scopes [DL-0019]; refines [DL-0015])

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
**Status:** accepted (implements the L3 rung of [DL-0019])

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
