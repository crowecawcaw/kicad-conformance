#!/usr/bin/env bash
# Host-side orchestrator for Tier-2 attribution. Runs
# tools/coverage/tier2-worker.sh once per job in tools/coverage/out/tier2/jobs.json,
# ONE DOCKER CONTAINER PER JOB -- deliberately not one long-lived container looping
# internally, because Docker Desktop has crashed under sustained load on this project
# before (`unexpected EOF`, exit 137, daemon 500) and a crash mid-job must lose at most
# one job's work, never the whole run.
#
# Resumable: a job is skipped if its `.done` marker already exists (a plain file under
# tools/coverage/out/tier2/state/, on the HOST via the bind mount -- not in a Docker
# volume, so it survives even a `docker volume rm`). Re-running this script after any
# interruption (Ctrl-C, Docker crash, workstation reboot) picks up exactly where it left
# off; nothing is redone.
#
# PARALLELISM: up to --parallel jobs run concurrently (default 4). This is safe because
# each job is a fully independent container -- its own writable filesystem layer (so the
# /src/build graft in tier2-worker.sh never collides with another job's), its own
# GCOV_PREFIX bucket keyed by run_id (no shared mutable state in the volume), and its own
# log/output file. Measured serially this pipeline was ~110s/job (275 jobs => ~5h);
# on this workstation's 16 vCPU that is CPU headroom left on the table, not a real
# constraint -- see tools/coverage/README.md's Tier-2 cost section for the before/after.
#
# Usage:
#   tools/coverage/tier2-run.sh [--jobs tools/coverage/out/tier2/jobs.json]
#                               [--image kicad-conformance/kicad-coverage:10.0.5]
#                               [--raw-volume kicad-coverage-raw-tier2]
#                               [--parallel N]  # default 4
#                               [--limit N]     # process at most N pending jobs, then stop
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

IMAGE="kicad-conformance/kicad-coverage:10.0.5"
JOBS_FILE="$REPO/tools/coverage/out/tier2/jobs.json"
RAW_VOLUME="kicad-coverage-raw-tier2"
LIMIT=0
# Default 1 (serial), not higher: measured on this workstation, --parallel 2 made two
# concurrent jobs each take LONGER than one job alone took serially (~109s), because the
# Docker Desktop VM's CPU is shared with whatever else is running in it -- concretely,
# two other agents' containers (owning runner/adapters and the engine-scope work) were
# also running full suite passes on kicad/kicad:10.0.5 at the same time, and this
# project's Docker Desktop has already crashed twice under sustained load. Serial is the
# considerate, predictable default; raise --parallel by hand only if you know the VM is
# otherwise idle.
PARALLEL=1

while [ $# -gt 0 ]; do
    case "$1" in
        --jobs)       JOBS_FILE="$2"; shift 2 ;;
        --image)      IMAGE="$2"; shift 2 ;;
        --raw-volume) RAW_VOLUME="$2"; shift 2 ;;
        --parallel)   PARALLEL="$2"; shift 2 ;;
        --limit)      LIMIT="$2"; shift 2 ;;
        -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

STATE_DIR="$REPO/tools/coverage/out/tier2/state"
LOG_DIR="$REPO/tools/coverage/out/tier2/logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"
docker volume create "$RAW_VOLUME" >/dev/null

win() { case "$(uname -s)" in MINGW*|MSYS*) cygpath -w "$1" ;; *) printf '%s' "$1" ;; esac; }

# Extract [run_id, kind, case_dir, pert_slug] tuples as TSV -- avoids needing jq or a
# python process per job just to read one record out of the jobs file.
TUPLES_FILE="$(mktemp)"
MSYS_NO_PATHCONV=1 docker run --rm -v "$(win "$REPO"):/work" -w /work kicad/kicad:10.0.5 \
    python3 -c '
import json
jobs = json.load(open("tools/coverage/out/tier2/jobs.json"))
for j in jobs:
    print("\t".join([j["run_id"], j["kind"], j["case_dir"], j["pert_slug"] or ""]))
' > "$TUPLES_FILE"

total=$(wc -l < "$TUPLES_FILE")
done_count=0
run_count=0
fail_count=0
start_ts=$(date -u +%s)

