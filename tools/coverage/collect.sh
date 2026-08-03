#!/usr/bin/env bash
# Turn the raw .gcda counters produced by a suite run into an HTML report plus a
# machine-readable summary. Runs INSIDE the coverage image (installed as
# /usr/local/bin/collect-coverage).
#
#   collect-coverage [OUTDIR]      default OUTDIR=/coverage/report
#
# Inputs:  /coverage/raw   -- .gcda tree written by the instrumented kicad-cli
#          /src/build      -- .gcno tree baked into the image at compile time
# Outputs: $OUTDIR/html/index.html   browsable report
#          $OUTDIR/coverage.json     gcovr JSON summary (per file)
#          $OUTDIR/coverage.info     lcov tracefile (for genhtml / CI tooling)
#          $OUTDIR/summary.txt       plain-text table
#          $OUTDIR/focus.json        per-subsystem rollup (the number that matters)
set -euo pipefail

OUTDIR="${1:-/coverage/report}"
BUILD_DIR="${KICAD_BUILD_DIR:-/src/build}"
SRC_DIR="${KICAD_SRC_DIR:-/src/kicad}"
RAW_DIR="${GCOV_PREFIX:-/coverage/raw}"

# gcov insists on finding each .gcda next to its .gcno. GCOV_PREFIX wrote them to
# $RAW_DIR + <absolute object path>, so the mirrored tree grafts straight back on.
graft="${RAW_DIR}${BUILD_DIR}"
if [ ! -d "$graft" ]; then
    echo "ERROR: no raw profile data at $graft" >&2
    echo "       Did the suite run with GCOV_PREFIX=$RAW_DIR and -v <hostdir>:/coverage ?" >&2
    exit 1
fi
n_gcda=$(find "$graft" -name '*.gcda' | wc -l)
echo "grafting $n_gcda .gcda files from $graft -> $BUILD_DIR"
[ "$n_gcda" -gt 0 ] || { echo "ERROR: profile tree exists but is empty -- kicad-cli never ran, or ran a binary that was not the instrumented one." >&2; exit 1; }
cp -a "$graft/." "$BUILD_DIR/"

mkdir -p "$OUTDIR/html"

# --filter keeps only KiCad's own sources; everything under /usr (system headers,
# Boost, wxWidgets, OCC) is outside --root and drops out anyway, but the excludes
# are explicit so the intent survives a gcovr upgrade.
#   thirdparty/  vendored libraries -- not KiCad code, not our problem
#   qa/          upstream's own tests
#   */build/     CMake/protobuf/SWIG generated sources
GCOVR_ARGS=(
    --root "$SRC_DIR"
    --object-directory "$BUILD_DIR"
    --filter "$SRC_DIR/"
    --exclude '.*/thirdparty/.*'
    --exclude '.*/qa/.*'
    --exclude '.*/build/.*'
    --exclude '.*/CMakeFiles/.*'
    --exclude '.*_wrap\.cxx'
    --exclude '.*\.pb\.(cc|h)'
    --gcov-ignore-parse-errors
    --exclude-unreachable-branches
    --exclude-throw-branches
    --print-summary
)
# One gcov subprocess per core is the bulk of the wall time on a tree this size.
if gcovr --help 2>/dev/null | grep -q -- '-j '; then
    GCOVR_ARGS+=(-j "$(nproc)")
fi

echo "running gcovr (this walks ~$n_gcda profiles; expect a few minutes)..."
# Every output gets an explicit filename -- gcovr's bare `-o` is positional-ish and
# gets ambiguous once three formats are requested in one invocation.
gcovr "${GCOVR_ARGS[@]}" \
    --html-details "$OUTDIR/html/index.html" \
    --json-summary "$OUTDIR/coverage.json" --json-summary-pretty \
    --txt "$OUTDIR/summary.txt" \
    | tee "$OUTDIR/gcovr-stdout.txt"

