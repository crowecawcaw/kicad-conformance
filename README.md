# kicad-conformance

An open-source **conformance test suite for KiCad file formats and CLI behavior.**

Each test case is a small, single-concept, human-readable artifact that doubles as
documentation. The corpus is owned by this repo and is **implementation-agnostic**:
KiCad itself (via `kicad-cli`) is the reference oracle, and any other tool — a
clean-room parser, a third-party exporter — can run the *same* suite through a thin
adapter.

> **Status: M0 complete; M0.5 (the standard-answers rework) in progress.** The runner (`runner/`,
> Python 3.11 stdlib), the reference `kicad-cli` adapter, the OK/REJECT/CRASH verdict +
> positive-control machinery, the known-oracle-divergence strict-xfail layer, the cheap
> coverage proxy, and worked examples in `board-parse`, `schematic-parse` and `drc` are
> real, committed, and green against `kicad-cli` 10.0.5 in Docker — see
> `python3 -m runner suites/` and `docs/ROADMAP.md`. The docs currently describe the
> **revised** design ([DL-0025]–[DL-0028](docs/DECISIONS.md): a fixed set of answers chosen
> by the input's file type, no `op`/`[[check]]` in the manifest, gerbers and drill recorded
> as bytes on every board, `model.json` renamed `summary.json`); the runner is being
> migrated to match — see `docs/ROADMAP.md` M0.5 for the exact per-case migration. One case
> (`board-parse/failure/0001-unterminated-sexpr`) reports `XFAIL` (known divergence): it
> documents a real KiCad 10.0.5 segfault (DL-0013) as a tracked, checked-in ledger entry
> (`docs/DIVERGENCES.md`, DL-0018) rather than a harness bug or a permanently-red build.
> Everything past that (deeper parse coverage, ERC, more DRC rule classes, library
> suites, fab-output coverage, a second adapter) is still ahead — see
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
oracle. The layout is **version-parametric** — recorded answers live under a per-version
directory and KiCad 11 slots in as an additional target when it ships. Nothing is
gated on 11. See [DL-0001](docs/DECISIONS.md).

---

## 60-second mental model — how a test case works

**One input file in, the answers KiCad gave for it out.** A case is a directory holding a
tiny manifest, the input, and the answers:

```
suites/board-parse/happy/0002-populated-board/
├── case.toml               # one sentence and a filename
├── board.kicad_pcb         # the input
└── expected/
    └── 10.0.5/             # the answers, as KiCad 10.0.5 gave them
        ├── summary.json    #   what KiCad understood
        ├── render-F_Cu.svg #   what the front copper layer draws
        ├── gerbers/        #   what a fab would receive
        └── drill/          #   ditto, the holes
```

`case.toml`, in full:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

**You do not say what to check.** The runner works that out from the input's file type: a
`.kicad_pcb` always yields those four answers, a `.kicad_sch` yields a summary and a
render, a symbol or footprint library yields its drawings. Adding a case means dropping in
a board, writing one sentence, regenerating, and reading the diff. There is no vocabulary
to learn ([DL-0025](docs/DECISIONS.md)).

**`summary.json` is one JSON document listing everything the tool understood** about the
board: how many pads, vias and footprints; the drilled holes; where each footprint sits
(to the nanometre) and which way up; and which pads are on which nets. For a schematic it
lists the components and the nets. The runner builds it by running several `kicad-cli`
exports and merging them — the case author never sees the intermediate files.
[`docs/VALIDATION.md`](docs/VALIDATION.md) §4 has the exact schema and a real example.

**An "answer" (an expected file) is what KiCad produced** when the case was written,
generated once and then frozen in the repo. Other test frameworks call it a snapshot, a
baseline, or a golden file. It is never hand-written — a hand-written answer records a
human's belief about KiCad, a generated one records KiCad's behaviour — and it lives under
`expected/<version>/` because "correct" is defined by a specific KiCad release.

The **runner** walks `suites/`, invokes the **adapter** (for KiCad, a subprocess wrapping
`kicad-cli`), and decides pass/fail:

- a **happy** case compares every answer. If the summary matches, the tool parsed the file
  into the same thing KiCad did; if it doesn't, the JSON diff names the exact fact that is
  wrong (a pad on the wrong net is one changed line). The gerbers and drill file are
  compared **byte for byte** after stripping the two date lines KiCad stamps into each —
  which is what catches a moved track or a shifted hole, things the summary does not
  record ([DL-0026](docs/DECISIONS.md)).