echo "Tier-2: $total jobs total, image=$IMAGE, volume=$RAW_VOLUME, parallel=$PARALLEL"

# run_one RUN_ID KIND CASE_DIR PERT_SLUG -- one job, one container, run in the
# background by the caller. Writes its own timing/result line so concurrent jobs never
# interleave a single echo mid-line.
run_one() {
    local run_id="$1" kind="$2" case_dir="$3" pert_slug="$4"
    local t0 t1 rc
    t0=$(date -u +%s)
    MSYS_NO_PATHCONV=1 docker run --rm \
        -v "$(win "$REPO"):/work" \
        -v "$RAW_VOLUME:/coverage/raw" \
        -w /work \
        "$IMAGE" \
        bash tools/coverage/tier2-worker.sh "$run_id" "$kind" "$case_dir" "$pert_slug" \
        > "$LOG_DIR/$run_id.container.log" 2>&1
    rc=$?
    t1=$(date -u +%s)
    if [ $rc -ne 0 ] || [ ! -f "$STATE_DIR/$run_id.done" ]; then
        echo "FAILED rc=$rc run_id=$run_id ($((t1-t0))s) -- see $LOG_DIR/$run_id.log"
    else
        echo "ok run_id=$run_id ($((t1-t0))s)"
    fi
}

declare -a pending_ids=() pending_kinds=() pending_cases=() pending_slugs=()
while IFS=$'\t' read -r run_id kind case_dir pert_slug; do
    if [ -f "$STATE_DIR/$run_id.done" ]; then
        done_count=$((done_count+1))
        continue
    fi
    pending_ids+=("$run_id"); pending_kinds+=("$kind")
    pending_cases+=("$case_dir"); pending_slugs+=("$pert_slug")
done < "$TUPLES_FILE"
rm -f "$TUPLES_FILE"

n_pending=${#pending_ids[@]}
echo "$done_count already done, $n_pending pending"
if [ "$LIMIT" != "0" ] && [ "$n_pending" -gt "$LIMIT" ]; then
    n_pending=$LIMIT
    echo "capped to --limit $LIMIT pending jobs this invocation"
fi

declare -a active_pids=()
idx=0
launched=0
while [ "$idx" -lt "$n_pending" ] || [ "${#active_pids[@]}" -gt 0 ]; do
    # Top up the pool while there's room and jobs left.
    while [ "${#active_pids[@]}" -lt "$PARALLEL" ] && [ "$idx" -lt "$n_pending" ]; do
        run_one "${pending_ids[$idx]}" "${pending_kinds[$idx]}" "${pending_cases[$idx]}" "${pending_slugs[$idx]}" &
        active_pids+=("$!")
        idx=$((idx+1))
        launched=$((launched+1))
    done
    if [ "${#active_pids[@]}" -eq 0 ]; then
        break
    fi
    # Wait for ANY one job to finish, then reap it (bash 5.2 supports `wait -n -p`).
    if wait -n -p finished_pid "${active_pids[@]}" 2>/dev/null; then :; fi
    new_active=()
    for pid in "${active_pids[@]}"; do
        if [ "$pid" != "${finished_pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
            new_active+=("$pid")
        fi
    done
    active_pids=("${new_active[@]}")
    run_count=$((run_count+1))
    echo "[$run_count/$n_pending pending this invocation, $((done_count+run_count))/$total overall]"
done

# Tally failures from the per-job container logs written this invocation (run_one's own
# echo already went to this script's stdout via the job's stdout, captured by whatever
# redirected THIS script -- see tier2-run.sh's own header comment for how it's invoked).
fail_count=0
for run_id in "${pending_ids[@]:0:$launched}"; do
    [ -f "$STATE_DIR/$run_id.done" ] || fail_count=$((fail_count+1))
done

elapsed=$(( $(date -u +%s) - start_ts ))
echo
echo "Tier-2 pass: $launched jobs attempted this invocation, $fail_count failed, "
echo "$done_count already-done jobs skipped, elapsed ${elapsed}s."
echo "Re-run this script to retry failures / continue past --limit; already-done jobs are skipped."
