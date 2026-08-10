# kicad-conformance

A corpus of KiCad input files paired with the raw `kicad-cli` output recorded for
them. KiCad 10.0.5 is the reference oracle: any implementation — KiCad itself, a
clean-room parser, a third-party exporter — can be checked against the same corpus,
either with its own runner or with the bundled reference runner (`runner/` +
`adapters/kicad.py`).

## Case anatomy

```
suites/board-parse/populated-board/
├── case.toml
├── board.kicad_pcb
└── expected/
    └── 10.0.5/
        ├── stats.json
        ├── pos.csv
        ├── ipcd356.d356
        ├── render-F_Cu.svg
        ├── gerbers/
        └── drill/
```

`case.toml`, in full:

```toml
concept = "A populated two-layer board: one SMD resistor, one through-hole capacitor, a track, a via."
doc     = "sexpr-pcb"
input   = "board.kicad_pcb"
```

Answers are raw `kicad-cli` output with volatile content (timestamps, UUIDs,
embedded scratch-dir paths) redacted — nothing is hand-written or synthesized. They
are recorded once, under `expected/<kicad-version>/`, and re-recorded only when the
oracle version bumps.

Which answers a case has follows from the input file's type, plus whatever it opts into
with `extra`:

| `extra` | What it adds |
|---|---|
| `["drc"]` | the board's DRC report |
| `["erc"]` | the schematic's ERC report |
| `["refill"]` | KiCad **recomputes** the board's zone fills, and the computed geometry is recorded — the one answer that exercises a fill engine rather than replaying a fill the input already carries |
| `["roundtrip"]` | an invariant with no recorded answer: re-serializing the fixture must not change what it exports |

See [`docs/FORMAT.md`](docs/FORMAT.md) for the full table, and for why `refill` records a
projection of the refilled board rather than the board itself.

## Quickstart

```bash
scripts/run.sh                                      # run everything under suites/
scripts/run.sh suites/drc/                           # scope to one suite
scripts/run.sh suites/board-parse/populated-board    # scope to one case
scripts/run.sh --regenerate suites/                  # re-record expected/ (review the diff)
```

`scripts/run.sh` runs the reference runner inside the pinned `kicad/kicad:10.0.5`
Docker image with `LC_ALL=C.UTF-8`/`TZ=UTC` pinned internally — you don't need
Python or KiCad installed locally.

To test another implementation, point the runner at its adapter instead:
`python -m runner --adapter ./my-adapter.sh suites/`.

## Contributing a case

1. Create `suites/<suite>/<slug>/` with a `case.toml`.
2. Write one sentence in `concept` describing what the case asserts.
3. Drop in the smallest input file that demonstrates it.
4. Run `scripts/run.sh --regenerate <case>` and commit `expected/10.0.5/…`.
5. Optionally add `perturb/<slug>/` — a modified copy of the input proving the
   case can actually fail (see `docs/FORMAT.md`).

### Geometry that decides a rounding rule spans both signs

A case that pins how a computed coordinate lands on the integer grid has to be
drawn on both sides of the origin. On positive coordinates `floor` and
truncation toward zero are the **same function**, and so are `round` and `ceil`
for any fraction above a half. A fixture drawn entirely in one quadrant
therefore records an answer that two different rules reproduce exactly, and
cannot say which one the oracle used — it is passed by an implementation that is
wrong in a way the fixture cannot see. Mirroring the construction across the
origin separates them, because the pair of rules that agree on `+N.f` disagree
on `-N.f`. Pick fractions well clear of an exact half, too: a tie is a separate
question with its own answer, and a case that lands on one is measuring both.

The same warning applies to anything with a sign in it — a rounding mode, a
half-open interval, an offset "toward the outside". Ask what *other* rule would
also reproduce the geometry you drew.

## License

Open source; license TBD by the owner.
