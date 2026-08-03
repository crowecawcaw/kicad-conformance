#!/usr/bin/env bash
# Regenerate expected files inside the pinned kicad/kicad:10.0.5 Docker Linux image, so
# the committed bytes are LF/platform-canonical (DL-0016) no matter what host you
# develop on. ALWAYS inspect the diff before committing (DESIGN §5) -- this script does
# not commit anything for you.
#
# Usage:
#   scripts/regen.sh                              # regenerate every expected file under suites/
#   scripts/regen.sh suites/drc/happy/0001-*       # regenerate one case
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export MSYS_NO_PATHCONV=1

docker run --rm \
  -v "${repo_root}:/work" \
  -w /work \
  -e LC_ALL=C.UTF-8 \
  -e TZ=UTC \
  kicad/kicad:10.0.5 \
  python3 -m runner --regenerate "${@:-suites/}"

echo
echo "Expected files (re)generated. Run 'git status' / 'git diff' under suites/ and" \
     "inspect before committing -- a diff should read as a semantic change, not noise."
