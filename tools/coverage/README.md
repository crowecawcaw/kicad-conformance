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
| Compiler | GCC 14 (Debian trixie), `-O0 -g0 --coverage -fprofile-update=atomic` |
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

# 2. Run the suite against it; raw counters land in tools/coverage/out/raw
tools/coverage/run-suite.sh

# ...or a subset, with any runner arguments after `--`
tools/coverage/run-suite.sh -- suites/drc

# 3. The report is written automatically by step 2:
#      tools/coverage/out/report/html/index.html   browsable, line-by-line
#      tools/coverage/out/report/focus.json        per-subsystem rollup
#      tools/coverage/out/report/coverage.info     lcov tracefile
#      tools/coverage/out/report/summary.txt       plain text
```

Counters **accumulate** into `out/raw` across runs, which is what you want when
measuring several suite invocations together. Delete `out/raw` to start a clean
measurement.

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

**`-g0` is deliberate.** gcov does not read DWARF — it pairs the compile-time `.gcno`
files with the run-time `.gcda` files. Dropping debug info removes tens of GB of
object files and the multi-GB link peaks that come with them, at no cost to coverage
accuracy. `-O0` is kept, because that is what makes line and branch attribution match
the source a human reads.

**`-fprofile-update=atomic` is not optional here.** KiCad runs DRC, ERC and some
parsing on thread pools. With the default non-atomic counter updates, concurrent
increments race and are lost — the report would silently *under*-report coverage,
which is precisely the failure mode that would make us chase phantom gaps.

**`GCOV_PREFIX` keeps the data outside the container.** The `.gcno` files stay in the
image at `/src/build`; at run time `GCOV_PREFIX=/coverage/raw` with
`GCOV_PREFIX_STRIP=0` redirects `.gcda` writes into a bind mount that mirrors the
build tree, and `collect.sh` grafts it back before invoking `gcovr`.

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
| Compile + link, **2353** ninja edges | ~1.2 ninja edges/s sustained at `-j12` (measured over the first 1000). **Projected 35-60 min** for the full compile; the tail is the heavy `pcbnew`/`eeschema` TUs plus the `--coverage` links, which run slower than the average. |
| BuildKit cache after deps+source+partial compile | 13.2 GB (`docker system df`) |
| Suite run, stock `kicad/kicad:10.0.5` (baseline) | **44 s** for 8 cases / 22 checks |
| Suite run, instrumented | not yet measured — expect several times the 44 s baseline (see Limitations #3) |

The compile time above is a **projection from a measured rate**, not an observed
total: this recipe was validated up to and well past the halfway point of the compile
(1020/2353 edges, no errors), and the coordinator runs the build to completion. The
final image size is likewise unmeasured; budget on the order of 10-15 GB, since the
image must retain `/src/build` (the `.gcno` files and objects) for gcov to work.

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

4. **It is a different binary.** Same source tag and same distro dependency versions,
   but a different build type, and with wxPython scripting compiled out. Behavioural
   differences are unlikely but not impossible; if the suite reports a *result*
   difference between this image and `kicad/kicad:10.0.5`, trust the release image.

5. **Python scripting paths are not measured** (see `KICAD_SCRIPTING_WXPYTHON=OFF`).

6. **Branch coverage is noisier than line coverage** in C++: every call that can throw
   creates hidden branches. `collect.sh` passes `--exclude-throw-branches` and
   `--exclude-unreachable-branches` to suppress the worst of it, but treat branch
   percentages as indicative only.
