# Line coverage of KiCad itself

This directory builds KiCad **10.0.5** from source with GCC/gcov instrumentation and
runs the conformance suite against it, so we can see **which lines and branches of
KiCad our suite never executes**. It answers a question the docs cannot: not "what do
we claim to test" but "what does KiCad actually run when we test it".

Nothing outside `tools/coverage/` is modified. The instrumented `kicad-cli` is picked
up by `adapters/kicad.py` through the `KICAD_CLI` environment variable, which is first
in that adapter's discovery order (`KICAD_CLI` -> `PATH` -> per-OS install dirs), so
the adapter, the runner and the suites all run unchanged.

## What gets built

| | |
|---|---|
| Base image | `debian:trixie-slim` (Debian 13) |
| KiCad source | `https://gitlab.com/kicad/code/kicad.git`, tag `10.0.5` |
| Pinned commit | `18fb9289ff0efdca53c0352ed81a0973f0a6b58c` |
| Compiler | GCC 14 (Debian trixie), `-O0 --coverage -fprofile-update=atomic` (+ `-g1` — see below) |
| Instrumented | KiCad only. Every dependency is a distro package. |

`debian:trixie-slim` is not an arbitrary choice: the official `kicad/kicad:10.0.5`
runtime image reports `Debian GNU/Linux 13 (trixie)` in `/etc/os-release`, so building
on trixie links our instrumented binary against the *same versions* of OpenCASCADE,
wxWidgets 3.2, Boost, ngspice, nng, protobuf and libgit2 that upstream's own binary
uses. Behaviour differences between the instrumented build and the image the suite
normally runs against are therefore about as small as they can be made.

The Dockerfile pins the tag *and* verifies the resolved commit hash, so the build
fails loudly rather than silently measuring different code if a tag is ever moved.

## Usage

```bash
# 1. Build the instrumented image (long -- see "Cost" below).
tools/coverage/build.sh --jobs 4

# 2. Run the suite; raw counters land in the `kicad-coverage-raw` Docker volume.
#    --fresh discards previously accumulated counters.
tools/coverage/run-suite.sh --fresh

# ...or a subset, with any runner arguments after `--`
tools/coverage/run-suite.sh -- suites/drc

# 3. The report is written automatically by step 2:
#      tools/coverage/out/report/html/index.html   browsable, line-by-line
#      tools/coverage/out/report/focus.json        per-subsystem rollup
#      tools/coverage/out/report/summary.txt       plain text
#      tools/coverage/out/report/coverage.info     lcov tracefile (may be absent --
#                                                  see collect.sh's lcov timeout note)

# 4. Analysis. focus.json is per-FILE and per-ROUND; these four answer the questions
#    it cannot. Each exists because a figure in docs/COVERAGE.md had to be re-derived
#    by hand once, which is neither reproducible nor diffable.
python3 tools/coverage/compare.py OLD.json NEW.json --top 45 --file zone_filler
tools/coverage/funcs.sh erc.cpp                        # per-FUNCTION, from the volume
tools/coverage/funcs.sh --zero-only --min-lines 12 pcb_io_kicad_sexpr_parser.cpp
python3 tools/coverage/gaps.py NEW.json corrected      # the docs/COVERAGE.md §3a table
python3 tools/coverage/gaps.py NEW.json dead --min-lines 60
python3 tools/coverage/subdir.py "pcbnew/pcb_io/kicad_sexpr/" OLD.json NEW.json
```

`compare.py`, `gaps.py` and `subdir.py` read only gcovr JSON and need nothing but a
python3 -- this workstation has none, so they are run as
`docker run --rm -v "$PWD":/work -w /work kicad/kicad:10.0.5 python3 …`. `funcs.sh` needs
the coverage image and the raw-counter volume, and delegates parsing to `gcovfuncs.py`
rather than an inline awk/python one-liner: quoting a parser through `docker run bash -c`
is a reliable way to get an empty table that reads as "no coverage".

**Archive the previous round's `coverage.json` before running a new one** --
`run-suite.sh` overwrites `out/report/`, and without a *before* file `compare.py` has
nothing to diff. `out/round1/` and `out/round2*-coverage.json` are the retained ones.

Counters **accumulate** in the volume across runs, which is what you want when measuring
several suite invocations together. Pass `--fresh` (or `docker volume rm
kicad-coverage-raw`) to start a clean measurement.

