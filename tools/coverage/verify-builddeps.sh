#!/usr/bin/env bash
# Drift check for the dependency list in the Dockerfile.
#
# The Dockerfile installs an explicit package list rather than running
# `apt-get build-dep kicad`, because two thirds of Debian's Build-Depends for the
# kicad source package is a documentation toolchain (texlive-lang-*, dblatex,
# docbook-*, doxygen, asciidoctor, xmlto, po4a, imagemagick, lmodern,
# source-highlight) that contributes nothing to kicad-cli -- 687 packages instead of
# ~250. This script re-derives the upstream list and prints anything upstream wants
# that we do not install, so the shortcut stays honest when the base image moves.
#
#   tools/coverage/verify-builddeps.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${BASE_IMAGE:-debian:trixie-slim}"

# The package tokens actually installed by the Dockerfile.
grep -oE '^\s{8}[a-z0-9][a-z0-9.+-]*\s*\\?$' "$HERE/Dockerfile" \
    | tr -d ' \\' | sort -u > /tmp/ours.txt

MSYS_NO_PATHCONV=1 docker run --rm "$BASE" bash -c '
set -e
printf "Types: deb deb-src\nURIs: http://deb.debian.org/debian\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n" > /etc/apt/sources.list.d/debian.sources
apt-get update -qq 2>/dev/null
apt-cache showsrc kicad | sed -n "s/^Build-Depends: //p" | head -1 \
    | tr "," "\n" | sed -E "s/\(.*\)//; s/\[.*\]//; s/<.*>//; s/\s//g" | grep -v "^$" | sort -u
' > /tmp/upstream.txt

# Documentation-only build deps we intentionally skip.
DOC_ONLY='^(asciidoctor|chrpath|dblatex|debhelper-compat|dh-python|docbook-utils|docbook-xsl|doxygen|dpkg-dev|imagemagick|liblocale-gettext-perl|libterm-readkey-perl|libtext-wrapi18n-perl|libunicode-linebreak-perl|libxml2-utils|lmodern|po4a|source-highlight|texlive-.*|xmlto|python3-wxgtk4\.0)$'

echo "=== upstream Build-Depends not installed by our Dockerfile ==="
comm -23 /tmp/upstream.txt /tmp/ours.txt | grep -Ev "$DOC_ONLY" || true
echo "(empty above == no drift; entries matching the doc-toolchain allowlist are hidden)"
echo
echo "=== installed by us, beyond upstream Build-Depends (KiCad 10 extras / tooling) ==="
comm -13 /tmp/upstream.txt /tmp/ours.txt || true
