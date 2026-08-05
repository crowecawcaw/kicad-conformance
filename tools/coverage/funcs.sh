#!/usr/bin/env bash
# Per-FUNCTION coverage for named source files, from the raw counters left in the
# Docker volume by run-suite.sh. focus.json/coverage.json are per FILE; this answers
# "was ERC_TESTER::TestPinToPin ever entered".
#
#   tools/coverage/funcs.sh erc.cpp zone_filler.cpp PDF_plotter.cpp
#   tools/coverage/funcs.sh --zero-only command_pcb_drc.cpp    # only dead functions
#
# Each argument is the BASENAME of a .cpp whose .gcda to look up. Runs `gcov -f -m`
# inside the coverage image (same graft collect.sh does), parsed by gcovfuncs.py
# rather than an inline awk/python one-liner (quoting through `docker run bash -c`
# is a reliable way to get an empty table that reads as "no coverage").
#
# "NO .gcda" means no process in the run ever loaded that translation unit's object.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
IMAGE="${COVERAGE_IMAGE:-kicad-conformance/kicad-coverage:10.0.5}"
RAW_VOLUME="${COVERAGE_RAW_VOLUME:-kicad-coverage-raw}"
PYARGS=""

while [ "${1:-}" = "--zero-only" ] || [ "${1:-}" = "--min-lines" ]; do
    case "$1" in
        --zero-only)  PYARGS="$PYARGS --zero-only"; shift ;;
        --min-lines)  PYARGS="$PYARGS --min-lines $2"; shift 2 ;;
    esac
done
[ $# -gt 0 ] || { sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 2; }

win() { case "$(uname -s)" in MINGW*|MSYS*) cygpath -w "$1" ;; *) printf '%s' "$1" ;; esac; }

MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$RAW_VOLUME:/coverage/raw" \
    -v "$(win "$REPO"):/work" \
    -e PYARGS="$PYARGS" -e TARGETS="$*" \
    "$IMAGE" bash -c '
set -euo pipefail
cp -a /coverage/raw/src/build/. /src/build/ 2>/dev/null || true
for t in $TARGETS; do
    gcda=$(find /src/build -name "$t.gcda" | head -1)
    if [ -z "$gcda" ]; then
        echo "### $t -- NO .gcda (translation unit never loaded by any run)"
        continue
    fi
    echo "### $t"
    gcov -f -m -o "$(dirname "$gcda")" "$gcda" 2>/dev/null \
        | python3 /work/tools/coverage/gcovfuncs.py $PYARGS
done'
