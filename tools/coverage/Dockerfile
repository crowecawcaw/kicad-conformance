# syntax=docker/dockerfile:1.7
#
# kicad-conformance :: gcov-instrumented KiCad build
# --------------------------------------------------
# Builds KiCad from source with GCC coverage instrumentation so that running the
# conformance suite produces line/branch coverage OF KICAD ITSELF.
#
# Base choice: debian:trixie-slim. This is the same distro release the official
# `kicad/kicad:10.0.5` runtime image is built on (verified: /etc/os-release in that
# image reports Debian GNU/Linux 13 trixie), so every heavy dependency -- OpenCASCADE,
# wxWidgets 3.2, Boost, ngspice, nng, protobuf, libgit2 -- is the *same version* the
# upstream binary was linked against, and all of them come from the distro as
# prebuilt packages. Nothing but KiCad itself is compiled here, and only KiCad
# itself is instrumented.
#
# See README.md in this directory for timings, sizes and limitations.

ARG BASE_IMAGE=debian:trixie-slim
# Named stages exist so the dependency and source layers can be materialised on their
# own -- `docker build --target source` gives you an image to iterate cmake in without
# paying for the compile. They are all one linear chain, so nothing is built twice.
FROM ${BASE_IMAGE} AS deps

# Pin exactly what is being measured. 10.0.5 is an annotated tag on the upstream
# repo; the peeled commit is recorded here so the build is reproducible even if a
# tag were ever moved.
ARG KICAD_GIT_URL=https://gitlab.com/kicad/code/kicad.git
ARG KICAD_TAG=10.0.5
ARG KICAD_COMMIT=18fb9289ff0efdca53c0352ed81a0973f0a6b58c

ENV DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# 1. Toolchain + KiCad's build dependencies.
#
# The Debian source package `kicad` (9.0.2 in trixie) declares the full
# Build-Depends set; `apt-get build-dep kicad` installs it verbatim. We do NOT use
# build-dep directly because ~two thirds of that set (texlive-lang-*, dblatex,
# docbook-*, doxygen, asciidoctor, xmlto, po4a, imagemagick) exists only to build
# the Debian *documentation* packages: 687 packages / several GB for zero effect on
# kicad-cli. The list below is that Build-Depends set with the documentation
# toolchain removed, plus the handful of extras KiCad 10 needs on top of 9.
# `tools/coverage/verify-builddeps.sh` re-derives the upstream list and diffs it
# against this one, so drift is detectable.
#
# THREE packages here are NOT in trixie's Build-Depends and were found the hard way,
# by CMake configure failing. Trixie packages KiCad 9.0.2; these are 10.x additions:
#   libspnav-dev             CMakeLists.txt:835 does `find_package( SPNAV REQUIRED )`
#                            unconditionally on non-Apple UNIX -- there is no option
#                            to switch it off, even though a 3Dconnexion space mouse
#                            is pure GUI and irrelevant to kicad-cli.
#   libwxgtk-webview3.2-dev  CMakeLists.txt:1096 requires the wxWidgets `webview`
#                            component; Debian splits it into its own package and
#                            libwxgtk3.2-dev does NOT depend on it.
#   libpoppler-*-dev         libs/kiplatform/CMakeLists.txt:93 does
#                            `find_package( Poppler REQUIRED )` on GTK builds (PDF
#                            printing). cmake/FindPoppler.cmake's Core component wants
#                            poppler's own headers, which Debian keeps in
#                            libpoppler-private-dev, so all three are needed.
#   libpixman-1-dev          CMakeLists.txt:845 `find_package( Pixman 0.30 REQUIRED )`
#                            (transitively present via libcairo2-dev, listed
#                            explicitly so it cannot silently disappear).
# ---------------------------------------------------------------------------
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        ccache \
        cmake \
        gettext \
        git \
        ninja-build \
        pkgconf \
        swig \
        \
        libboost-date-time-dev \
        libboost-dev \
        libboost-filesystem-dev \
        libboost-iostreams-dev \
        libboost-locale-dev \
        libboost-program-options-dev \
        libboost-regex-dev \
        libboost-system-dev \
        libboost-test-dev \
        libboost-thread-dev \
        libbz2-dev \
        libcairo2-dev \
        libcurl4-openssl-dev \
        libfontconfig-dev \
        libfreetype-dev \
        libgit2-dev \
        libglew-dev \
        libglm-dev \
        libgtk-3-dev \
        libharfbuzz-dev \
        libngspice0-dev \
        libnng-dev \
        libocct-data-exchange-dev \
        libpixman-1-dev \
        libpoppler-cpp-dev \
        libpoppler-glib-dev \
        libpoppler-private-dev \
        libprotobuf-dev \
        libprotoc-dev \
        libpython3-dev \
        libsecret-1-dev \
        libspnav-dev \
        libssl-dev \
        libwxgtk-webview3.2-dev \
        libwxgtk3.2-dev \
        libzstd-dev \
        protobuf-compiler \
        python3-all \
        python3-dev \
        unixodbc-dev \
        zlib1g-dev \
        \
        fontconfig \
        fonts-dejavu-core \
        fonts-freefont-ttf \
        \
        gcovr \
        lcov \
        python3 \
        xz-utils \
    ; \
    gcov --version | head -1

