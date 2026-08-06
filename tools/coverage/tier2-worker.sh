#!/usr/bin/env bash
# Runs ONE Tier-2 attribution job INSIDE the coverage image (see tier2-run.sh
# §4.1/§4.2). Invoked by tier2-run.sh via `docker run`, one job per container so a
# Docker Desktop crash mid-job never corrupts more than that one job's output.
#
#   tier2-worker.sh <run_id> base <case_dir>
#   tier2-worker.sh <run_id> pert <case_dir> <perturbation_slug>
#
# <case_dir> is relative to /work (e.g. suites/board-parse/populated-board).
#
# Base run: executes the case's OWN input, unmodified -- this is base[C].
# Pert run: copies the case directory to scratch under /tmp (never touches suites/,
#   per the project rule against modifying suites/), overlays perturb/<slug>/'s files
#   on top by filename (the overlay IS just
#   "a file with this name replaces the input of the same name"), then executes that.
#   This is pert[C,P] -- but ONLY for perturbations Tier 1 already scored ASSERTED
#   (checked by the caller before this script is ever invoked): an INERT/CRASH/
#   INVALID-PERTURBATION perturbation credits nothing by definition (§4.2), so running
#   it under gcov would be pure waste.
#
# Output: /work/tools/coverage/out/tier2/perline/<run_id>.json (per-line counts,
#   KiCad-source-only, see tier2_extract.py) and a `.done` marker for resumability.
set -uo pipefail   # NOT -e: a case that CRASHes or FAILs must still let us extract
                   # whatever it touched and mark the job done -- coverage extraction
                   # cares about side effects, not exit codes.

RUN_ID="$1"; KIND="$2"; CASE_DIR="$3"; PERT_SLUG="${4:-}"

SRC_DIR=/src/kicad
BUILD_DIR=/src/build
RAW_ROOT=/coverage/raw/tier2
OUT_DIR=/work/tools/coverage/out/tier2
LOG_DIR="$OUT_DIR/logs"
PERLINE_DIR="$OUT_DIR/perline"
STATE_DIR="$OUT_DIR/state"
mkdir -p "$LOG_DIR" "$PERLINE_DIR" "$STATE_DIR"

export LC_ALL=C.UTF-8 TZ=UTC
export KICAD_CLI=/opt/kicad-cov/bin/kicad-cli

cd /work

if [ "$KIND" = "pert" ]; then
    SCRATCH="/tmp/tier2-scratch/$RUN_ID"
    rm -rf "$SCRATCH"
    mkdir -p "$SCRATCH"
    cp -a "$CASE_DIR/." "$SCRATCH/"
    rm -rf "$SCRATCH/perturb"
    if [ ! -d "$CASE_DIR/perturb/$PERT_SLUG" ]; then
        echo "ERROR: no such perturbation $CASE_DIR/perturb/$PERT_SLUG" >&2
        exit 1
    fi
    # Overlay: rule 1 -- a file in perturb/<slug>/ replaces the same-named input; every
    # other input is used unchanged (already true, it's a full copy of the case dir).
    cp -a "$CASE_DIR/perturb/$PERT_SLUG/." "$SCRATCH/"
    RUN_PATH="$SCRATCH"
elif [ "$KIND" = "base" ]; then
    RUN_PATH="/work/$CASE_DIR"
else
    echo "ERROR: KIND must be base or pert, got '$KIND'" >&2
    exit 2
fi

BUCKET="$RAW_ROOT/$RUN_ID"
rm -rf "$BUCKET"
mkdir -p "$BUCKET"
export GCOV_PREFIX="$BUCKET"
export GCOV_PREFIX_STRIP=0

LOG="$LOG_DIR/$RUN_ID.log"
echo "=== $RUN_ID ($KIND) run_path=$RUN_PATH $(date -u +%FT%TZ) ===" > "$LOG"
python3 -m runner "$RUN_PATH" >> "$LOG" 2>&1
run_rc=$?
echo "--- exit code: $run_rc ---" >> "$LOG"

# Sanity check (README's known trap): a CRASH here on a BASE run means this run
# contributes nothing to `base[C]` and any credit computed against it would be bogus.
# Never silently swallow it -- surface it in the log where the aggregation step greps.
if grep -q '\[CRASH\]' "$LOG"; then
    echo "!!! CRASH detected in $RUN_ID -- base/pert counts for this run are suspect !!!" >> "$LOG"
fi

# Graft this run's .gcda onto the REAL build tree (collect.sh does the same thing, for
# the same reason): `gcov -o <objdir> <path>` resolves the data file to open as
# <objdir>/<basename-of-path>, NOT by opening <path> itself. Pointing gcov straight at
# the bucket's copy silently produced all-zero counts ("cannot open data file, assuming
# not executed" on stderr -- easy to miss, and it was missed on the first pass of this
# tool). Safe to graft directly onto /src/build with no cleanup: this container is
# `--rm`, one job per container, so its writable layer (including this graft) is
# discarded when it exits -- the NEXT job's container starts from the same clean image.
if [ -d "$BUCKET$BUILD_DIR" ]; then
    cp -a "$BUCKET$BUILD_DIR/." "$BUILD_DIR/"
fi

python3 /work/tools/coverage/tier2_extract.py \
    --bucket "$BUCKET$BUILD_DIR" \
    --build-dir "$BUILD_DIR" \
    --src-dir "$SRC_DIR" \
    --out "$PERLINE_DIR/$RUN_ID.json" \
    >> "$LOG" 2>&1

rm -rf "$BUCKET"
if [ "$KIND" = "pert" ]; then
    rm -rf "/tmp/tier2-scratch/$RUN_ID"
fi

touch "$STATE_DIR/$RUN_ID.done"
echo "done: $RUN_ID"
