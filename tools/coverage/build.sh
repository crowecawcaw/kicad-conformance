#!/usr/bin/env bash
# Build the gcov-instrumented KiCad image. Run from anywhere; paths are derived.
#
#   tools/coverage/build.sh [--jobs N] [--tag NAME] [--kicad-tag 10.0.5] [--retries N]
#
# This is a long build (see README.md for measured timings). It is safe to
# interrupt and re-run: compiled objects persist in the BuildKit `ccache` cache
# mount, so a resumed build replays them at cache-hit speed.
#
# Resilience: Docker Desktop's BuildKit RPC connection has been observed to drop
# ("failed to receive status: rpc error: code = Unavailable desc = error reading
# from server: EOF") under a sustained ~40-minute compile, taking the whole daemon
# down with it. That is a BuildKit/Desktop stability problem, not a recipe error --
# ccache makes a retry cheap (already-compiled objects replay in seconds), so this
# script retries the build automatically, restarting Docker Desktop first if the
# daemon itself died.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

JOBS="${COVERAGE_BUILD_JOBS:-8}"
TAG="kicad-conformance/kicad-coverage:10.0.5"
KICAD_TAG="10.0.5"
RETRIES="${COVERAGE_BUILD_RETRIES:-5}"
RETRY_DELAY=15

while [ $# -gt 0 ]; do
    case "$1" in
        --jobs)      JOBS="$2"; shift 2 ;;
        --tag)       TAG="$2"; shift 2 ;;
        --kicad-tag) KICAD_TAG="$2"; shift 2 ;;
        --retries)   RETRIES="$2"; shift 2 ;;
        -h|--help)   sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)           echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

docker_is_healthy() {
    docker info >/dev/null 2>&1
}

restart_docker_desktop() {
    echo "Docker daemon looks unhealthy -- restarting Docker Desktop..." >&2
    powershell.exe -NoProfile -Command \
        "Start-Process 'C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe'" \
        >/dev/null 2>&1 || true
    local waited=0
    local max_wait=180
    until docker_is_healthy; do
        if [ "$waited" -ge "$max_wait" ]; then
            echo "Docker Desktop did not become healthy within ${max_wait}s" >&2
            return 1
        fi
        sleep 5
        waited=$((waited + 5))
    done
    echo "Docker Desktop is back up (waited ${waited}s)." >&2
    return 0
}

if ! docker_is_healthy; then
    restart_docker_desktop || { echo "FATAL: could not reach a healthy Docker daemon" >&2; exit 1; }
fi

# -j is bounded by builder RAM, not cores: g++ -O0 peaks around 1-1.5 GB on KiCad's
# larger translation units. Docker Desktop's VM defaults to a fraction of host RAM;
# raise it in %USERPROFILE%\.wslconfig before raising --jobs.
echo "building $TAG  (KiCad $KICAD_TAG, -j$JOBS, up to $RETRIES attempt(s))"
export DOCKER_BUILDKIT=1

attempt=1
while :; do
    echo "=== docker build attempt $attempt/$RETRIES ==="
    docker build \
        --progress=plain \
        --build-arg "KICAD_TAG=${KICAD_TAG}" \
        --build-arg "BUILD_JOBS=${JOBS}" \
        -t "$TAG" \
        -f "$HERE/Dockerfile" \
        "$HERE"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "build succeeded on attempt $attempt"
        exit 0
    fi

    echo "build attempt $attempt failed (exit $rc)" >&2
    if [ "$attempt" -ge "$RETRIES" ]; then
        echo "FATAL: giving up after $RETRIES attempt(s)" >&2
        exit "$rc"
    fi

    # The failure mode this guards against takes the whole daemon down with it
    # (BuildKit RPC EOF), so check health and restart before retrying -- retrying
    # against a dead daemon would just fail immediately and burn an attempt.
    if ! docker_is_healthy; then
        restart_docker_desktop || { echo "FATAL: could not recover Docker daemon" >&2; exit 1; }
    fi

    attempt=$((attempt + 1))
    echo "retrying in ${RETRY_DELAY}s (ccache is warm, so this replays already-built objects fast)..." >&2
    sleep "$RETRY_DELAY"
done