# ---------------------------------------------------------------------------
# 2. Source. Shallow fetch of the pinned tag only (~250 MB vs ~2 GB full history).
#    Isolated in its own layer so editing the cmake flags below does not refetch.
#
#    The refspec deliberately writes a real local tag (`...:refs/tags/${TAG}`) rather
#    than just checking out FETCH_HEAD: KiCad derives its version string with
#    `git describe`, which on a tagless shallow checkout fails with "No names found"
#    and silently produces a binary that misreports its own version.
# ---------------------------------------------------------------------------
FROM deps AS source
WORKDIR /src
RUN set -eux; \
    git init kicad; \
    cd kicad; \
    git remote add origin "${KICAD_GIT_URL}"; \
    git fetch --depth 1 origin "refs/tags/${KICAD_TAG}:refs/tags/${KICAD_TAG}"; \
    git checkout -q "refs/tags/${KICAD_TAG}"; \
    test "$(git rev-parse HEAD)" = "${KICAD_COMMIT}" \
        || { echo "FATAL: tag ${KICAD_TAG} resolved to $(git rev-parse HEAD), expected ${KICAD_COMMIT}"; exit 1; }; \
    git log -1 --format='source: %H %d %ci'

# ---------------------------------------------------------------------------
# 3. Configure.
#
# Coverage flags:
#   -fprofile-arcs -ftest-coverage  == --coverage; emits .gcno at compile time and
#                                      .gcda at run time.
#   -fprofile-update=atomic         KiCad runs DRC/ERC and parsing on thread pools.
#                                      Without atomic counter updates, concurrent
#                                      increments are lost and coverage silently
#                                      under-reports. Costs some speed; correctness
#                                      is the whole point here.
#   -O0                             no inlining => line/branch mapping matches the
#                                      source the way a reader expects.
#   -g0                             DEBUG INFO IS DELIBERATELY OFF. gcov does not use
#                                      DWARF at all (it uses the .gcno files), and a
#                                      full -g build of KiCad costs tens of GB of
#                                      object files plus multi-GB link peaks. This is
#                                      the single biggest disk/RAM saving here. Add
#                                      -g back only if you need to debug the build.
#
# CMAKE_BUILD_TYPE=Debug selects the *_DEBUG flag variables we override above.
# KICAD_STDLIB_DEBUG / _LIGHT_DEBUG must stay OFF: they define _GLIBCXX_DEBUG, which
# changes libstdc++ ABI and would not match the distro-built wxWidgets/OCC.
#
# Disabled features and why (each one shrinks the build without touching anything
# the conformance suite exercises):
#   KICAD_BUILD_QA_TESTS=OFF   upstream's own unit tests -- not our suite, and their
#                              source would pollute the coverage denominator.
#   KICAD_SCRIPTING_WXPYTHON=OFF  the SWIG-generated pcbnew_wrap.cxx is one of the
#                              largest translation units in the project and a peak-RAM
#                              hazard on a 6 GB builder. kicad-cli does not use it.
#                              (Caveat: Python scripting coverage is therefore out of
#                              scope -- documented in README.md.)
# ---------------------------------------------------------------------------
FROM source AS build
ENV CCACHE_DIR=/ccache
ARG COVERAGE_FLAGS="-O0 -g0 -fprofile-arcs -ftest-coverage -fprofile-update=atomic"
ARG KICAD_PREFIX=/opt/kicad-cov

