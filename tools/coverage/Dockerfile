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
# This is Debian's `kicad` source package Build-Depends with the documentation
# toolchain (texlive-lang-*, dblatex, docbook-*, doxygen, imagemagick, ...) stripped
# out -- it exists only for the Debian doc packages, not kicad-cli. Four packages
# (libspnav-dev, libwxgtk-webview3.2-dev, libpoppler-*-dev, libpixman-1-dev) are
# 10.x additions not in trixie's (9.0.2) Build-Depends, found by CMake configure
# failing without them. `verify-builddeps.sh` diffs this list against upstream.
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
# -fprofile-update=atomic: KiCad runs DRC/ERC/parsing on thread pools, so
# non-atomic counters would silently under-report. -O0: no inlining, so line/branch
# mapping matches the source.
#
# THE FLAGS MUST RIDE IN CMAKE_C_FLAGS / CMAKE_CXX_FLAGS, NOT THE _DEBUG VARIANTS.
# KiCad's CMakeLists.txt:441-446 unconditionally sets CMAKE_CXX_FLAGS_DEBUG,
# clobbering anything passed via -DCMAKE_CXX_FLAGS_DEBUG=... (produced a 26.7 GB
# image with zero .gcno files, once). CMAKE_CXX_FLAGS is only ever appended to, so
# it survives; KICAD_BUILD_SMALL_DEBUG_FILES=ON holds debug info to -g1 instead.
# The `grep -q profile-arcs build.ninja` assertion below catches a regression here.
#
# KICAD_STDLIB_DEBUG/_LIGHT_DEBUG stay OFF: they define _GLIBCXX_DEBUG, which
# changes libstdc++ ABI and would not match the distro-built wxWidgets/OCC.
# KICAD_BUILD_QA_TESTS=OFF (upstream's own unit tests, not ours) and
# KICAD_SCRIPTING_WXPYTHON=OFF (SWIG pcbnew_wrap.cxx is a peak-RAM hazard and
# kicad-cli doesn't use it -- Python scripting coverage is out of scope).
# ---------------------------------------------------------------------------
FROM source AS build
ENV CCACHE_DIR=/ccache
ARG COVERAGE_FLAGS="-O0 -fprofile-arcs -ftest-coverage -fprofile-update=atomic"
ARG KICAD_PREFIX=/opt/kicad-cov

RUN --mount=type=cache,target=/ccache,sharing=locked \
    set -eux; \
    cmake -S /src/kicad -B /src/build -G Ninja \
        -DCMAKE_BUILD_TYPE=Debug \
        -DCMAKE_INSTALL_PREFIX="${KICAD_PREFIX}" \
        -DCMAKE_C_FLAGS="${COVERAGE_FLAGS}" \
        -DCMAKE_CXX_FLAGS="${COVERAGE_FLAGS}" \
        -DCMAKE_EXE_LINKER_FLAGS="--coverage" \
        -DCMAKE_SHARED_LINKER_FLAGS="--coverage" \
        -DCMAKE_MODULE_LINKER_FLAGS="--coverage" \
        -DCMAKE_C_COMPILER_LAUNCHER=ccache \
        -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
        -DKICAD_BUILD_QA_TESTS=OFF \
        -DKICAD_SCRIPTING_WXPYTHON=OFF \
        -DKICAD_STDLIB_DEBUG=OFF \
        -DKICAD_STDLIB_LIGHT_DEBUG=OFF \
        -DKICAD_BUILD_SMALL_DEBUG_FILES=ON \
        -DKICAD_USE_VALGRIND=OFF \
        -DKICAD_SANITIZE_ADDRESS=OFF \
        -DKICAD_SANITIZE_THREADS=OFF \
    ; \
    echo '=== configure OK ==='; \
    grep -E '^(CMAKE_CXX_FLAGS|CMAKE_BUILD_TYPE):' /src/build/CMakeCache.txt; \
    echo '=== asserting the generator actually emitted the coverage flags ==='; \
    grep -m1 '^  FLAGS = ' /src/build/build.ninja; \
    grep -q 'profile-arcs' /src/build/build.ninja \
        || { echo "FATAL: build.ninja carries no -fprofile-arcs -- KiCad's CMakeLists clobbered our flags; see the comment above this RUN"; exit 1; }

