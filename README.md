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
oracle version bumps. Which answers a case has depends entirely on the input file's
type; see [`docs/FORMAT.md`](docs/FORMAT.md) for the full table.

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

## License

Open source; license TBD by the owner.