RUN --mount=type=cache,target=/ccache,sharing=locked \
    set -eux; \
    cmake -S /src/kicad -B /src/build -G Ninja \
        -DCMAKE_BUILD_TYPE=Debug \
        -DCMAKE_INSTALL_PREFIX="${KICAD_PREFIX}" \
        -DCMAKE_C_FLAGS_DEBUG="${COVERAGE_FLAGS}" \
        -DCMAKE_CXX_FLAGS_DEBUG="${COVERAGE_FLAGS}" \
        -DCMAKE_EXE_LINKER_FLAGS="--coverage" \
        -DCMAKE_SHARED_LINKER_FLAGS="--coverage" \
        -DCMAKE_MODULE_LINKER_FLAGS="--coverage" \
        -DCMAKE_C_COMPILER_LAUNCHER=ccache \
        -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
        -DKICAD_BUILD_QA_TESTS=OFF \
        -DKICAD_SCRIPTING_WXPYTHON=OFF \
        -DKICAD_STDLIB_DEBUG=OFF \
        -DKICAD_STDLIB_LIGHT_DEBUG=OFF \
        -DKICAD_USE_VALGRIND=OFF \
        -DKICAD_SANITIZE_ADDRESS=OFF \
        -DKICAD_SANITIZE_THREADS=OFF \
    ; \
    echo '=== configure OK ==='; \
    grep -E '^(CMAKE_CXX_FLAGS_DEBUG|CMAKE_BUILD_TYPE):' /src/build/CMakeCache.txt

# ---------------------------------------------------------------------------
# 4. Build + install -- MINIMAL TARGET SET, not the default `all`.
#
# We only need kicad-cli. Its own CMakeLists.txt (kicad/CMakeLists.txt) links it
# against `common`/`gal`/`kicommon`/`core`/etc. -- never against pcbnew or eeschema
# directly -- because pcbnew and eeschema are KIFACEs: shared modules
# (`_pcbnew.kiface`, `_eeschema.kiface`) that kicad-cli dlopen()s at runtime via
# KIWAY (see common/kiway.cpp). `sym`/`fp` upgrade also route through these two
# kifaces. So the target set that actually matters is:
#     kicad-cli        the executable (kicad/CMakeLists.txt:120)
#     pcbnew_kiface     -> _pcbnew.kiface   (pcbnew/CMakeLists.txt:854)
#     eeschema_kiface   -> _eeschema.kiface (eeschema/CMakeLists.txt:639)
#
# Confirmed by reading the CMake graph, not guessed:
#   - `add_dependencies( pcbnew pcbnew_kiface )` / `add_dependencies( eeschema
#     eeschema_kiface )` point FROM the GUI executable TO the kiface, never the
#     reverse -- so asking ninja for the kiface targets does not pull in the GUI
#     apps (kicad/pcbnew/eeschema/gerbview/cvpcb/pcb_calculator/pl_editor/
#     bitmap2component) or their dialogs.
#   - `pnsrouter_qa` (a QA-only static lib pulled into the default `all` target
#     regardless of KICAD_BUILD_QA_TESTS, and the thing edge 1043/2353 was stuck
#     on in the first failed attempt) is not in PCBNEW_KIFACE_LIBRARIES, so it is
#     never built at all with this target list.
#   - Verified with `ninja -n kicad-cli pcbnew_kiface eeschema_kiface`: 2010 edges
#     vs 2354 for the default `all` target (~15% smaller). The saving is modest,
#     not the ~60% one might hope for, because pcbnew_kiface's own dependency
#     closure (3d-viewer, connectivity, every pcb_io/sch_io format plugin, the
#     SWIG-generated pcbnew_wrap.cxx -- KiCad 10 has no build switch to omit that
#     one) is most of the historically-slow part of the graph anyway. What it
#     reliably skips is the kicad GUI shell, the four standalone GUI apps, and QA.
#
# -j is bounded by the builder VM's RAM, not by its core count. Measured on this
# workstation (16 vCPU / 5.75 GiB VM):
#   -j6   ~1.4 edges/s, container peak 1.1 GiB  -- lots of idle cores
#   -j12  ~1.0 edges/s in the heavy pcbnew/eeschema region, MemAvailable down to
#         1.85 GiB of 6.0 GiB -- no swapping, but not much headroom either
# 8 is the balanced default. Going higher is only worth it after raising the VM's
# memory in %USERPROFILE%\.wslconfig; an OOM kill part-way through costs more than
# the parallelism buys. With the smaller target set above, lower -j (e.g. 4-6) is
# also a reasonable choice if BuildKit's RPC connection turns out to be the more
# fragile constraint than raw CPU time -- fewer edges in flight per unit wall time
# means fewer surprises if the daemon hiccups, at the cost of a slower build.
#
# The ccache cache mount is what makes an interrupted build cheap to resume: BuildKit
# throws away the partial layer, but the compiled objects are still in /ccache, so a
# retry replays them at cache-hit speed instead of recompiling. This RUN is
# deliberately narrowed to JUST the compile+link (no install, no stats/du) so a
# retry's ccache replay is not followed by unrelated work before the layer can
# commit.
# ---------------------------------------------------------------------------
ARG BUILD_JOBS=8
ARG KICAD_TARGETS="kicad-cli pcbnew_kiface eeschema_kiface"
RUN --mount=type=cache,target=/ccache,sharing=locked \
    set -eux; \
    ccache --zero-stats; \
    cmake --build /src/build --parallel "${BUILD_JOBS}" -- ${KICAD_TARGETS}; \
    ccache --show-stats | head -20