**The latest measurement, its interpretation and the gap backlog it produced live in
[`docs/COVERAGE.md`](../../docs/COVERAGE.md).** Read that before reading a number here.

`verify-builddeps.sh` re-derives Debian's `Build-Depends` for the `kicad` source
package and diffs it against the explicit package list in the Dockerfile, so the
dependency shortcut (see below) is checkable rather than a matter of trust.

## Design notes

**Dependencies come from the distro, not from source.** `apt-get build-dep kicad`
resolves cleanly on trixie (verified), but it pulls **687 packages** — roughly two
thirds of which are a documentation toolchain (`texlive-lang-*`, `dblatex`,
`docbook-*`, `doxygen`, `asciidoctor`, `xmlto`, `po4a`, `imagemagick`) needed only to
build Debian's *doc* packages, contributing nothing to `kicad-cli` and costing several
GB. The Dockerfile installs that Build-Depends list minus the documentation toolchain,
plus the extras KiCad 10 needs. Trixie ships KiCad 9.0.2, so its build-deps are the
9.x set; the delta to 10.0.5 is what `verify-builddeps.sh` exists to keep visible.

Three of those extras are not in Debian's list at all and were each found by a CMake
configure failure — worth knowing if you ever retarget this at another distro:

| Package | Why |
|---|---|
| `libspnav-dev` | `CMakeLists.txt:835` calls `find_package( SPNAV REQUIRED )` unconditionally on non-Apple UNIX. A 3Dconnexion space mouse is pure GUI and useless to `kicad-cli`, but there is no option to disable it. |
| `libwxgtk-webview3.2-dev` | `CMakeLists.txt:1096` requires the wx `webview` component; Debian splits it out and `libwxgtk3.2-dev` does *not* depend on it. |
| `libpoppler-private-dev` (+ `-cpp-`, `-glib-`) | `libs/kiplatform/CMakeLists.txt:93` requires Poppler for PDF printing; its Core component needs poppler's internal headers. |

**The coverage flags must ride in `CMAKE_CXX_FLAGS`, never `CMAKE_CXX_FLAGS_DEBUG`.**
KiCad's `CMakeLists.txt:441-446` does an unconditional non-cache
`set( CMAKE_CXX_FLAGS_DEBUG "-g3 -ggdb3" )`, which silently clobbers anything passed as
`-DCMAKE_CXX_FLAGS_DEBUG=…` — the *cache* keeps showing the coverage flags while
`build.ninja` gets none, and the image builds happily with **zero `.gcno` files** in it.
That exact failure shipped once (see `docs/COVERAGE.md` §7). `CMAKE_CXX_FLAGS` is only
ever appended to by KiCad, so the flags survive; the Dockerfile now also asserts
`profile-arcs` is present in `build.ninja` after configure and that the compile emitted
>1000 `.gcno` files, so it can never regress quietly again.

**Debug info is minimised, not disabled.** `-g0` cannot win, because
`${CMAKE_CXX_FLAGS_DEBUG}` is emitted *after* `${CMAKE_CXX_FLAGS}` and a later `-g3`
takes precedence. `KICAD_BUILD_SMALL_DEBUG_FILES=ON` is used instead, which is KiCad's
own switch for `-g1 -ggdb1` — enough to keep the object tree small (gcov does not read
DWARF at all; it pairs `.gcno` with `.gcda`) while still giving readable backtraces.
`-O0` is kept, because that is what makes line and branch attribution match the source a
human reads.

**The target list must include `cvpcb_kiface`, and leaving it out is silent.** `kicad-cli`
dlopen()s kifaces through KIWAY, and the obvious closure — `kicad-cli pcbnew_kiface
eeschema_kiface` — is one short: `eeschema/eeschema_jobs_handler.cpp:1353` passes
`m_kiway->KiFACE( KIWAY::FACE_CVPCB )` into `ERC_TESTER::RunTests` **unconditionally**,
because cvpcb owns the footprint-link tester (`erc.cpp:1816`). Without `_cvpcb.kiface`,
`KIWAY::KiFACE()` throws `IO_ERROR` (`kiway.cpp:284`) before any ERC test runs, every
`sch erc` invocation exits 255, and `eeschema/erc/**` reports ~5% however many ERC cases
the suite has. That shipped once (see `docs/COVERAGE.md` §2c and §8.4: it cost a whole
measurement round of ERC data and presented as 19 `CRASH` verdicts, i.e. as a *suite*
problem). The Dockerfile now builds and installs it and asserts `test -x` on it. Cost:
the ninja edge count went from **2009 to 2032**, i.e. **23 extra edges** (22 of them
`Building CXX object cvpcb/…`, plus the link), counted in the build log.

