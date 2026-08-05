"""kicad-conformance reference runner. Python 3.11+, stdlib only.

Entrypoint: `python3 -m runner [PATHS...]`. A `case.toml` names no verb and no output
file -- the input's suffix chooses the recorded answers (`engine.battery_for`) and
`extra` is the only opt-in knob. Answers are raw kicad-cli output, normalized only for
observed run-to-run drift (`normalize.py`) and compared as bytes or as directory trees.

| Module | Responsibility |
|---|---|
| `cli.py` | Flags, orchestration, report printing. |
| `manifest.py` | Parses and validates `case.toml`; rejects unknown keys. |
| `adapter.py` | Invokes an adapter executable with `LC_ALL`/`TZ` pinned. |
| `engine.py` | The per-type answer set, the OK/REJECT/CRASH classifier, the comparators, `--regenerate`, and the rejection-case exit+control path. |
| `normalize.py` | Per-format redaction of dates, embedded paths and minted UUIDs. |
| `determinism.py` | The run-twice self-test that keeps `normalize.py` honest. |
| `assertions.py` | `--verify-assertions`: each `perturb/<slug>/` must move a recorded answer. |

The reference adapter lives outside this package, at `adapters/kicad.py` -- an ordinary
executable wrapping `kicad-cli`, runnable standalone. The runner's direct subprocess
child is that adapter, never `kicad-cli` itself, so the adapter re-raises any signal that
killed `kicad-cli` against itself; that is what lets `engine.classify` see a genuinely
signaled child and report CRASH instead of laundering it into a normal exit code.
"""

__version__ = "0.1.0"
