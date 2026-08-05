#!/usr/bin/env bash
# Derive the ENGINE denominator from the pinned instrumented build, and report
# engine line coverage against it.
#
#   tools/coverage/engine-scope.sh            # all four stages
#   tools/coverage/engine-scope.sh graph      # 1. ELF reference graph      (~2 min)
#   tools/coverage/engine-scope.sh close      # 2. reachability closure     (~1 min)
#   tools/coverage/engine-scope.sh lines      # 3. gcov line attribution    (~10 min)
#   tools/coverage/engine-scope.sh report     # 4. join + print             (~1 min)
#   tools/coverage/engine-scope.sh why SYM…   # audit one classification
#
# WHAT THIS ANSWERS that the main coverage report cannot: which KiCad lines are
# *in scope* for a CLI-only conformance suite. The global % divides by all of
# KiCad, most of which is GUI a `kicad-cli` run can never enter; this instead
# divides by the transitive closure of the CLI entry points over the real symbol
# reference graph of the built objects (see engine_elf.py, engine_scope.py,
# engine-roots.json for the definition and the rejected alternatives).
#
# Everything runs inside the pinned coverage image, so the answer is a function of
# (image, engine-roots.json) and nothing else. Intermediates live in the
# `kicad-engine-scope` Docker volume -- not a Windows bind mount, for the same
# >10x reason run-suite.sh gives.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
IMAGE="${COVERAGE_IMAGE:-kicad-conformance/kicad-coverage:10.0.5}"
RAW_VOLUME="${COVERAGE_RAW_VOLUME:-kicad-coverage-raw}"
SCOPE_VOLUME="${ENGINE_SCOPE_VOLUME:-kicad-engine-scope}"
OUT="$HERE/out/engine"
JOBS="${ENGINE_SCOPE_JOBS:-$(nproc 2>/dev/null || echo 4)}"

# Docker Desktop for Windows needs a Windows-shaped host path for -v.
if command -v cygpath >/dev/null 2>&1; then
    HOSTREPO="$(cygpath -w "$REPO")"
else
    HOSTREPO="$REPO"
fi

docker volume create "$SCOPE_VOLUME" >/dev/null

run() {
    docker run --rm \
        -v "$HOSTREPO:/work" \
        -v "$SCOPE_VOLUME:/scope" \
        -v "$RAW_VOLUME:/coverage" \
        --entrypoint bash "$IMAGE" -c "$1"
}

stage_graph() {
    echo "== stage 1/4: ELF reference graph =="
    run "rm -rf /scope/graph && python3 /work/tools/coverage/engine_elf.py \
             --build-dir /src/build --outdir /scope/graph"
}

stage_close() {
    echo "== stage 2/4: reachability closure from the CLI entry points =="
    run "rm -rf /scope/scope && python3 /work/tools/coverage/engine_scope.py \
             --graphdir /scope/graph close \
             --roots /work/tools/coverage/engine-roots.json --outdir /scope/scope"
}

stage_lines() {
    echo "== stage 3/4: gcov line attribution (graft + gcov over every .gcno) =="
    run "python3 /work/tools/coverage/engine_lines.py scan \
             --build-dir /src/build --raw /coverage \
             --out /scope/lines/linemap.jsonl.gz --jobs $JOBS"
}

stage_floor() {
    # The FREE FLOOR. `kicad-cli version` does no work at all, but it runs every
    # static constructor in kicad-cli and (because it constructs the COMMAND objects)
    # every subcommand's argparse setup. Those lines are "covered" in any run and
    # tell you nothing -- this is the measured size of that effect.
    echo "== free floor: what a no-op kicad-cli invocation executes by itself =="
    run "set -e
         rm -rf /scope/floor && mkdir -p /scope/floor/raw
         GCOV_PREFIX=/scope/floor/raw GCOV_PREFIX_STRIP=0 \
             /opt/kicad-cov/bin/kicad-cli version >/dev/null 2>&1 || true
         n=\$(find /scope/floor/raw -name '*.gcda' | wc -l)
         echo \"  floor run wrote \$n .gcda\"
         [ \"\$n\" -gt 0 ] || { echo 'FATAL: floor run produced no counters' >&2; exit 3; }
         python3 /work/tools/coverage/engine_lines.py scan \
             --build-dir /src/build --raw /scope/floor/raw \
             --out /scope/floor/linemap.jsonl.gz --jobs $JOBS"
}

stage_report() {
    echo "== stage 4/4: join and report =="
    mkdir -p "$OUT"
    run "python3 /work/tools/coverage/engine_lines.py report \
             --linemap /scope/lines/linemap.jsonl.gz \
             --scope /scope/scope --graph /scope/graph --per-command \
             --floor /scope/floor/linemap.jsonl.gz \
             --outdir /work/tools/coverage/out/engine"
    run "cp /scope/scope/closure-stats.json /scope/graph/elf-stats.json \
            /scope/lines/linemap.jsonl.gz.stats.json \
            /work/tools/coverage/out/engine/ 2>/dev/null || true"
    echo
    echo "artifacts in tools/coverage/out/engine/:"
    ls -la "$OUT"
}

case "${1:-all}" in
    graph)  stage_graph ;;
    close)  stage_close ;;
    lines)  stage_lines ;;
    floor)  stage_floor ;;
    report) stage_report ;;
    why)    shift
            run "python3 /work/tools/coverage/engine_scope.py --graphdir /scope/graph \
                     why --roots /work/tools/coverage/engine-roots.json $*" ;;
    grep)   shift
            run "python3 /work/tools/coverage/engine_scope.py --graphdir /scope/graph \
                     grep $*" ;;
    all)    stage_graph; stage_close; stage_lines; stage_floor; stage_report ;;
    *)      echo "usage: $0 [all|graph|close|lines|floor|report|why SYM...|grep REGEX]" >&2
            exit 2 ;;
esac
