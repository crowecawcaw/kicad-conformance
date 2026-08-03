#!/usr/bin/env bash
# Run the conformance suite the same way CI does: inside the pinned kicad/kicad:10.0.5
# Docker Linux image (DL-0001, DL-0010), with LC_ALL/TZ pinned (DESIGN §4). Any args pass
# straight to `python -m runner`, so this one script also does the regenerate job:
#
# Usage:
#   scripts/run.sh                        # run everything under suites/
#   scripts/run.sh suites/drc/            # scope to one suite
#   scripts/run.sh --determinism-check    # run-twice self-test
#   scripts/run.sh --regenerate suites/   # regenerate expected/ from the current adapter
#
# ALWAYS inspect the diff before committing after a --regenerate (DESIGN §5) -- this
# script does not commit anything for you, and a diff under expected/** should read as a
# semantic change, not noise.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Git Bash on Windows mangles /work-style mount paths unless disabled.
export MSYS_NO_PATHCONV=1

docker run --rm \
  -v "${repo_root}:/work" \
  -w /work \
  -e LC_ALL=C.UTF-8 \
  -e TZ=UTC \
  kicad/kicad:10.0.5 \
  python3 -m runner "${@:-suites/}"