# lcov tracefile: same counters, the format CI and diff-coverage tooling expects.
# Debian trixie ships gcovr 7.2, which has no --lcov output (that arrived in gcovr 8),
# so this goes through lcov itself. lcov 2.x is strict about inconsistencies that are
# routine in a large C++ tree, hence the --ignore-errors list; the whole block is
# non-fatal because the gcovr outputs above are the primary artifacts.
if command -v lcov >/dev/null 2>&1; then
    lcov --capture \
         --directory "$BUILD_DIR" \
         --base-directory "$SRC_DIR" \
         --output-file "$OUTDIR/coverage.raw.info" \
         --rc geninfo_unexecuted_blocks=1 \
         --ignore-errors mismatch,negative,unused,source,gcov,empty,inconsistent \
         >/dev/null 2>"$OUTDIR/lcov-stderr.txt" \
    && lcov --remove "$OUTDIR/coverage.raw.info" \
         '/usr/*' '*/thirdparty/*' '*/qa/*' '*/build/*' '*_wrap.cxx' '*.pb.cc' \
         --output-file "$OUTDIR/coverage.info" \
         --ignore-errors unused,empty,inconsistent \
         >/dev/null 2>>"$OUTDIR/lcov-stderr.txt" \
    && rm -f "$OUTDIR/coverage.raw.info" \
    || echo "warning: lcov tracefile generation failed (non-fatal; see $OUTDIR/lcov-stderr.txt)" >&2
fi

# Roll the per-file numbers up to subsystem level. The global percentage is close to
# meaningless for a CLI-only run (see README "Limitations"); these buckets are the
# actual signal -- they are the parsers, exporters and rule engines the conformance
# suite claims to cover.
python3 - "$OUTDIR/coverage.json" "$OUTDIR/focus.json" <<'PY'
import json, sys, collections

src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src))

# Ordered: FIRST pattern that matches a path wins, so the GUI bucket must stay last
# (eeschema/dialogs would otherwise be swallowed by an "eeschema/" style pattern).
# These directory names were checked against the actual 10.0.5 tree -- several of the
# obvious guesses are KiCad 9 names that no longer exist (pcbnew/plugins became
# pcbnew/pcb_io, eeschema/sch_plugins became eeschema/sch_io).
BUCKETS = [
    ("io/board",      ("pcbnew/pcb_io",)),
    ("io/schematic",  ("eeschema/sch_io",)),
    ("io/common",     ("common/io", "libs/sexpr", "common/drawing_sheet")),
    ("drc",           ("pcbnew/drc",)),
    ("erc",           ("eeschema/erc",)),
    ("netlist",       ("eeschema/netlist_exporters", "common/netlist_reader",
                       "pcbnew/netlist_reader")),
    ("connectivity",  ("pcbnew/connectivity", "pcbnew/ratsnest")),
    ("export/plot",   ("pcbnew/exporters", "common/plotters", "eeschema/printing")),
    ("cli/jobs",      ("kicad/cli", "common/jobs", "jobs_handler")),
    ("geometry",      ("libs/kimath", "libs/core")),
    ("gui",           ("/dialogs/", "/widgets/", "/tools/", "common/gal",
                       "preview_items", "3d-viewer")),
]

roll = collections.defaultdict(lambda: {"lines": 0, "covered": 0, "files": 0})
for f in data.get("files", []):
    p = f["filename"].replace("\\", "/")
    total = f.get("line_total", 0)
    covered = f.get("line_covered", 0)
    name = "other"
    for bucket, pats in BUCKETS:
        if any(pat in p for pat in pats):
            name = bucket
            break
    b = roll[name]
    b["lines"] += total
    b["covered"] += covered
    b["files"] += 1

out = {}
for name, b in sorted(roll.items()):
    pct = (100.0 * b["covered"] / b["lines"]) if b["lines"] else 0.0
    out[name] = {**b, "percent": round(pct, 2)}

overall = {
    "line_percent": data.get("line_percent"),
    "branch_percent": data.get("branch_percent"),
    "line_total": data.get("line_total"),
    "line_covered": data.get("line_covered"),
}
json.dump({"overall": overall, "buckets": out}, open(dst, "w"), indent=2)

print("\n=== coverage by subsystem (line %) ===")
print(f"{'bucket':<16}{'files':>7}{'lines':>10}{'covered':>10}{'pct':>8}")
for name, b in sorted(out.items(), key=lambda kv: -kv[1]["lines"]):
    print(f"{name:<16}{b['files']:>7}{b['lines']:>10}{b['covered']:>10}{b['percent']:>7.1f}%")
print(f"\nGLOBAL line coverage: {overall['line_percent']}%  "
      f"({overall['line_covered']}/{overall['line_total']})")
print("NOTE: the global number is dominated by GUI code no CLI run can reach. "
      "Read the io/*, drc, erc and export buckets instead.")
PY

echo
echo "report written to $OUTDIR"
ls -la "$OUTDIR"