**`-fprofile-update=atomic` is not optional here.** KiCad runs DRC, ERC and some
parsing on thread pools. With the default non-atomic counter updates, concurrent
increments race and are lost — the report would silently *under*-report coverage,
which is precisely the failure mode that would make us chase phantom gaps.

**`GCOV_PREFIX` keeps the data outside the container.** The `.gcno` files stay in the
image at `/src/build`; at run time `GCOV_PREFIX=/coverage/raw` with
`GCOV_PREFIX_STRIP=0` redirects `.gcda` writes into a mount that mirrors the
build tree, and `collect.sh` grafts it back before invoking `gcovr`.

That mount is a **named Docker volume, not a host bind mount**, and the difference is
not cosmetic: libgcov dumps ~1900 small `.gcda` files at *every* process exit, and on
Docker Desktop for Windows a bind mount crosses the VM/host boundary. Measured on this
workstation, one `kicad-cli version` took **3.39 s** dumping to a bind mount versus
**0.32 s** to the VM's own filesystem — a >10x tax on every invocation in the suite. See
`run-suite.sh`'s header.

This mechanism was verified independently of the KiCad build, by compiling a small
program in the same image with the same flags: the `.gcno` landed next to the object,
running the binary under `GCOV_PREFIX=/coverage/raw GCOV_PREFIX_STRIP=0` produced
`/coverage/raw/<full object path>/t.gcda`, and after the graft `gcovr` reported the
deliberately-uncalled function as a missing line. So the collection path is known
good; if a real run reports nothing, the fault is upstream of it (wrong binary, or
`kicad-cli` never actually invoked).

**Disabled at configure time:** upstream's own QA tests (`KICAD_BUILD_QA_TESTS=OFF`
— not our suite, and their source would pollute the denominator) and wxPython
scripting (`KICAD_SCRIPTING_WXPYTHON=OFF` — the SWIG-generated `pcbnew_wrap.cxx` is a
peak-RAM hazard on a small builder and `kicad-cli` does not use it).

## Cost

Measured on this workstation: Docker Desktop / WSL2 Linux engine, 16 vCPU, 5.75 GiB
VM RAM, `--build-arg BUILD_JOBS=6`.

| Phase | Measured |
|---|---|
| `apt-get install` of the dependency set (~510 packages) | ~2.5 min cold, ~40 s with the BuildKit apt cache warm |
| Shallow fetch of tag `10.0.5` | ~60 s |
| CMake configure | 9 s (`Configuring done (8.0s)`, `Generating done (1.0s)`) |
| Compile + link, **2009** ninja edges (the reduced target set) | **24 min 29 s measured** at `-j8`, cold ccache, emitting **1912** `.gcno` files. Image export/unpack adds ~4 min. |
| Rebuild after changing `KICAD_TARGETS` (adding `cvpcb_kiface`, **2032** edges), ccache warm | **29 min 55 s measured** 2026-08-04 (`06:52:51Z` → `07:22:46Z`, `build succeeded on attempt 1`), of which **4 min 36 s** is image export/unpack. Emits **1934** `.gcno`. The ccache replay is not free — it ran at ~3.6 edges/s early and ~1.9 edges/s through the pcbnew/eeschema region. |
| Final image | **17.1 GB** (`docker images`) — it must retain `/src/build`, since gcov pairs each `.gcda` with the `.gcno` next to its object. |
| BuildKit cache after a full build | 26.2 GB (`docker system df`) |
| Suite run, stock `kicad/kicad:10.0.5` (baseline) | **44 s** for 8 cases / 22 checks |
| Suite run, instrumented, 77 cases / 199 checks | **4 min 46 s**, measured 2026-08-03 (`docs/COVERAGE.md` §2). Counters in a Docker volume; on a Windows bind mount the same run projected to ~2.5 h. |
| Suite run, instrumented, **133 cases / 424 checks** | **≤ 11 min 54 s**, measured 2026-08-04 — see `docs/COVERAGE.md`'s note that this is a polled upper bound, not a stopwatch reading. |
| `collect.sh` (gcovr over 1850-1886 profiles) | **~9 min to `coverage.json`** (2026-08-04: gcovr finished 07:45, collect started 07:35), plus **lcov**, which is the long pole — `focus.json` did not land until 07:56. Set `COVERAGE_SKIP_LCOV=1` if you do not need `coverage.info`. |

