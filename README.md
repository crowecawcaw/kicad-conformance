# kicad-conformance

An open-source **conformance test suite for KiCad file formats and CLI behavior.**

Each test case is a small, single-concept, human-readable artifact that doubles as
documentation. The corpus is owned by this repo and is **implementation-agnostic**:
KiCad itself (via `kicad-cli`) is the reference oracle, and any other tool — a
clean-room parser, a third-party exporter — can run the *same* suite through a thin
adapter.

**Status:** 8 cases, green against `kicad-cli` 10.0.5 in CI (`board-parse`,
`schematic-parse`, `drc`). See [`docs/ROADMAP.md`](docs/ROADMAP.md) for what is not
covered yet.

---

## Why this exists — the three goals

1. **Documentation.** KiCad's file formats and CLI behavior are under-documented and
   the dev-docs pages [lag the source](https://dev-docs.kicad.org/en/file-formats/).
   Every test case cites the format-doc section it exercises and states one behavior
   in words, so a directory listing reads like a coverage map and each case is a
   worked example. Where the docs are not merely behind but **wrong**, the finding is
   logged in [`docs/UNDOCUMENTED.md`](docs/UNDOCUMENTED.md) with the command that proves
   it — the official format pages are stamped 2024-11 and still claim to cover "all
   versions of KiCad from 6.0", which is no longer true.
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
suites/board-parse/populated-board/
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
[`docs/DESIGN.md`](docs/DESIGN.md) §3b has the exact schema.

**An "answer" (an expected file) is what KiCad produced** when the case was written,
generated once and then frozen in the repo. Other test frameworks call it a snapshot, a
baseline, or a golden file. It is never hand-written — a hand-written answer records a
human's belief about KiCad, a generated one records KiCad's behaviour — and it lives under
`expected/<version>/` because "correct" is defined by a specific KiCad release.

The **runner** walks `suites/`, invokes the **adapter** (for KiCad, a subprocess wrapping
`kicad-cli`), and decides pass/fail:

- a **happy** case (no `control` set) compares every answer. If the summary matches, the
  tool parsed the file into the same thing KiCad did; if it doesn't, the JSON diff names
  the exact fact that is wrong (a pad on the wrong net is one changed line). The gerbers
  and drill file are compared **byte for byte** after stripping the date lines KiCad
  stamps into each — which is what catches a moved track or a shifted hole, things the
  summary does not record ([DL-0026](docs/DECISIONS.md)).
- a **rejection case** (sets `control`) records no answers at all and compares only the
  outcome: the tool must **gracefully reject** the input. A **crash** (termination by
  signal / exit `>128`) is a distinct verdict and **never** a pass
  ([`docs/DESIGN.md`](docs/DESIGN.md) §3a). Rejection cases also carry a **positive
  control** — a defect-free copy of the input that must be accepted — because a test that
  can't fail is not evidence.

A case that is about something extra — a DRC run, say — adds one line, `extra = ["drc"]`,
and gets `drc.json` alongside the standard four. That is the only knob there is.

That's the whole thing. Full details in
[`docs/TEST_CASE_FORMAT.md`](docs/TEST_CASE_FORMAT.md).

> **Honest scope note.** The gerber and drill answers are recorded as **bytes**, which
> makes them a strong KiCad-version-regression signal and **not** a cross-implementation
> conformance bar: a clean-room tool emitting valid RS-274X with different apertures would
> fail them while being perfectly correct. So in ecosystem mode they report `INFO`, never
> `FAIL`. The fair cross-implementation comparison — rasterize both sides and diff the
> pixels — is on the roadmap ([`docs/DESIGN.md`](docs/DESIGN.md) §3d/§7).

---

## Quickstart

The runner is a small **Python 3.11+ program with no third-party runtime dependencies**
(uses stdlib `tomllib`). See [DL-0002](docs/DECISIONS.md) for the rationale. The adapter
boundary is a language-agnostic subprocess contract, so the runner choice does not lock
in the implementation-under-test. **The host has no Python in CI or in this workflow —
everything runs inside the pinned `kicad/kicad:10.0.5` Docker image:**

```bash
# scripts/run.sh wraps this Docker invocation; any args pass straight to `python -m runner`
scripts/run.sh                              # run everything under suites/
scripts/run.sh suites/drc/                  # scope to one suite
scripts/run.sh suites/board-parse/populated-board  # scope to one case
scripts/run.sh --determinism-check          # run-twice self-test

# Re-record the expected answers after a kicad-cli version bump (review the diff first):
scripts/run.sh --regenerate suites/
```

Equivalently, spelled out:

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
python -m runner --adapter ./my-adapter.sh suites/board-parse/
```

The adapter is any executable that answers the verb protocol in
[`docs/DESIGN.md`](docs/DESIGN.md) §2. Its output is compared against the **KiCad-recorded**
answer — KiCad is authoritative; a divergence is a finding for the *other* tool (or,
occasionally, for the suite), triaged in a checked-in divergence ledger
([`docs/DIVERGENCES.md`](docs/DIVERGENCES.md)). A second implementation does not have to
imitate KiCad's exports: it emits the `summary.json` schema directly
([`docs/DESIGN.md`](docs/DESIGN.md) §3b), and is not judged on the byte-recorded
`gerbers/`/`drill/` answers at all (§3d).

---

## Repo map

```
kicad-conformance/
├── README.md                  # you are here
├── docs/
│   ├── DESIGN.md               # architecture + what a case compares: adapter contract, comparison kinds, normalizers
│   ├── TEST_CASE_FORMAT.md    # the authoring spec (manifest schema, layout, worked examples)
│   ├── DECISIONS.md           # numbered decision log (ADR-style, append-only)
│   ├── DIVERGENCES.md         # checked-in known-divergence ledger (DL-0009/DL-0018)
│   ├── COVERAGE.md            # which lines of KiCad the suite executes, measured with gcov
│   ├── ASSERTED_COVERAGE.md   # ...and which of them anything actually asserts (DL-0030/DL-0031)
│   ├── UNDOCUMENTED.md        # log of KiCad behaviours missing from, or contradicted by, the official docs
│   └── ROADMAP.md             # milestones
├── suites/                    # the curated, hand-authored corpus (committed)
│   ├── board-parse/           # .kicad_pcb -- the standard board answers + rejection cases
│   ├── schematic-parse/       # .kicad_sch -- summary + render + rejection cases
│   └── drc/                   # design-rule findings (extra = ["drc"])
├── adapters/
│   └── kicad.py                # the reference adapter -- an executable, outside the runner package
├── runner/                    # the runner (10 modules; module map in runner/__init__.py's docstring)
└── .github/workflows/         # CI
```

`suites/` is the curated documentation corpus — small, hand-authored, single-concept. A
suite for `erc`, `netlist`-specific cases, symbol/footprint libraries, or fab-specific
gerber/drill cases doesn't exist in the tree yet — it is created when its first case is
authored ([`docs/ROADMAP.md`](docs/ROADMAP.md) M1–M4). A large real-world `corpus/` for a
scheduled coverage sweep is a separate, later, gitignored idea ([DL-0009](docs/DECISIONS.md))
and does not exist yet either.

---

## Contributing a case

1. Pick the suite (the input's family).
2. Create `suites/<suite>/<slug>/` with a `case.toml` and the smallest possible input
   that demonstrates exactly one concept. A rejection-case slug is conventionally
   prefixed `rejects-`.
3. Cite the format-doc section in `doc = ` and write a one-line `concept = `.
4. **Write nothing else.** The input's file type decides which answers get recorded. Add
   `extra = ["drc"]` (or `erc`, `pos`, `stats`, `ipcd356`, `netlist`) only if the case is
   genuinely *about* that projection; add a second *case* only for a different input. A
   rejection case adds `control = "…"` and `error_contains = "…"` instead — setting
   `control` is what makes it a rejection case, not a directory choice.
5. Record the answer with `scripts/run.sh --regenerate <case>` (runs inside the
   `kicad/kicad:10.0.5` Docker image, so it is LF / Linux-canonical), read the diff, and
   commit `expected/10.0.5/…`.
6. Run `scripts/run.sh <your case>` and confirm it passes — then **break the input**
   (move a pad to another net, rotate a footprint) and confirm it goes red. For a
   rejection case, add the positive control and confirm it fails for the *right* reason.
   A crash is never a pass.

The full contributor checklist is in [`docs/TEST_CASE_FORMAT.md`](docs/TEST_CASE_FORMAT.md).

> **Where step 6 is going.** Breaking the input by hand proves the case is falsifiable
> *once*, and leaves no artifact — so nothing re-checks it and a case can quietly rot into
> one that passes whatever KiCad does. [`docs/ASSERTED_COVERAGE.md`](docs/ASSERTED_COVERAGE.md)
> specifies the fix: commit the broken input as `perturb/<slug>/`, and a new runner mode
> (`--verify-assertions`) re-runs it every time and requires the case to go red. That is
> also what turns [`docs/COVERAGE.md`](docs/COVERAGE.md)'s "which lines ran" into "which
> lines anything asserts" ([DL-0030](docs/DECISIONS.md), [DL-0031](docs/DECISIONS.md)).
> Designed, not yet implemented — step 6 is still manual today.

## License

The suite tooling and hand-authored fixtures are open source (license TBD by the
owner). A future downloaded `corpus/` of real-world projects would retain their upstream
licenses and never be redistributed by this repo.
