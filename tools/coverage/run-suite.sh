#!/usr/bin/env bash
# Run the conformance suite against the instrumented KiCad and leave the raw gcov
# counters in a Docker volume, then build the report into a host directory.
#
#   tools/coverage/run-suite.sh [--out DIR] [--image NAME] [--raw-volume NAME]
#                               [--fresh] [-- <runner args...>]
#
# Default runner args are `suites/`, i.e. the same invocation the project's normal
# docker one-liner uses -- only the image and two env vars differ.
#
# WHY THE RAW COUNTERS LIVE IN A DOCKER VOLUME, NOT A BIND MOUNT.
# libgcov dumps one .gcda per instrumented object at every process exit -- ~1900 small
# files for a KiCad-sized tree, once per kicad-cli invocation, and a single board case
# makes six invocations. On Docker Desktop for Windows a bind-mounted host directory
# crosses the VM/host filesystem boundary, and that dump was MEASURED at 3.39 s per
# `kicad-cli version` versus 0.32 s writing to the VM's own filesystem -- a >10x
# penalty applied to every invocation in the suite. Keeping the counters in a named
# volume (which lives inside the VM) is the difference between a ~2.5-hour run and a
# ~20-minute one, and costs nothing: collect.sh runs inside a container anyway, so it
# just mounts the same volume. The report -- a few MB, written once -- still lands on
# the host.
#
# Counters ACCUMULATE across runs in the volume. Pass --fresh (or docker volume rm it)
# to start a clean measurement; keep it to merge several suite invocations.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

IMAGE="kicad-conformance/kicad-coverage:10.0.5"
OUT="$REPO/tools/coverage/out"
RAW_VOLUME="kicad-coverage-raw"
FRESH=0
RUNNER_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --out)        OUT="$2"; shift 2 ;;
        --image)      IMAGE="$2"; shift 2 ;;
        --raw-volume) RAW_VOLUME="$2"; shift 2 ;;
        --fresh)      FRESH=1; shift ;;
        --)           shift; RUNNER_ARGS=("$@"); break ;;
        -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)       echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done
[ ${#RUNNER_ARGS[@]} -gt 0 ] || RUNNER_ARGS=(suites/)

REPORT="$OUT/report"
mkdir -p "$REPORT"

if [ "$FRESH" = 1 ]; then
    docker volume rm "$RAW_VOLUME" >/dev/null 2>&1 || true
fi
docker volume create "$RAW_VOLUME" >/dev/null

# Windows path form for -v, and MSYS_NO_PATHCONV so Git Bash leaves /work alone.
# Whole paths go through cygpath -- never concatenate a Windows path with a "/sub"
# suffix, the mixed separators are not reliably accepted.
win() { case "$(uname -s)" in MINGW*|MSYS*) cygpath -w "$1" ;; *) printf '%s' "$1" ;; esac; }

echo "suite -> $IMAGE   raw profiles -> docker volume '$RAW_VOLUME'"
MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(win "$REPO"):/work" \
    -v "$RAW_VOLUME:/coverage/raw" \
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
    -v "$RAW_VOLUME:/coverage/raw" \
    -v "$(win "$REPORT"):/coverage/report" \
    "$IMAGE" collect-coverage /coverage/report

echo
echo "HTML report: $REPORT/html/index.html"
echo "summary:     $REPORT/focus.json"
exit $rc
