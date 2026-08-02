# kicad-conformance

An open-source **conformance test suite for KiCad file formats and CLI behavior.**

Each test case is a small, single-concept, human-readable artifact that doubles as
documentation. The corpus is owned by this repo and is **implementation-agnostic**:
KiCad itself (via `kicad-cli`) is the reference oracle, and any other tool — a
clean-room parser, a third-party exporter — can run the *same* suite through a thin
adapter.

> **Status: design phase.** This repository currently contains design documents and a
> directory skeleton only. No runner code and no real test fixtures/goldens exist yet.
> The worked examples in the docs are illustrations, not files on disk. See
> [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Why this exists — the three goals

1. **Documentation.** KiCad's file formats and CLI behavior are under-documented and
   the dev-docs pages [lag the source](https://dev-docs.kicad.org/en/file-formats/).
   Every test case cites the format-doc section it exercises and states one behavior
   in words, so a directory listing reads like a coverage map and each case is a
   worked example.
2. **Ecosystem compatibility.** One corpus, many implementations. The coupling to any
   tool is a **thin subprocess adapter** (a handful of capability verbs, files in,
   exit code + captured output out). The same physical test files can validate KiCad,
   a Rust reimplementation, or a web-based viewer. See the *adapter contract* in
   [`docs/DESIGN.md`](docs/DESIGN.md).
3. **Helping AI agents.** Cases are short, single-concept, and self-describing so an
   agent can read a handful and learn how a format token or a CLI operation actually
   behaves — including the failure modes.

## Primary target

**KiCad 10.0.5.** This is a deliberate choice: KiCad 11 is not released (the `master`
dev line currently reports `10.99` via nightlies), so 10.0.5 is the newest *stable*
oracle. The layout is **version-parametric** — goldens live under a per-version
directory and KiCad 11 slots in as an additional target when it ships. Nothing is
gated on 11. See [DL-0001](docs/DECISIONS.md).

---

## 60-second mental model — how a test case works

A **case** is a directory. It holds a tiny manifest (`case.toml`), one or more input
**fixtures**, and — for cases that compare rich output — a **golden** tree keyed by
KiCad version.

```
suites/board-parse/happy/0002-minimal-two-layer-board/
├── case.toml            # what to run and what to expect
├── board.kicad_pcb      # the input fixture
└── golden/
    └── 10.0.5/
        └── canonical.kicad_pcb   # KiCad's authoritative re-save (normalized)
```

`case.toml` declares one or more **checks**, each naming an adapter **verb**
(`parse-pcb`, `drc`, `export-gerbers`, …) and an **expectation**:

```toml
concept = "A minimal two-layer board parses and canonicalizes cleanly."
doc     = "sexpr-pcb"

[[check]]
op      = "parse-pcb"       # verb the adapter must implement
expect  = "ok"              # exit success
compare = "golden-file"     # normalize output, diff against the golden
golden  = "canonical.kicad_pcb"
```

The **runner** walks `suites/`, and for each check invokes the **adapter** (for KiCad,
a subprocess wrapping `kicad-cli`), applies the **normalization layer** to strip
nondeterminism (timestamps, generator strings, fresh UUIDs, …), then decides pass/fail:

- `expect = "ok"` / `"error"` pins the exit-code polarity (openjd-style), where `"error"`
  means a **graceful** non-zero rejection — a **crash** (termination by signal / exit
  `>128`) is a distinct verdict and **never** a pass, for either polarity
  ([`docs/DESIGN.md`](docs/DESIGN.md) §3a),
- for failure cases, an optional `error_contains` substring is asserted on stderr, plus a
  required **positive control** (removing the injected defect must make the check exit 0 —
  "a test that can't fail is not evidence"),
- for rich output, the normalized result is compared to the golden — **byte-exact after
  normalization** for text (gerbers, drill, upgraded s-expr; a KiCad-regression signal) or
  a **structural reduction** for semantic outputs (DRC/ERC violation sets, netlist net→node
  membership; the cross-adapter conformance signal). Text goldens are stored **LF** and
  regenerated inside the Linux Docker image so they are platform-canonical.

That's the whole model. One input fixture can drive several checks (e.g. one board
feeding both `drc` and `export-gerbers`). Full details in
[`docs/TEST_CASE_FORMAT.md`](docs/TEST_CASE_FORMAT.md).

---

## Quickstart (intended UX — runner not built yet)

The runner will be a small **Python 3.11+ program with no third-party runtime
dependencies** (uses stdlib `tomllib`). See [DL-0002](docs/DECISIONS.md) for the
rationale. The adapter boundary is a language-agnostic subprocess contract, so the
runner choice does not lock in the implementation-under-test.

**Locally, against an installed `kicad-cli` 10.0.5:**

```bash
# Uses the kicad adapter by default; discovers kicad-cli via KICAD_CLI / PATH / install dirs.
python -m runner suites/                        # run everything
python -m runner suites/drc/                     # scope to one suite
python -m runner suites/board-parse/happy/0002-* # scope to one case

# Regenerate goldens after a kicad-cli version bump (review the diff before committing):
python -m runner --regenerate suites/
```

**In Docker (what CI uses), pinned to the exact patch release:**

```bash
docker run --rm -v "$PWD:/work" -w /work \
  -e LC_ALL=C.UTF-8 -e TZ=UTC \
  kicad/kicad:10.0.5 \
  python3 -m runner suites/
```

`LC_ALL=C.UTF-8` and `TZ=UTC` are mandatory — locale and timezone leak into KiCad's
number formatting and file headers.

**Against another implementation (ecosystem mode):**

```bash
python -m runner --adapter ./adapters/mytool.sh suites/board-parse/
```

The adapter is any executable that answers the verb protocol in
[`docs/DESIGN.md`](docs/DESIGN.md). Its output is compared against the **KiCad-authored**
golden — KiCad is authoritative; a divergence is a finding for the *other* tool (or,
occasionally, for the suite), triaged in a checked-in divergence ledger.

---

## Repo map

```
kicad-conformance/
├── README.md                  # you are here
├── docs/
│   ├── DESIGN.md              # architecture: model, adapter contract, comparison, coverage
│   ├── TEST_CASE_FORMAT.md    # the authoring spec (manifest schema, layout, worked examples)
│   ├── DECISIONS.md           # numbered decision log (ADR-style, append-only)
│   └── ROADMAP.md             # milestones
├── suites/                    # the curated, hand-authored corpus (committed)
│   ├── schematic-parse/{happy,failure}/
│   ├── board-parse/{happy,failure}/
│   ├── erc/{happy,failure}/
│   ├── drc/{happy,failure}/
│   ├── gerber/{happy,failure}/
│   ├── drill/{happy,failure}/
│   ├── netlist/{happy,failure}/
│   ├── symbol-lib/{happy,failure}/
│   ├── footprint-lib/{happy,failure}/
│   └── integration/{happy,failure}/   # multi-verb cases only (per-verb suites stay pure)
├── corpus/                    # large real-world projects for coverage sweeps (gitignored)
│   ├── manifest.toml         #   pinned SHA + SPDX per project (committed)
│   └── projects/             #   downloaded, never redistributed (gitignored)
├── runner/                    # the runner + adapters (code lands in M0)
│   └── adapters/
├── tools/                     # coverage build/collect scripts (M6)
│   └── coverage/
└── .github/workflows/         # CI (M0)
```

`suites/` is the curated documentation corpus — small, hand-authored, single-concept.
`corpus/` is a separate, large, gitignored set of real projects used only for the
scheduled line-coverage sweep and broad regression. See [DL-0009](docs/DECISIONS.md).

---

## Contributing a case

1. Pick the suite (operation family) and polarity (`happy`/`failure`). Single-verb cases
   go in their verb suite; **multi-verb cases go in `integration/`** so each verb suite's
   listing stays a true coverage map.
2. Create `suites/<suite>/<happy|failure>/<NNNN-slug>/` with a `case.toml` and the
   smallest possible fixture that demonstrates exactly one concept.
3. Cite the format-doc section in `doc = ` and write a one-line `concept = `.
4. For rich-output checks, generate the golden with `python -m runner --regenerate`
   **inside the `kicad/kicad:10.0.5` Docker image** (so goldens are LF / Linux-canonical),
   inspect the diff, and commit `golden/10.0.5/…`. For `structured` checks the committed
   golden is the canonical reduction, not the raw KiCad report.
5. Run `python -m runner <your case>` and confirm it passes; for a failure case, add a
   positive control and confirm it fails for the *right* reason (assert `error_contains`,
   and prove that removing the defect makes the check exit 0). A crash is never a pass.

The full contributor checklist is in [`docs/TEST_CASE_FORMAT.md`](docs/TEST_CASE_FORMAT.md).

## License

The suite tooling and hand-authored fixtures are open source (license TBD by the
owner). Downloaded `corpus/` projects retain their upstream licenses and are never
redistributed by this repo.
