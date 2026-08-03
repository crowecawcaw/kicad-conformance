#!/usr/bin/env bash
# Run the conformance suite against the instrumented KiCad and leave the raw gcov
# counters in a host directory.
#
#   tools/coverage/run-suite.sh [--out DIR] [--image NAME] [-- <runner args...>]
#
# Default runner args are `suites/`, i.e. the same invocation the project's normal
# docker one-liner uses -- only the image and two env vars differ.
#
# Counters ACCUMULATE across runs into --out. Delete the directory (or pass a fresh
# one) to start a clean measurement; keep it to merge several suite invocations.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

IMAGE="kicad-conformance/kicad-coverage:10.0.5"
OUT="$REPO/tools/coverage/out"
RUNNER_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --out)   OUT="$2"; shift 2 ;;
        --image) IMAGE="$2"; shift 2 ;;
        --)      shift; RUNNER_ARGS=("$@"); break ;;
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)       echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done
[ ${#RUNNER_ARGS[@]} -gt 0 ] || RUNNER_ARGS=(suites/)

RAW="$OUT/raw"
REPORT="$OUT/report"
mkdir -p "$RAW" "$REPORT"

# Windows path form for -v, and MSYS_NO_PATHCONV so Git Bash leaves /work alone.
# Whole paths go through cygpath -- never concatenate a Windows path with a "/sub"
# suffix, the mixed separators are not reliably accepted.
win() { case "$(uname -s)" in MINGW*|MSYS*) cygpath -w "$1" ;; *) printf '%s' "$1" ;; esac; }

echo "suite -> $IMAGE   raw profiles -> $RAW"
MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(win "$REPO"):/work" \
    -v "$(win "$RAW"):/coverage/raw" \
    -w /work \
    -e LC_ALL=C.UTF-8 -e TZ=UTC \
    -e KICAD_CLI=/opt/kicad-cov/bin/kicad-cli \
    -e GCOV_PREFIX=/coverage/raw -e GCOV_PREFIX_STRIP=0 \
    "$IMAGE" \
    python3 -m runner "${RUNNER_ARGS[@]}" && rc=0 || rc=$?
# `|| rc=$?` rather than a bare `rc=$?`: under `set -e` a failing suite would
# otherwise abort the script here, and a failing suite is precisely when you still
# want the coverage report. The runner's exit status is relayed at the end instead.

echo
echo "collecting..."
MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(win "$RAW"):/coverage/raw" \
    -v "$(win "$REPORT"):/coverage/report" \
    "$IMAGE" collect-coverage /coverage/report

echo
echo "HTML report: $REPORT/html/index.html"
echo "summary:     $REPORT/focus.json"
exit $rc