# ---------------------------------------------------------------------------
# 4. Build + install -- MINIMAL TARGET SET, not the default `all`.
#
# We only need kicad-cli plus the pcbnew/eeschema/cvpcb KIFACEs it dlopen()s at
# runtime (see common/kiway.cpp) -- never the GUI apps or QA targets:
#     kicad-cli        the executable (kicad/CMakeLists.txt:120)
#     pcbnew_kiface     -> _pcbnew.kiface   (pcbnew/CMakeLists.txt:854)
#     eeschema_kiface   -> _eeschema.kiface (eeschema/CMakeLists.txt:639)
#     cvpcb_kiface      -> _cvpcb.kiface    (cvpcb/CMakeLists.txt:59)
#
# THE THIRD ONE IS NOT OPTIONAL: `eeschema_jobs_handler.cpp` calls unconditionally
# into cvpcb's footprint-link tester during `sch erc`, so without `_cvpcb.kiface`
# KIWAY::KiFACE() throws before any ERC test runs, kicad-cli exits 255, and
# `eeschema/erc/**` reads ~0% no matter how many ERC cases the suite has -- and it
# fails loud enough in stderr to look like a suite problem, not an image one.
# Costs ~1% more build time (23 of ~2032 ninja edges).
#
# -j is bounded by the builder VM's RAM, not core count -- 8 is a balanced default
# on a 16 vCPU / 6 GiB VM; raise %USERPROFILE%\.wslconfig memory before raising -j.
# The ccache mount makes an interrupted build cheap to resume; this RUN stays
# narrowed to compile+link only so a retry's cache replay isn't blocked on
# unrelated work before the layer commits.
# ---------------------------------------------------------------------------
ARG BUILD_JOBS=8
ARG KICAD_TARGETS="kicad-cli pcbnew_kiface eeschema_kiface cvpcb_kiface"
RUN --mount=type=cache,target=/ccache,sharing=locked \
    set -eux; \
    ccache --zero-stats; \
    cmake --build /src/build --parallel "${BUILD_JOBS}" -- ${KICAD_TARGETS}; \
    ccache --show-stats | head -20; \
    n_gcno="$(find /src/build -name '*.gcno' | wc -l)"; \
    echo "=== .gcno files emitted: ${n_gcno} ==="; \
    test "${n_gcno}" -gt 1000 \
        || { echo "FATAL: only ${n_gcno} .gcno files -- this build is NOT instrumented and gcov would report nothing"; exit 1; }

# ---------------------------------------------------------------------------
# 4b. Install -- manual copy, not `cmake --install`: that walks every install()
# rule and would abort on the GUI/QA COMPONENT "binary" targets we never built.
# KICAD_KIFACE == KICAD_BIN on Linux, so the three artifacts belong in one flat
# directory anyway. No stock symbol/footprint libraries are installed (they live
# in separate kicad-symbols/kicad-footprints repos this Dockerfile never fetches).
# ---------------------------------------------------------------------------
RUN set -eux; \
    mkdir -p "${KICAD_PREFIX}/bin"; \
    install -m755 /src/build/kicad/kicad-cli "${KICAD_PREFIX}/bin/kicad-cli"; \
    install -m755 /src/build/pcbnew/_pcbnew.kiface "${KICAD_PREFIX}/bin/_pcbnew.kiface"; \
    install -m755 /src/build/eeschema/_eeschema.kiface "${KICAD_PREFIX}/bin/_eeschema.kiface"; \
    install -m755 /src/build/cvpcb/_cvpcb.kiface "${KICAD_PREFIX}/bin/_cvpcb.kiface"; \
    test -x "${KICAD_PREFIX}/bin/_cvpcb.kiface" \
        || { echo "FATAL: _cvpcb.kiface missing -- every 'sch erc' invocation will exit 255 and eeschema/erc/** will read 0%; see the note above target 4"; exit 1; }; \
    du -sh /src/build "${KICAD_PREFIX}"

# ---------------------------------------------------------------------------
# 5. Runtime wiring. The .gcno files stay in /src/build (gcov pairs them with the
# runtime .gcda). GCOV_PREFIX_STRIP=0 keeps the full object path so /coverage/raw
# mirrors /src/build and collect.sh can graft it back on; counters accumulate
# across runs. KICAD_CLI is what adapters/kicad.py's discover_kicad_cli() checks
# first, so the instrumented binary needs zero adapter/runner changes.
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
