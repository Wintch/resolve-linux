#!/usr/bin/env bash
# Builds and installs a new Resolve .deb from a .run installer via makeresolvedeb,
# replacing the currently-installed build. Run preflight_backup.sh first so rollback.sh has
# something to restore.
#
# Get the .run with download_resolve.sh (no account or registration data needed, runs as a
# normal user), unzip it, then pass the .run's path here.
#
# Usage: MAKERESOLVEDEB=/path/to/makeresolvedeb_<ver>_multi.sh ./install_resolve.sh <file.run>
#
# The build happens in $RESOLVE_UPDATE_DIR (see lib.sh), not wherever the .run was
# downloaded: makeresolvedeb's unpack dir plus the resulting .debs (Resolve 21 ships as two
# .debs to stay under a 10 GB single-file limit) need tens of GB. makeresolvedeb also
# refuses any installer path that isn't a bare filename in its working directory, so the
# .run is moved into the build dir.
#
# Use a makeresolvedeb release that knows your Resolve version: 1.10.1 added the Immersive
# directory and the bundled ResolvePython that 21.1 ships; 1.10.0 silently skips both.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

if [[ $# -ne 1 ]]; then
    echo "Usage: MAKERESOLVEDEB=/path/to/makeresolvedeb.sh $0 /path/to/DaVinci_Resolve_Studio_<version>_Linux.run" >&2
    exit 1
fi
RUN_FILE="$1"
MAKERESOLVEDEB="${MAKERESOLVEDEB:-}"

if [[ ! -f "$RUN_FILE" ]]; then
    echo "Not found: $RUN_FILE" >&2
    exit 1
fi
if [[ -z "$MAKERESOLVEDEB" || ! -x "$MAKERESOLVEDEB" ]]; then
    echo "Set MAKERESOLVEDEB to the executable makeresolvedeb script (got '${MAKERESOLVEDEB}')." >&2
    exit 1
fi

echo "== Checking for an existing rollback bundle =="
LATEST_BUNDLE="$(ls -1dt "$ROLLBACK_ROOT"/*/ 2>/dev/null | head -1 || true)"
if [[ -z "$LATEST_BUNDLE" ]]; then
    echo "WARNING: no rollback bundle found under $ROLLBACK_ROOT/." >&2
    echo "Run preflight_backup.sh first, or you have no way back to the current build." >&2
    read -rp "Continue anyway? [y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1
else
    echo "Latest rollback bundle: $LATEST_BUNDLE"
fi

echo
echo "== Preparing build dir: $BUILD_ROOT =="
mkdir -p "$BUILD_ROOT"

echo
# A leftover .deb from an earlier build would be picked up by the find below and handed
# to apt together with the new ones (two versions of the same package).
if find "$BUILD_ROOT" -maxdepth 1 -iname "davinci-resolve-studio*.deb" | grep -q .; then
    echo "Stale davinci-resolve-studio*.deb already in $BUILD_ROOT -- move them out first" >&2
    echo "(e.g. into $BUILD_ROOT/stale-debs/), they would be installed with the new build." >&2
    exit 1
fi

echo "== Moving the .run into the build dir (makeresolvedeb requires a bare filename in its CWD) =="
mv -v "$RUN_FILE" "$BUILD_ROOT/"
RUN_BASENAME="$(basename "$RUN_FILE")"

echo
echo "== Running makeresolvedeb =="
( cd "$BUILD_ROOT" && "$MAKERESOLVEDEB" "$RUN_BASENAME" )

echo
echo "== Locating built .debs =="
NEW_DEBS=$(find "$BUILD_ROOT" -maxdepth 1 -iname "davinci-resolve-studio*.deb")
if [[ -z "$NEW_DEBS" ]]; then
    echo "No new .deb found in $BUILD_ROOT — check makeresolvedeb's output above for where" >&2
    echo "it actually wrote them and install manually with 'apt install ./<file>.deb'." >&2
    exit 1
fi
echo "$NEW_DEBS"

echo
echo "== Disk-space guard before installing =="
# makeresolvedeb doesn't set an Installed-Size control field, so this measures real bytes
# from the built .debs against the currently-installed /opt/resolve. A disk-full in the
# middle of 'apt install' leaves a half-unpacked, broken Resolve, not just a failed command.
NEW_SIZE=0
for d in $NEW_DEBS; do
    SIZE=$(dpkg-deb -c "$d" | awk '{sum+=$3} END{print sum+0}')
    NEW_SIZE=$((NEW_SIZE + SIZE))
done
# /opt/resolve/Extras is NOT owned by either package (Resolve's Extras Download Manager
# fetches it at runtime) and survives an upgrade untouched; exclude it from the "old" side.
OLD_TOTAL=$(du -sb /opt/resolve 2>/dev/null | awk '{print $1+0}')
OLD_EXTRAS=$(du -sb /opt/resolve/Extras 2>/dev/null | awk '{print $1+0}')
OLD_SIZE=$(( ${OLD_TOTAL:-0} - ${OLD_EXTRAS:-0} ))
NEEDED=$((NEW_SIZE - OLD_SIZE))
OPT_PARENT=/opt
AVAIL=$(df --output=avail -B1 "$OPT_PARENT" | tail -1)
SAFETY_MARGIN=$((2 * 1024 * 1024 * 1024))  # 2 GB buffer

human() { numfmt --to=iec-i --suffix=B "$1" 2>/dev/null || echo "${1}B"; }
echo "  new build's /opt/resolve payload:   $(human "$NEW_SIZE")"
echo "  currently installed (minus Extras): $(human "$OLD_SIZE")"
echo "  net new space needed:               $(human "$NEEDED")"
echo "  currently available for /opt:       $(human "$AVAIL")"

if [[ "$NEEDED" -gt 0 ]] && (( NEEDED + SAFETY_MARGIN > AVAIL )); then
    echo >&2
    echo "!! Not enough headroom for a safe install (need $(human "$NEEDED") plus a" >&2
    echo "   $(human "$SAFETY_MARGIN") safety margin, have $(human "$AVAIL"))." >&2
    echo "   Free space first (e.g. 'apt autoremove' for orphaned kernels, 'apt-get clean')," >&2
    echo "   then re-run: move the built .debs aside or install them by hand." >&2
    exit 1
fi
echo "  OK — proceeding."

if [[ -n "${PRE_UPDATE_HOOK:-}" ]]; then
    echo
    echo "== Running PRE_UPDATE_HOOK: $PRE_UPDATE_HOOK =="
    if ! bash -c "$PRE_UPDATE_HOOK"; then
        echo "PRE_UPDATE_HOOK failed — not installing." >&2
        exit 1
    fi
fi

echo
echo "== Installing (this replaces the currently-installed build) =="
# shellcheck disable=SC2086
apt install $NEW_DEBS

echo
echo "== Installed version =="
dpkg -l | grep -i resolve | grep -v -- -data

echo
echo "== Fixing ownership of the build dir (ran as root) =="
chown -R "$TARGET_USER": "$BUILD_ROOT"

echo
echo "== Now: launch Resolve and verify by hand (open a project, edit, render a frame). =="
echo "== A process that launches is not evidence that it works.                        =="
echo "== If anything's wrong: ./rollback.sh ${LATEST_BUNDLE:+$LATEST_BUNDLE}"