Every figure in the table above is now an **observed total** from the 2026-08-03 build
(`build.sh --jobs 8`, `build succeeded on attempt 1`), not a projection. Note that the
edge count dropped from 2353 to 2009 once the reduced target set landed, and that the
whole compile is one BuildKit layer — see "Resuming" below.

**Parallelism.** `-j6` was chosen defensively but the measurement says it is too
conservative: peak container memory during the compile was **1.1 GiB of 5.75 GiB**,
while CPU sat at ~900% of a possible 1600%. This build is CPU-bound, not RAM-bound —
`--jobs 12` should be safe here and materially faster. Raise the VM's memory in
`%USERPROFILE%\.wslconfig` before going much beyond that.

**Resuming.** The compile is a single BuildKit layer, so an interrupted build loses
the layer — but not the work: `/ccache` is a persistent cache mount, so a re-run
replays already-compiled objects at cache-hit speed rather than recompiling them.

## Limitations — read this before quoting a number

1. **The global percentage is meaningless and will look terrible.** Most of KiCad is
   GUI: dialogs, canvas, tools, widgets, the six applications. A `kicad-cli` run
   cannot reach any of it. Expect a low global line-coverage figure and do not treat
   it as a finding. The useful signal is coverage **within** the file-format parsers
   and writers, the exporters, and the DRC/ERC engines — which is why `collect.sh`
   emits `focus.json` bucketed by subsystem. Read those buckets.

2. **Uncovered does not mean untestable, and covered does not mean tested.** gcov
   records that a line *executed*, not that the suite *asserted* anything about its
   effect. A parser line executed while reading a fixture is "covered" even if the
   suite never checks the value it produced. Coverage here is a gap-finder, not a
   quality score.

3. **The instrumented binary is several times slower.** `-O0` plus per-arc atomic
   counter updates typically costs a large constant factor versus the release build.
   Long suites will take proportionally longer, and any wall-clock or timeout
   assertions in the suite may behave differently than against the release image.
   Do not use this image for timing-sensitive results.

4. **It is a different binary, and this is NOT hypothetical — it changed three observable
   behaviours across the 2026-08-03 and 2026-08-04 runs** (`docs/COVERAGE.md` §2). The
   third, and worst, is not in the list below because it was an image-recipe bug rather
   than a Debug-build artifact: `sch erc` exited 255 on every schematic because the
   reduced target set omitted `_cvpcb.kiface` (see the target-list note above and
   `docs/COVERAGE.md` §2c). It is fixed; the point that survives is that a coverage
   *number* can be zero for a reason that has nothing to do with the suite, so a
   subsystem reading ~0% deserves one direct `kicad-cli` invocation before it is written
   up as a gap. Same source tag and same
   distro dependency versions, but a Debug build with wxASSERT live, no stock
   symbol/footprint libraries (so every invocation prints a
   `LIBRARY_MANAGER::LoadGlobalTables` assert backtrace), no wxPython, and
   `-ftrivial-auto-var-init=pattern` rather than the release fill. Measured
   consequences: **(a)** parse-failure messages such as `Failed to load board: …`
   are not printed at all, though the exit code is unchanged, which fails all 14
   `rejects-*` cases; **(b)** an uninitialised `int` in
   `pcbnew/exporters/export_d356.cpp`'s via path reads as `0xFEFEFEFF`, which fails the
   6 board cases that contain a via. If this image and `kicad/kicad:10.0.5` disagree on
   a *result*, **trust the release image** — but check whether the disagreement is
   telling you about a real KiCad bug, as (b) was.

5. **Python scripting paths are not measured** (see `KICAD_SCRIPTING_WXPYTHON=OFF`).

6. **Branch coverage is noisier than line coverage** in C++: every call that can throw
   creates hidden branches. `collect.sh` passes `--exclude-throw-branches` and
   `--exclude-unreachable-branches` to suppress the worst of it, but treat branch
   percentages as indicative only.
