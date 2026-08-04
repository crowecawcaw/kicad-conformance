# Decision log

ADR-style, lightweight, numbered `DL-NNNN`, **append-only** (supersede, don't rewrite).
Each entry: status · context · decision · rationale · consequences. Statuses:
`accepted`, `proposed` (awaiting owner ratification), `superseded`.

Cross-referenced from [`DESIGN.md`](DESIGN.md), [`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md),
[`ROADMAP.md`](ROADMAP.md).

A handful of these entries used an earlier vocabulary this repo no longer uses (`golden`,
`model.json`, `[[check]]`, `op =`/`compare =`, `outcome`, an `L0`–`L3` comparator ladder).
Where an entry's *substance* is still current policy, it is kept in full with the old
term explained as what it was renamed to (append-only history, not current guidance).
Where an entry's substance was itself retired, it is reduced to a tombstone under
[§ Superseded](#superseded) below instead of carrying its obsolete body forward.

---

## DL-0001 — Primary target KiCad 10.0.5; version-parametric layout
**Status:** accepted (owner decision)

**Context.** KiCad 11 is not released (research 2026-08-02); the `master` dev line
reports `10.99` via nightlies, and 11.0 stable is expected ~Q1 2027. The suite needs a
concrete, stable oracle now.

**Decision.** Target **KiCad 10.0.5** (newest stable patch) as the primary and only
gating oracle. Make the layout version-parametric: recorded answers live under
`expected/<version>/` (this entry originally said `golden/<version>/`; renamed by
[DL-0023]), CI pins `kicad/kicad:10.0.5`, and a non-gating `kicad/kicad:nightly` (10.99)
job tracks the moving 11 target. Nothing is gated on 11.

**Rationale.** 10.0.5 is a stable, reproducible, Docker-pinnable release; the nightly 11
is a moving target unsuitable for gating. Version-parametric answers mean 11 slots in as
`expected/11.0.0/` when its tags publish, with no fixture changes.

**Consequences.** Recorded answers churn on each pinned-`kicad-cli` bump (managed by the
regenerate flow + version subdirs). A second gating version can be added later without
restructuring.

---

## DL-0002 — Runner language: Python 3.11+, standard library only
**Status:** accepted

**Context.** The runner must shell out to `kicad-cli`/Docker, run in CI, and stay easy
for EDA contributors and AI agents to read/extend. Options weighed: Python vs a Rust/Go
single-binary CLI.

**Decision.** A small **Python 3.11+ runner using only the stdlib** (`tomllib`,
`subprocess`, `json`, `pathlib`). Canonical entrypoint `python -m runner`. No third-party
runtime dependency.

**Rationale.** Lowest contributor/agent friction; Python already ships in the
`kicad/kicad` Docker image and on every CI runner; `tomllib` removes the PyYAML
dependency; the runner is I/O-bound on `kicad-cli` so Rust/Go's speed edge is
irrelevant. openjd's reference harness set the Python precedent.

**Consequences.** No single static binary to distribute (acceptable — Python is
omnipresent in this context). The adapter boundary being a subprocess contract
([DL-0007]) means this choice does **not** impose Python on any implementation-under-test.

---

## DL-0003 — Test-case format: per-case directory + tiny TOML manifest (hybrid)
**Status:** accepted — **one clause superseded by the owner's directory-flattening
ruling (2026-08-03):** polarity is no longer directory-encoded (see the correction below);
everything else stands.

**Context.** openjd encodes expectations purely in filenames because every case is the
same operation. KiCad has diverse verbs, multi-file outputs, per-version answers, and a
requirement that one input drive multiple checks — none expressible in a filename.

**Decision.** A case is a **directory** with a small **`case.toml`** manifest, the
fixture(s), and (unless it is a rejection case) an `expected/<version>/` tree. TOML, not
YAML.

**Correction (2026-08-03):** this entry originally also said "suite, polarity, and
concept are still encoded in the directory path + slug." That stood until the owner's
directory-flattening ruling: polarity now comes from whether `case.toml` sets `control`
(`manifest.py`'s `_polarity_from_manifest`), not from a `happy/`/`failure/` path segment.
Suite and concept are still directory/slug-encoded; only polarity moved into the
manifest.

**Rationale.** The manifest is the minimum needed to name an input and (for a rejection
case) its control/assertion; the common case is 3 lines. TOML gives comments, is
whitespace-insensitive, and matches the surrounding ecosystem.

**Consequences.** The manifest schema is the authoring contract
([`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §5), documented independently of the runner
so alternate runners don't drift.

---

## DL-0004 — Expected files are per-KiCad-version, oracle-authored, regenerable
**Status:** accepted — renamed by [DL-0023]: everything below still holds, but the
directory is `expected/<version>/` and the artifact is called an *expected file*, not a
"golden" (this entry's original text used "golden" throughout; read it as "expected").

**Context.** Rich outputs (gerbers, drill, DRC JSON, summaries) need a reference to
compare against. That reference must represent KiCad's actual behavior at a version.

**Decision.** Expected files live at `expected/<kicad-version>/` inside each case, are
**generated by the reference `kicad-cli`** (never hand-written) via
`python -m runner --regenerate`, and are keyed by **oracle version, not adapter**. A
second adapter compares its output against the same KiCad-recorded answer.

**Rationale.** A hand-written expected file encodes a human's belief; a generated one
encodes KiCad's behavior — only the latter is a conformance reference. Per-version
subdirs let multiple KiCad versions coexist.

**Consequences.** Expected files must be regenerated and diff-reviewed on each
pinned-version bump or fixture `(version YYYYMMDD)` bump. The repo carries generated
artifacts under version control (acceptable — they're the reference).

---

## DL-0005 — Normalization: pin the environment + strip enumerated nondeterminism
**Status:** accepted

**Context.** `kicad-cli` output carries build/time/identity noise (timestamps, fresh
UUIDs, gerber/Excellon headers, locale-formatted numbers). Comparisons must ignore noise
without hiding real differences.

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

**Consequences.** A maintained normalizer table that must be revisited on KiCad bumps.
Some fixtures are permanently excluded and counted.

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
advisory infra, decoupled from the gating suite. Lands in [`ROADMAP.md`](ROADMAP.md) M5
(the runner's own cheap CLI-surface coverage proxy that used to sit ahead of this in M0
was cut — a one-line "N/M verbs exercised" summary at 8 cases was not worth 215 lines of
runner code; this scheduled from-source line-coverage effort is unaffected).

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
stable as suites grow. The reference adapter lives at `adapters/kicad.py` (repo root, not
inside the `runner/` package — it is an executable, not a runner internal).

---

## DL-0009 — Two corpora: curated `suites/` (committed) + real-world `corpus/` (gitignored); plus a divergence ledger
**Status:** accepted — **`corpus/` itself has not been created yet** (see the correction
below); the divergence ledger is live and in use.

**Context.** Two different needs: (a) small, hand-authored, single-concept cases for
documentation/agents; (b) thousands of real projects to drive a future coverage sweep and
broad regression. And a place to record where a second adapter diverges from KiCad.

**Decision.** Keep them separate. **`suites/`** is committed, curated, hand-authored.
**`corpus/`** will hold a committed `manifest.toml` (pinned SHA + SPDX + `permissive` flag
per project) and a gitignored `projects/` — downloaded, never redistributed, regenerable,
disk-guardrailed. A checked-in **divergence ledger** ([`DIVERGENCES.md`](DIVERGENCES.md))
triages each known second-adapter failure with a per-entry verdict ("KiCad's answer is
right, fix the tool" vs "suite wrong"), after openjd's `OPENJD_TEST_RESULTS.md`.

**Correction (2026-08-03):** `corpus/` does not exist in the repo yet — it is an M-later
idea, not yet-committed infrastructure. `README.md` used to claim
`corpus/manifest.toml` was committed; it wasn't, and the claim is removed. `corpus/`
costs nothing to `mkdir` when this work actually starts.

**Rationale.** Curated cases must stay small and readable; coverage needs breadth. Mixing
them would bloat the docs corpus and muddy the coverage map. The ledger lets the suite be
stricter than any single tool without hiding regressions.

**Consequences.** Two ingestion paths (author-a-case vs a future `corpus sync` from
manifest). Ledger needs periodic reconciliation so it doesn't rot into a dumping ground.

---

## DL-0010 — CI: gate on `kicad/kicad:10.0.5` Docker; non-gating nightly job
**Status:** accepted

**Context.** CI must run against real KiCad, reproducibly, and also watch the moving 11
target.

**Decision.** Gating job runs in the `kicad/kicad:10.0.5` container (pinned by digest),
`LC_ALL=C.UTF-8`/`TZ=UTC` set, path-filtered to `suites/` + `runner/` + `adapters/`. A
separate **non-gating** `kicad/kicad:nightly` (10.99) job reports drift. Record
`kicad-cli version --format about` in every run.

**Rationale.** Docker pins an exact patch with no apt state; nightly tracking surfaces
KiCad-11 breakage early without blocking PRs. Mirrors openjd's per-implementation,
path-filtered CI.

**Consequences.** Nightly job will go red as 11 evolves; kept non-gating and triaged into
the divergence ledger. Coverage runs on a separate schedule ([DL-0006]).

---

## DL-0011 — Fixture provenance for curated cases: hand-author small, generate large
**Status:** accepted (default ratified; owner may revisit)

**Context.** Curated `suites/` fixtures can be (a) hand-authored minimal s-expr, or (b)
generated by `kicad-cli` from seeds. Malformed rejection-case fixtures essentially must
be hand-authored (to place the exact defect); realistic happy-case boards are tedious to
hand-write.

**Ratified default (2026-08-02).** Curated fixtures are **hand-authored when small** (all
rejection cases and minimal `parse-*` cases, for surgical control of the exact
defect/token under test) and **seed-and-`upgrade`** when larger (realistic happy-case
boards/schematics derived by `kicad-cli … upgrade --force` from a committed minimal text
seed, committing the upgraded form). **Every committed fixture must be reproducible
without the KiCad GUI** — i.e. CLI-reproducible from a committed text seed, with no
GUI-only artifacts. This keeps the whole corpus regenerable and reviewable.

**Rationale.** Failure cases need surgical control a generator can't give; large valid
fixtures are more reliably produced by KiCad itself. The whole corpus stays regenerable
and reviewable this way.

**Consequences.** A documented per-suite provenance convention. The owner may revisit the
split as the corpus grows.

---

## DL-0012 — STEP / 3D export conformance: defer, pending ratification
**Status:** accepted — **deferred** (out of scope for now; owner may revisit)

**Context.** STEP/3D (`export step`, `render`) are attractive but are the **least
deterministic** outputs (OCC ISO-10303 timestamp + author + non-stable
tessellation/entity ordering), need OpenCASCADE, and sometimes a display (`xvfb-run`).

**Ratified (2026-08-02): accepted = deferred.** STEP/3D conformance is **out of scope for
now**. If pursued later, comparison is **geometry-only** (bounding box / mesh) at a
printed-quantum tolerance, never byte-exact, behind an opt-in suite. The `export-step`
verb stays reserved-but-unused in the adapter contract. The owner may revisit.

**Rationale.** 3D adds the most nondeterminism and the heaviest dependency for the least
diffable output; the high-value conformance surfaces (parse, DRC/ERC, gerber, drill,
netlist) don't need it.

**Consequences.** `step`/`render` stay out of the milestones; a later milestone can add an
opt-in `step/` suite with geometric comparison.

---

## DL-0013 — Crash is a distinct verdict and never a pass; every rejection case needs a positive control
**Status:** accepted

**Context.** Empirically (KiCad 10.0.5), a truncated board makes `pcb upgrade` print a
good `Expecting '('` message and then **segfault** (exit 139 native Windows / `SIGSEGV`
Docker Linux). 139 is non-zero, so a naïve "non-zero = rejected" rule silently passes a
rejection case on a **crash** — building the PCB rejection-case corpus on a KiCad bug.
Separately, the schematic loader emits only `Failed to load schematic` for *every*
defect, so stderr cannot prove which defect fired.

**Decision.** (a) The runner classifies each invocation as **`OK` / `REJECT` / `CRASH`**.
`CRASH` = killed by a signal, or exit code `> 128` (128 + signal; on Windows a fatal-
exception status), detected **portably** — never by the literal 139. A `CRASH` is reported
as its own verdict and is **never a pass**, for a happy or a rejection case; a rejection
case is satisfied only by a `REJECT` (a bounded, graceful non-zero exit). (b) **Every
rejection case carries a positive control** (`control` field): a defect-free variant the
runner runs through the same check and requires to reach `OK`. A case whose control does
not flip to `OK` is reported **not-evidence**, never passed.

**Rationale.** Exit *polarity* is reliable; exit *code* and stderr *content* are not
(platform-dependent crash codes; undiscriminating schematic message). The crash verdict
stops a KiCad bug from laundering into "conformant"; the positive control replaces the
stderr-can't-provide "fails for the right reason" guarantee with an executable one.

**Consequences.** Known oracle crashes (the 10.0.5 PCB parse segfault) are filed upstream
and recorded in the divergence ledger; the paired PCB rejection case asserts the real
`Expecting` substring so a future clean rejection conforms without an edit.

---

## DL-0014 — Structured comparisons store a canonical reduction, not the raw report
**Status:** accepted (clarifies [DL-0008]) — renamed by [DL-0023]: the stored canonical
reduction is now called the *expected file* and lives in `expected/<version>/`; the
principle (store the reduction, never the raw report) is unchanged and is why
`summary.json` is what it is.

**Context.** Early wording was incoherent about whether contributors commit the raw
KiCad JSON or a reduction.

**Decision.** For a structured comparison (DRC/ERC, netlist, summary, …) the committed
expected file is the **canonical reduction** produced by `--regenerate` applying the
per-answer reduction to the oracle output — **not** the raw KiCad report. At compare time
the runner reduces the adapter's output the same way and asserts equality.

**Rationale.** Storing the reduction makes the expected file self-describing, diffs
review as semantic changes, and a second adapter is judged on exactly the reduced shape
it must emit. The raw report's formatting/ordering/IDs are noise the reduction already
discards.

**Consequences.** Regenerate writes the reduced form, always — there is no path that
writes the raw report to `expected/`.

---

## DL-0015 — Byte answers are a KiCad-regression signal; cross-adapter conformance is the semantic subset
**Status:** accepted — its scoping rule is **current policy again, narrowed to fabrication
output only** (superseded once by [DL-0024], then reinstated for `gerbers/`/`drill/` by
[DL-0026]; its original application to re-serialized `.kicad_pcb`/`.kicad_sch` bytes stays
retired, because the summary is a strictly better comparator for those).

**Context.** A byte compare pins KiCad's *exact formatting* (token order, whitespace,
aperture numbering, comment style). A clean-room second adapter emits
valid-but-differently-formatted output and would "diverge" on a byte compare for reasons
that are **not bugs**.

**Decision.** Classify comparisons by what they measure. **Structured/semantic compares +
exit polarity + error substrings are the cross-adapter conformance signal.** **Byte
compares are a KiCad self-consistency / version-regression tool.** Today that means:
`gerbers/`/`drill/` are byte-compared on every board case ([DL-0026]), and in ecosystem
mode they report `INFO`, never `FAIL` — a second adapter is judged on the semantic
subset (the `summary` covers connectivity/placement/counts; there is no portable
Gerber-native semantic comparator, [DL-0020]).

**Rationale.** Goal #2 (one corpus, many implementations) is genuinely delivered for the
portable subset (summary, render, exit+control) and is explicitly not claimed for
byte-recorded fab output.

**Consequences.** A second adapter's `gerbers/`/`drill/` divergences are informational,
never gating; the ledger only carries genuine semantic divergences.

---

## DL-0016 — Expected files are Docker-Linux-authored and stored LF; normalize line endings
**Status:** accepted — renamed by [DL-0023]: this entry originally said "golden"
throughout; read it as "expected." The Docker-Linux-authored, stored-LF rule is
unchanged and is current policy.

**Context.** CI compares inside the `kicad/kicad:10.0.5` **Docker (Linux)** image, but a
contributor may develop on native Windows, where `kicad-cli` writes **CRLF** (and can leak
`\` path separators into messages). A Windows-regenerated text file would mismatch a
Linux-CI run on line endings alone.

**Decision.** (a) Normalize **CRLF↔LF** for every text expected file and store **LF** in
the repo. (b) **Committable expected files are regenerated inside the Docker Linux
image** — `--regenerate` is run in the container so the bytes are platform-canonical; a
Windows-native regenerate is fine for local iteration only. (c) `.gitattributes` marks
`suites/**` as LF so git does not re-mangle it on checkout. Linux is the canonical
platform.

**Rationale.** Removes a whole class of false diffs (line endings, path separators) that
would otherwise make every text expected file fail across the Windows-dev / Linux-CI
split.

**Consequences.** Contributors on Windows regenerate via Docker (`scripts/run.sh
--regenerate`) for anything they commit.

---

## DL-0018 — Known-oracle-divergence declaration is a strict-xfail layer, not a skip
**Status:** accepted

**Context.** `board-parse/rejects-unterminated-sexpr` makes `kicad-cli` 10.0.5 print the
correct `Expecting …` message and then segfault (`CRASH`, [DL-0013]) -- a confirmed KiCad
bug, not a harness defect. A `CRASH` is never a pass, so as originally authored this one
case made `python3 -m runner suites/` exit non-zero forever, on a bug that is not this
repo's to fix. A brand-new public repo cannot ship with a permanently-red gating build.
Two options were weighed: (a) silently loosen the case, or (b) declare the divergence as
data and reinterpret the already-expected bad verdict without touching the OK/REJECT/CRASH
classifier itself. Option (b), modeled on OpenJD's and this project's own
divergence-ledger precedent ([DL-0009]), keeps the assertion honest while keeping the
build green.

**Decision.** A case may declare a `known_divergence` table: `reason` (required, one
line), `kind` (required, e.g. `"crash"`), `tracking` (optional, an upstream issue URL/id
or a `"TODO: file upstream"` placeholder). Semantics are **strict xfail**, applied as a
layer on top of the existing OK/REJECT/CRASH verdict — it never changes what the verdict
*is*:

- If the actual verdict matches the declared `kind`, the check is scored **`XFAIL`**
  ("known divergence") -- not a failure; the build stays green.
- If the same check instead comes back clean (the oracle got fixed), that is an
  **`XPASS`** -- and XPASS **fails the build** with a message pointing at
  `docs/DIVERGENCES.md`, because a strict xfail that can silently rot is not evidence of
  anything.
- A case with no `known_divergence` behaves exactly as before this decision; `XFAIL`/
  `XPASS` are separately-counted verdicts alongside `PASS`/`FAIL`/`CRASH`/`SKIP`/
  `NOT-EVIDENCE`/`NEEDS-REGEN` in the summary.
- The positive control ([DL-0013]) is unaffected and still required/run.

**Rationale.** The runner's OK/REJECT/CRASH classifier ([DL-0013]) must stay a single
source of truth for what actually happened -- inventing a fourth raw verdict, or
special-casing the classifier per-case, would blur that. Strictness (XPASS fails loudly)
is what prevents this from degenerating into a permanent, unreviewed skip.

**Consequences.** `manifest.py` parses `known_divergence`; `engine.py` scores it after the
positive control passes; `cli.py` treats `XFAIL` as non-failing and `XPASS` as failing.
`docs/DIVERGENCES.md` is the checked-in ledger this anticipated.

---

## DL-0020 — Gerber geometry is NOT reduced structurally; board copper is covered by stats+pos+ipcd356+SVG
**Status:** accepted

**Context.** A semantic ("structured") comparison for the `gerbers/` answer could be a
structural RS-274X reduction (apertures + flashes/draws with per-layer coordinates).
Whether that is worth building was an open call.

**Decision.** **Do not build a Gerber structural reduction.** Board copper *meaning* is
instead covered by the composition of **stats** (copper areas, track/via/pad counts, min
widths, folded into `summary.json`), **pos** (placement), **ipcd356** (net→pad
connectivity + access-point geometry), and the **render** (the drawn copper geometry).
`gerbers/`/`drill/` remain byte-compared answers, a KiCad-version-regression signal only
([DL-0015], [DL-0026]).

**Rationale.** A faithful RS-274X reducer is a second rasterizer's worth of work (aperture
macros, `%LP` polarity, `G36/G37` regions, arc interpolation, step-and-repeat, `%FS`
coordinate format), and its cross-impl output is as formatting-sensitive as the byte
compare it was meant to improve on. The four projections above already localize every
copper defect that matters (wrong count/net/placement/area).

**Consequences.** **Honest gap:** a bug that corrupts RS-274X output while leaving the
`.kicad_pcb` model intact (a plotter-only aperture bug) is caught only by the byte
compare (KiCad-regression, not cross-impl) and by the render. A Gerber reducer can be
added later if a concrete second-adapter need appears.

---

## DL-0021 — SVG render: hybrid normalized-SVG-exact (KiCad-regr.) + pinned-`resvg` raster (cross-impl); audited tolerance
**Status:** accepted — unchanged in substance. Renamed by [DL-0022]/[DL-0023]: the four
`export-svg-*` verbs are one `render` verb dispatching on the input suffix, and "L3" is
just "render" (an earlier revision numbered comparison kinds L0–L3; that numbering is
retired).

**Context.** The render comparison needs a deterministic SVG comparison in CI.
Empirically the `kicad/kicad:10.0.5` image ships **no SVG rasterizer** (probed:
`rsvg-convert`, `resvg`, `inkscape`, `cairosvg`, ImageMagick, `dvisvgm`, `pdftocairo` all
MISSING). Separately, KiCad's own `export svg` output is deterministic **except** its
`<title>` line (filename + wall-clock date); path geometry is byte-stable run-to-run.

**Decision.** A **hybrid**: **(a) KiCad-vs-KiCad regression** → normalize `<title>`/
`<desc>` and compare the SVG **byte-exact after normalization** (no rasterizer, zero
tolerance) — the comparator in use today; determinism pinned via `--black-and-white
--page-size-mode 2 --exclude-drawing-sheet` + `LC_ALL=C.UTF-8`/`TZ=UTC`. **(b)
Cross-implementation** (arrives with the second adapter, [`ROADMAP.md`](ROADMAP.md) M6) →
rasterize both SVGs with a **pinned `resvg`** at fixed DPI/white-background, and
pixel/SSIM-diff under an **explicit, per-case, documented threshold that must be shown
load-bearing** (perturb the geometry by one quantum → comparator goes red, or the
threshold is dead and removed — same rule as a normalizer, DESIGN §4a).

**Rationale.** KiCad's SVG being already-exact means the regression case needs no
renderer and gets a *stronger, cheaper* exact vector compare with no threshold.
Rasterization is reserved for the genuinely-different case (a clean-room tool's
valid-but-differently-structured SVG, which exact-match would over-fit). `resvg` is
chosen over `rsvg-convert`/Inkscape/cairosvg because it is a single static binary with
bundled fonts and CPU-deterministic rendering.

**Consequences.** The reference (KiCad-vs-KiCad) comparator needs no additional
dependency; pinning `resvg` and the raster/SSIM path land with the second adapter.

---

## DL-0025 — A case's answers follow from the input's file type; `op` and `[[check]]` are deleted
**Status:** accepted (owner decision, 2026-08-03)

**Context.** An earlier manifest revision read:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
input   = "board.kicad_pcb"

[[check]]
op       = "model"
expected = "model.json"
```

The owner read it and asked: **"What's `op`? What's `model`?"** That is the whole finding.
Two of the six lines were vocabulary a contributor had to acquire before writing anything,
and neither carried a decision the contributor was actually making.

**Decision.** **Delete `[[check]]`, `op`, `expected`, `outcome` and `args`.** The runner
infers what to record from the **input file's suffix**, and records a fixed set — the
**standard answers** — that is the same for every case of that type:

| Input | Standard answers, in `expected/<version>/` |
|---|---|
| `.kicad_pcb` | `summary.json`, `render-F_Cu.svg`, `gerbers/`, `drill/` |
| `.kicad_sch` | `summary.json`, `render.svg` |
| `.kicad_sym` | `render/` |
| `.pretty` / `.kicad_mod` | `render/` |
| a rejection case (sets `control`) | none — exit code and stderr only |

**Rationale.** A contributor should learn **nothing** to add a case: drop in a board,
write one sentence, regenerate, read the diff. Every field removed here was a field a
case could get *wrong*.

**Consequences.**
- The verb vocabulary (`model`, `render`, `parse-pcb`, …) disappears from the
  contributor-facing surface entirely; it survives only inside the adapter.
- **Cases record more than they strictly need to.** `drc/clean-board` is about a DRC
  result and also carries a summary, a render, gerbers and a drill file — accepted
  deliberately (DESIGN.md §9).
- **There is no per-case opt-out.**
- A case that genuinely needs an extra answer uses `extra` ([DL-0027]).

---

## DL-0026 — Gerbers and drill return as byte answers on every board, using KiCad's own layer set
**Status:** accepted (owner decision, 2026-08-03) — closes [`ROADMAP.md`](ROADMAP.md) M4
by its option 1

**Context.** An earlier revision deleted the byte-comparison layer wholesale, which took
gerber and drill coverage to **zero**. The owner's instruction: *"Add gerbers and drill
back, byte answers. Add them for all boards."*

**Decision.** Every board case records, as standard answers: **`gerbers/`** (everything
`kicad-cli pcb export gerbers -o <dir>` writes) and **`drill/`** (everything
`kicad-cli pcb export drill -o <dir>/` writes, one `.drl`), each compared as a directory
tree: same filenames, every file byte-identical after normalization.

**No `--layers` is passed.** KiCad plots the layer set stored in the board, falling back
to its built-in default when the board has none. Verified: the populated fixture carries
`(pcbplotparams (layerselection 0x…_55555555_5755f5ff))` and plots **6 gerbers + a job
file**; the minimal fixture carries no `pcbplotparams` block and plots **20 gerbers + a
job file**. Each is stable run-to-run.

**Rationale for taking KiCad's set rather than pinning one.** (1) It is **what the fab
receives**. (2) It removes a knob — a per-case layer list is a per-case argument. (3) It
makes the layer *selection itself* part of the recorded answer — a KiCad release that
changes the default set makes the case go red, which a pinned list would hide.

**The normalizers, re-derived from the binary.** Method: export twice, two seconds apart,
in the same container; diff; normalize exactly what moved. Five normalizers:

| # | File | Line |
|---|---|---|
| G1 | every gerber | `%TF.CreationDate,<ts>*%` |
| G2 | every gerber | `G04 Created by KiCad (PCBNEW <ver>) date <ts>*` |
| G3 | `.gbrjob` | JSON key `Header.CreationDate` |
| D1 | `.drl` | `; DRILL file KiCad <ver> date <ts>` |
| D2 | `.drl` | `; #@! TF.CreationDate,<ts>` |

`TF.GenerationSoftware` (gerber), `Header.GenerationSoftware` (`.gbrjob`) and
`TF.GenerationSoftware` (Excellon) are all **stable across runs** and are deliberately
**not** normalized — leaving them intact makes every fab answer assert, for free, that it
was produced by the pinned KiCad. The drill report's "Created on" line has no input at
all (the standard answers don't pass `--generate-report`).

**Why this is not a repeat of the byte-layer deletion's mistake.** That deletion was
right that a comparison whose findings must be suppressed for every tool but one is not
carrying its weight *when a better comparison already covers the same ground* — true of
re-serialized `.kicad_pcb` bytes (the summary compares the same file's meaning, exactly
and fairly). It is **not** true of fab output: there is no semantic comparator for
RS-274X ([DL-0020]), so a byte answer here duplicates nothing. The scoping from
[DL-0015] applies again, narrowly: **in ecosystem mode `gerbers/` and `drill/` report
`INFO`, never `FAIL`.**

**Consequences.**
- Real new coverage: track/pad geometry, hole positions, the default layer selection, and
  every plotter-side change between KiCad patch releases.
- A directory-tree comparator in `runner/engine.py`, plus the gerber/Excellon normalizers
  in `runner/normalize.py`.
- **Input filenames become load-bearing.** Gerber filenames and the `%TF.ProjectId` line
  (whose GUID is the input filename's bytes) both embed the input's stem, verified:
  `board.kicad_pcb` → `board-F_Cu.gtl` / `%TF.ProjectId,board,626f6172-…`; the same board
  as `renamed.kicad_pcb` → `renamed-F_Cu.gtl` / `%TF.ProjectId,renamed,72656e61-…`. The
  runner copies inputs to scratch under their original names; case authors name board
  inputs `board.kicad_pcb`.

---

## DL-0027 — Extras are a flat list of names; a rejection case is four keys
**Status:** accepted (owner decision, 2026-08-03) — completes [DL-0025]

**Context.** Deleting `[[check]]` removes the place where the three surviving needs used
to live: opt-in projections (`drc`, `pos`, `ipcd356`, `stats`, `netlist`), the schematic
cross-format check, and rejection-case assertions (`error_contains`, `control`). All three
must stay expressible, at the smallest possible cost to the zero-boilerplate common case.

**Decision — extras.** One optional key, a flat list of strings:

```toml
extra = ["drc"]
```

Each name adds exactly one invocation, and **the name is the answer's filename**: `drc` →
`drc.json`, `pos` → `pos.json`, and so on. One entry, `summary-kicadxml`, adds no file — it
rebuilds `summary.json` from KiCad's XML netlist and compares it to the **same**
`summary.json`, the cross-format-fairness proof. Full table in
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §6.

**Decision — rejection cases.** No `[[check]]`, no `outcome`, no `op`. What remains is
what the case actually asserts:

```toml
concept = "A board whose (version ...) form is unterminated is rejected with a parse-position error."
input   = "board.kicad_pcb"
control = "control.kicad_pcb"
error_contains = "Expecting"
```

`error_contains` / `error_contains_any`, `control`, `skip_reason` and the
`[known_divergence]` table are **unchanged**.

**Rationale.** A list of strings is the smallest thing that can express "and also this."
`error_contains` was deliberately not renamed — it is already plain English.

**Consequences.** `case.toml` has eleven possible keys and three that a normal case uses
(`concept`, `doc`, `input`). A rejection case uses four (adds `control`,
`error_contains`/`error_contains_any`). Nothing in the repo needs a `[[check]]` block.

---

## DL-0028 — `model.json` → `summary.json`
**Status:** accepted (owner decision, 2026-08-03) — renames the file introduced when the
composite answer was first added

**Context.** The owner's question was "What's `op`? What's **`model`**?" [DL-0025]
deletes `op`. `model` survives as the name of the JSON document describing what the tool
understood — and the question is evidence that the name failed.

**Decision.** Rename the file and the concept: **`summary.json`**, "the summary."

**Rationale.** A reader guesses right on sight: "model" has four meanings in this domain;
the file is none of them. The document deliberately drops computed areas, densities,
clearances and all geometry — it *is* a summary, not a complete model.

**Consequences.** `runner/summary.py`'s `build_board_summary`/`build_schematic_summary`.
The word "model" is now absent from the contributor-facing surface entirely.

---

## DL-0029 — `parse-pcb`'s probe moves from `pcb upgrade --force` to `pcb export stats`
**Status:** accepted (owner decision, 2026-08-03)

**Context.** `parse-pcb` (the loader every `board`-kind rejection case runs, DESIGN.md
§2) was implemented as `pcb upgrade --force` on a scratch copy. Two independent scale-out
agents each verified, on every malformed board they tried (8/8 and 10/10), that
`kicad-cli pcb upgrade --force` **SIGSEGVs** immediately after printing the correct
`Failed to load board: …` message. A crash is never a pass ([DL-0013]), so every
`rejects-*` board case in `suites/board-parse/` scored a strict `known_divergence` xfail
instead of the genuine reject-and-PASS its `concept` describes — eleven cases (as of this
scale-out), all blocked on the identical bug in the identical command, not eleven
independent findings.

Both agents also verified that **every other board-consuming subcommand** rejects the
same malformed bytes gracefully. `pcb export stats --format json` was checked directly
against all eleven `rejects-*` fixtures (plus their `control` siblings): exit `3`, the
same `Failed to load board: …` message on stderr, on every malformed one; a clean `0` and
a written `stats.json` on every control. It is a strictly better loader probe: same
exit-polarity-only contract (the JSON is discarded either way), same failure message, no
crash.

**Decision.** `parse-pcb` (`adapters/kicad.py`'s `cmd_parse_pcb`) now runs `pcb export
stats --format json -o <scratch>/stats.json <scratch-copy>` instead of `pcb upgrade
--force`. The old command is kept alive as its own verb, `parse-pcb-upgrade`
(`cmd_parse_pcb_upgrade`), reachable only via a case's `known_divergence.probe` override
(`runner/manifest.py`, `runner/engine.py`'s `_run_failure_case`) — a narrow, one-case
escape hatch, not a reintroduction of the per-case verb knob [DL-0025]/[DL-0027] deleted.

**Consequence for the eleven `rejects-*` board cases.** Ten now genuinely PASS: their
`[known_divergence]` tables are removed, since `pcb export stats` rejects them
gracefully and their existing `error_contains` substrings still match verbatim (the
message text is produced by the same board-loading code both commands share; only the
wrapping command differs). The eleventh, `rejects-unterminated-sexpr`
([DIV-0001](DIVERGENCES.md)), is deliberately preserved as the one case that still
documents the `pcb upgrade --force` segfault: it sets `known_divergence.probe =
"parse-pcb-upgrade"` so it keeps invoking the crashing command on purpose (verified: `pcb
export stats` rejects this exact fixture gracefully too, exit 3 — without the override
this case would silently stop testing the crash at all, not merely start passing).

**Rationale.** `export stats` is not picked because it's convenient — every board-facing
`kicad-cli` subcommand shares the same board-loading front end, so any one of them that
doesn't crash would do; `stats` is chosen because it is already a verb this adapter
implements for an unrelated reason (the `stats` extra / `summary`'s board composition),
so no new kicad-cli surface is introduced. Keeping `upgrade --force` reachable (rather
than deleting it outright) is what lets DIV-0001 keep meaning what it always meant — "the
upgrade path segfaults" — instead of being quietly retired the moment nothing exercises
it.

**Consequences.** `adapters/kicad.py` (`cmd_parse_pcb`, `cmd_parse_pcb_upgrade`,
`IMPLEMENTED_VERBS`), `runner/manifest.py` (`KnownDivergence.probe`),
`runner/engine.py` (`LOADER_VERB`'s docstring, `_run_failure_case`'s verb selection),
`docs/DESIGN.md` §2's verb table, `docs/TEST_CASE_FORMAT.md` §8, `docs/DIVERGENCES.md`'s
DIV-0001 entry, and the eleven `suites/board-parse/rejects-*` manifests.

---

## DL-0030 — Asserted coverage: a case's falsifiability is a committed perturbation, checked by the runner
**Status:** accepted (owner principle, 2026-08-03) — design in
[`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md); **not yet implemented**

**Context.** The owner's measurement discipline, in his words: *"Aim to cover all
meaningful lines and branches. Coverage is what we can assert and verify, not just run."*

Nothing enforces it. [`COVERAGE.md`](COVERAGE.md) §6.1 concedes the gap outright —
"covered ≠ tested — gcov proves a line ran, not that anything was asserted about it" — so
every percentage in that document is an upper bound on something never measured. Worse,
the one place falsifiability *is* established is
[`TEST_CASE_FORMAT.md`](TEST_CASE_FORMAT.md) §11's manual step, *"Broke the input and
watched it go red"*: performed once by the author, recorded nowhere, re-checked never. A
case can rot into one that passes whatever KiCad does — a normalizer widens, a comparator
loses a field, an answer is regenerated from an already-wrong run — and the suite stays
green through all of it. A green suite of inert cases is the failure mode this project
cares most about ([COVERAGE.md](COVERAGE.md) §7).

Note that **rejection cases have never had this problem**: [DL-0013]'s positive control is
exactly a falsifiability check, run every time, and a rejection case whose control does not
flip is reported not-evidence rather than passed. Happy cases had no equivalent.

**Decision.** Define **asserted** operationally, and make the definition executable by
reusing the machinery that already exists:

> A perturbation `P` of case `C`'s input is **asserted** iff running `C` with `P`
> substituted for its input, against `C`'s own committed answers, **fails**.

A perturbation is a copy of the case's input with something changed, committed at
`suites/<suite>/<slug>/perturb/<perturbation-slug>/`, overlaying the case's inputs by
filename. **`case.toml` gains no key**: the common case stays `concept` + `doc` + `input`,
and the perturbation's slug plus `diff input perturb/<slug>/input` is its complete
description. The runner grows one alternate mode, `--verify-assertions`, structurally a
sibling of `--determinism-check`, scoring each perturbation `ASSERTED` / `INERT` /
`INVALID-PERTURBATION` / `CRASH`, and counting happy cases that carry none.

**Rationale.**
- **It formalizes an existing rule rather than inventing one.** The mechanism *is* the
  contributor checklist's manual step, moved out of a human's memory into the repo, and it
  extends [DL-0013]'s positive-control principle from rejection cases to every case.
- **It needs no new comparator, answer format or verdict semantics.** "The case goes red"
  is what `python -m runner` already computes. That is why the syntax can be a directory
  and no manifest key.
- **Mutation on the fixture side is the only affordable side.** Source-level mutation of
  KiCad is the gold standard and costs ~25 min of rebuild per mutant against a 500k-line
  tree; it is kept as a targeted tool for settling a disputed subsystem, not a routine
  measurement ([`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md) §3.5).
- **Mutating the *answer* instead was considered and rejected**: it tests the comparator,
  which is a global property, and would pass for every case in the corpus whether or not
  any KiCad behaviour is observed.

**Three guards that make the check mean what it says.** (1) A perturbation of a happy case
must still **load** — otherwise "break the file" trivially moves the answer and asserts
nothing. (2) An overlay filename that matches no declared input is an error, not a silent
no-op. (3) A rejection case must not carry a `perturb/` directory; its `control` already
is one.

**When it runs.** Gating in CI on the paths `ci.yml` already filters on, **not** in the
default `python -m runner suites/`. The default run is the developer inner loop and the
ecosystem-mode entry point, and a second implementation should not be made to pay for the
suite's own self-checks. Not scheduled either: the defect is introduced by the commit that
touches `suites/`, so catching it there names the author and the change instead of handing
a stranger a bisect. Measured cost on this workstation — 12 board cases run in 1 min 26 s
(~12 s fixed, ~6.2 s marginal per board case), and a perturbation short-circuits at the
first differing answer, so the common one costs about half a case. The gating job already
runs the suite twice (normal + `--determinism-check`); this adds roughly half a pass.

**Consequences.**
- New: `runner/assertions.py`, a `--verify-assertions` flag in `runner/cli.py`, perturbation
  discovery in `runner/manifest.py`, and a first-difference short-circuit in the answer
  generation path of `runner/engine.py`.
- `.github/workflows/ci.yml` gains one step in the gating job.
- `docs/TEST_CASE_FORMAT.md` §11 and `README.md`'s "Contributing a case" step 6 change from
  "break the input and watch it go red" to "commit the perturbation that proves it."
- **Adoption is ratcheted, not retroactive.** All 77 existing cases have no perturbation;
  `UNASSERTED-CASE` is counted and printed, never failed, and CI gates only on the count
  not increasing. `INERT` and `INVALID-PERTURBATION` are hard failures from day one — they
  can only appear if someone wrote a perturbation, and a wrong perturbation is worse than
  none.
- `perturb/` fixtures obey every existing fixture rule ([DL-0011], [DL-0016]) and **must
  keep the input's filename** — gerber output embeds it ([DL-0026]), so a renamed
  perturbation would "move the answer" for the wrong reason.

---

## DL-0031 — The asserted-coverage gap report is Tier 2: scheduled, gcov-attributed, and a document not a gate
**Status:** accepted (2026-08-03) — design in
[`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md) §4; **not yet implemented**

**Context.** [DL-0030] makes each case falsifiable and re-checks it, but it answers a
per-case question. The owner's question is a per-*line* one: **which KiCad code does the
suite execute while asserting nothing about it?** Answering that needs the gcov data
[`COVERAGE.md`](COVERAGE.md) already produces, joined to [DL-0030]'s per-perturbation
results.

**Decision.** Split the mechanism in two tiers and keep them on different schedules.

**Tier 1** is [DL-0030]: release-image, no gcov, gating per push.

**Tier 2** is the attribution and the report: run each case *and each of its perturbations*
under the instrumented image with per-run counter isolation
(`GCOV_PREFIX`/`GCOV_PREFIX_STRIP`, the one genuinely new piece of tooling), then

```
credited[C,P] = { L : base[C][L] > 0  and  base[C][L] != pert[C,P][L] }   if P moved an answer
asserted      = union of credited[C,P];      GAP = executed \ asserted
```

Emitting `asserted.json`, `asserted-credit.json` and `asserted-gap.md` next to the existing
`focus.json`. **It runs on the existing scheduled coverage job, never per-PR**, and its
output is a document with the same status COVERAGE.md has today.

**Rationale.**
- [DL-0006] already decided that from-source instrumented coverage is scheduled
  infrastructure, not a per-PR check. That decision applies here unchanged; Tier 2 is the
  same build, the same job, more analysis.
- **Requiring `base[C][L] > 0`** keeps `asserted ⊆ executed` true by construction, so the
  report can never claim more assertion than coverage. A line that runs only under the
  perturbation is not part of what the recorded answers cover.
- **Count inequality rather than a 0↔n transition** keeps the signal in loop-heavy parser
  and plotter code, which is most of what we care about.
- **`asserted_semantic` is tracked separately from `asserted`** because a line asserted
  only by a `gerbers/`/`drill/` byte answer is not asserted for any implementation but
  KiCad ([DL-0015], [DL-0026]). The suite's cross-implementation claim rests on the
  semantic number, so the report must not blend them.
- **Credit fan-out is published.** A coarse perturbation (shift the board 1 mm) credits
  thousands of lines on one moved answer. `asserted-credit.json` records `|credited[C,P]|`
  per perturbation and the report flags anything above the corpus p90 `low-specificity` —
  no automatic penalty, just visibility, pushing authors toward surgical perturbations that
  are also better documentation.

**Consequences.**
- `tools/coverage/run-suite.sh` gains a `--per-case` bucketing mode; the pooled mode stays,
  because COVERAGE.md's published numbers depend on it.
- New `tools/coverage/asserted.py`.
- [`COVERAGE.md`](COVERAGE.md) §3's table gains two columns **only once real numbers exist**
  — an empty column reads as zero.
- The report produces a second, cheaper class of gap than COVERAGE.md §5's: §5 says "write
  a case that reaches this code"; this says "a case already reaches this code and nothing
  would notice if it changed," which is usually one perturbation in a case that exists.
- The honest limits are listed in [`ASSERTED_COVERAGE.md`](ASSERTED_COVERAGE.md) §6 and must
  travel with any number quoted from this report — in particular that credit is an **upper
  bound** (a line can be credited coincidentally), that Tier 2's attribution comes from the
  Debug instrumented binary whose behaviour is known to differ ([COVERAGE.md](COVERAGE.md)
  §2/§6.4), and that none of this measures whether the recorded answer is *right*.

---

## Superseded

Entries below are retired: their mechanism no longer exists in the code, and their
surviving substance (if any) is restated in a live entry or in [`DESIGN.md`](DESIGN.md).
Full original text: git history (this file, pre-2026-08-03 structure cleanup).

- **DL-0008** — comparison model: exit / structured / golden-file modes, plus a
  `compare` field selecting one. The `golden-file`/`golden-dir` mode and the `compare`
  field are deleted (DL-0023, DL-0024). The printed-quantum-tolerance principle and the
  exit/structured/render/bytes comparison kinds it introduced live on as current policy
  — see [`DESIGN.md`](DESIGN.md) §3.
- **DL-0017** — multi-verb cases live in a dedicated `integration/` suite. Superseded by
  DL-0022: the composite answer made a multi-verb case unnecessary, and `integration/`
  was retired.
- **DL-0019** — a four-rung L0–L3 comparator ladder, with per-projection `case.toml`
  examples this entry's own text called "obsolete" even before this cleanup. Superseded
  by DL-0022 (composite answer), DL-0024 (byte-layer deletion), DL-0025 (manifest
  surface); the ladder numbering is retired in favor of four named comparison kinds
  — see [`DESIGN.md`](DESIGN.md) §3.
- **DL-0022** — one composite `model` answer per case is the default. Its manifest
  surface (`op = "model"`, `[[check]]`) is superseded by DL-0025; its filename by
  DL-0028. The principle — one input, one composite answer, projections opt-in — is
  unchanged and is restated by DL-0025/DL-0028 in the current vocabulary.
- **DL-0023** — `golden` → `expected`; drop `compare`; `expect` → `outcome` with a
  default. The rename half is carried forward by DL-0004/DL-0014/DL-0016 (kept live,
  above); the `op`/`outcome`/`expected`-field half is itself superseded by DL-0025,
  which deletes those fields outright rather than defaulting them.
- **DL-0024** — delete the byte-comparison layer; accept the gerber/drill coverage gap.
  The re-serialized-`.kicad_pcb`/`.kicad_sch`-bytes deletion and the `upgrade`/`bom` verb
  deletion still stand. The gerber/drill half is reversed by DL-0026, which also explains
  why restoring that half is not a repeat of this entry's mistake.