- a **failure** case records no answers at all and compares only the outcome: the tool must
  **gracefully reject** the input. A **crash** (termination by signal / exit `>128`) is a
  distinct verdict and **never** a pass ([`docs/DESIGN.md`](docs/DESIGN.md) §3a). Failure
  cases also carry a **positive control** — a defect-free copy of the input that must be
  accepted — because a test that can't fail is not evidence.

A case that is about something extra — a DRC run, say — adds one line, `extra = ["drc"]`,
and gets `drc.json` alongside the standard four. That is the only knob there is.

That's the whole thing. Full details in
[`docs/TEST_CASE_FORMAT.md`](docs/TEST_CASE_FORMAT.md).

> **Honest scope note.** The gerber and drill answers are recorded as **bytes**, which
> makes them a strong KiCad-version-regression signal and **not** a cross-implementation
> conformance bar: a clean-room tool emitting valid RS-274X with different apertures would
> fail them while being perfectly correct. So in ecosystem mode they report `INFO`, never
> `FAIL`. The fair cross-implementation comparison — rasterize both sides and diff the
> pixels — is on the roadmap ([`docs/VALIDATION.md`](docs/VALIDATION.md) §7.4).

---

## Quickstart

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

# Re-record the expected answers after a kicad-cli version bump (review the diff first):
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
[`docs/DESIGN.md`](docs/DESIGN.md). Its output is compared against the **KiCad-recorded**
answer — KiCad is authoritative; a divergence is a finding for the *other* tool (or,
occasionally, for the suite), triaged in a checked-in divergence ledger. A second
implementation does not have to imitate KiCad's exports: it emits the `summary.json`
schema directly ([`docs/VALIDATION.md`](docs/VALIDATION.md) §4), and is not judged on the
byte-recorded `gerbers/` / `drill/` answers at all (§7.4).

---

## Repo map

```
kicad-conformance/
├── README.md                  # you are here
├── docs/
│   ├── DESIGN.md              # architecture: adapter contract, comparison kinds, normalizers, coverage
│   ├── VALIDATION.md          # what a case compares: the standard answers per input type + known gaps
│   ├── TEST_CASE_FORMAT.md    # the authoring spec (manifest schema, layout, worked examples)
│   ├── DECISIONS.md           # numbered decision log (ADR-style, append-only)
│   ├── DIVERGENCES.md         # checked-in known-divergence ledger (DL-0009/DL-0018)
│   └── ROADMAP.md             # milestones
├── suites/                    # the curated, hand-authored corpus (committed)
│   ├── board-parse/{happy,failure}/      # .kicad_pcb -- the standard board answers + parse failures
│   ├── schematic-parse/{happy,failure}/  # .kicad_sch -- summary + render + parse failures
│   ├── drc/{happy,failure}/              # design-rule findings (extra = ["drc"])
│   ├── erc/{happy,failure}/              # electrical-rule findings (empty; next up)
│   ├── netlist/{happy,failure}/          # netlist-interchange specifics (empty; hierarchy cases)
│   ├── symbol-lib/{happy,failure}/       # .kicad_sym -- drawings only (empty)
│   ├── footprint-lib/{happy,failure}/    # .pretty -- drawings only (empty)
│   ├── gerber/{happy,failure}/           # cases specifically ABOUT gerber output (empty; routine
│   └── drill/{happy,failure}/            #   fab coverage rides on every board case -- DL-0026)
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

1. Pick the suite (the input's family) and polarity (`happy`/`failure`).
2. Create `suites/<suite>/<happy|failure>/<NNNN-slug>/` with a `case.toml` and the
   smallest possible input that demonstrates exactly one concept.
3. Cite the format-doc section in `doc = ` and write a one-line `concept = `.
4. **Write nothing else.** The input's file type decides which answers get recorded. Add
   `extra = ["drc"]` (or `erc`, `pos`, `stats`, `ipcd356`, `netlist`) only if the case is
   genuinely *about* that projection; add a second *case* only for a different input. A
   `failure/` case adds `control = "…"` and `error_contains = "…"` instead.
5. Record the answer with `python -m runner --regenerate` **inside the
   `kicad/kicad:10.0.5` Docker image** (so it is LF / Linux-canonical), read the diff, and
   commit `expected/10.0.5/…`.
6. Run `python -m runner <your case>` and confirm it passes — then **break the input**
   (move a pad to another net, rotate a footprint) and confirm it goes red. For a failure
   case, add the positive control and confirm it fails for the *right* reason. A crash is
   never a pass.

The full contributor checklist is in [`docs/TEST_CASE_FORMAT.md`](docs/TEST_CASE_FORMAT.md).

## License

The suite tooling and hand-authored fixtures are open source (license TBD by the
owner). Downloaded `corpus/` projects retain their upstream licenses and are never
redistributed by this repo.