# ---------------------------------------------------------------------------
# 4b. Install -- manual, not `cmake --install`.
#
# `cmake --install` walks EVERY install() rule in the project. kicad-cli and both
# kifaces share COMPONENT "binary" with kicad/pcbnew/eeschema/gerbview/cvpcb/
# pcb_calculator/pl_editor/bitmap2component -- none of which were built above --
# so `cmake --install` (even with `--component binary`) would abort trying to
# install artifacts that do not exist. Since KICAD_KIFACE == KICAD_BIN on Linux
# (CMakeLists.txt:694 -- "For now, the kifaces are just in the normal bin
# folder"), the three artifacts we actually built all belong in one flat
# directory anyway, so copying them there directly is both correct and simpler
# than fighting cmake_install.cmake's component graph.
#
# No stock symbol/footprint libraries are installed (they live in separate
# kicad-symbols/kicad-footprints repos this Dockerfile never fetches) -- this is
# expected, documented in README.md's usage of the suite against this image, and
# NOT something to route around here.
# ---------------------------------------------------------------------------
RUN set -eux; \
    mkdir -p "${KICAD_PREFIX}/bin"; \
    install -m755 /src/build/kicad/kicad-cli "${KICAD_PREFIX}/bin/kicad-cli"; \
    install -m755 /src/build/pcbnew/_pcbnew.kiface "${KICAD_PREFIX}/bin/_pcbnew.kiface"; \
    install -m755 /src/build/eeschema/_eeschema.kiface "${KICAD_PREFIX}/bin/_eeschema.kiface"; \
    du -sh /src/build "${KICAD_PREFIX}"

# ---------------------------------------------------------------------------
# 5. Runtime wiring.
#
# The .gcno files live next to the objects in /src/build and MUST stay in the image:
# gcov pairs each runtime .gcda with the .gcno emitted at compile time.
#
# GCOV_PREFIX redirects .gcda writes out of the (throwaway) container layer into a
# bind-mounted directory. GCOV_PREFIX_STRIP=0 keeps the full object path, so the
# tree under /coverage/raw mirrors /src/build exactly and collect.sh can graft it
# back on top. Each run appends into the existing counters, so several suite runs
# merge naturally.
#
# KICAD_CLI is the first thing adapters/kicad.py checks (discover_kicad_cli:
# KICAD_CLI env -> PATH -> per-OS install dirs), so the instrumented binary is
# picked up with zero changes to the adapter or the runner.
# ---------------------------------------------------------------------------
ENV KICAD_PREFIX=/opt/kicad-cov \
    KICAD_CLI=/opt/kicad-cov/bin/kicad-cli \
    PATH=/opt/kicad-cov/bin:${PATH} \
    KICAD_BUILD_DIR=/src/build \
    KICAD_SRC_DIR=/src/kicad \
    GCOV_PREFIX=/coverage/raw \
    GCOV_PREFIX_STRIP=0 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

COPY collect.sh /usr/local/bin/collect-coverage
RUN chmod +x /usr/local/bin/collect-coverage; mkdir -p /coverage/raw

WORKDIR /work
