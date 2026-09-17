#!/usr/bin/env bash
# Builds and installs a new Resolve .deb from a manually-downloaded .run installer via
# makeresolvedeb, replacing the currently-installed 21.0.4-mrd1.10.0 build. Run
# preflight_backup.sh first so rollback.sh has something to restore.
#
# The .run download itself can't be scripted — Blackmagic gates it behind a free-account
# login tied to the Studio dongle (see the parent repo's README). Get it manually from the
# forum/support site, then pass its path here.
#
# Builds on /mnt/resolve_test, NOT ~/resolve-install (root LV) — the root filesystem was
# down to ~17G free after just the .run landed there (92% used, no LVM headroom either,
# see ../README.md). makeresolvedeb's own unpack dir + the resulting .deb(s) (Resolve 21
# already needs 2 .debs to dodge a 10GB single-file limit) would likely not fit. This also
# satisfies a real constraint in the script itself, not just convention: it refuses any
# installer path that isn't a bare filename in the current working directory
# (`$INSTALLER_ARCHIVE != $(basename "$INSTALLER_ARCHIVE")` check) — so the .run has to be
# moved into the build dir either way, not just referenced from wherever it was downloaded.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 /path/to/DaVinci_Resolve_Studio_<version>_Linux.run" >&2
    exit 1
fi
RUN_FILE="$1"
# ROOT-ONLY VARIANT (2026-09-11): no working sudo for 'iam' on this rig -- hardcoded to
# /home/iam rather than trusting $HOME, which would be /root when run as root.
# Also: 1.10.1 (2026-09-10), not the 1.10.0 (2026-05-15) this rig's 21.0.4 install used --
# confirmed by diff that 1.10.1 adds handling for the new Immersive directory and bundled
# ResolvePython that 21.1 ships (1.10.0 would silently skip packaging both).
MAKERESOLVEDEB_SCRIPT="/home/iam/resolve-install/makeresolvedeb_1.10.1_multi.sh"
BUILD_ROOT="/mnt/resolve_test/resolve-update-build"

if [[ ! -f "$RUN_FILE" ]]; then
    echo "Not found: $RUN_FILE" >&2
    exit 1
fi
if [[ ! -x "$MAKERESOLVEDEB_SCRIPT" ]]; then
    echo "Not found or not executable: $MAKERESOLVEDEB_SCRIPT" >&2
    exit 1
fi

echo "== Checking for an existing rollback bundle =="
LATEST_BUNDLE="$(ls -1dt /mnt/resolve_test/resolve-update-rollback/*/ 2>/dev/null | head -1 || true)"
if [[ -z "$LATEST_BUNDLE" ]]; then
    echo "WARNING: no rollback bundle found under /mnt/resolve_test/resolve-update-rollback/." >&2
    echo "Run preflight_backup.sh first, or you have no way back to 21.0.4-mrd1.10.0." >&2
    read -rp "Continue anyway? [y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1
else
    echo "Latest rollback bundle: $LATEST_BUNDLE"
fi

echo
echo "== Preparing build dir on /mnt/resolve_test (spacious, not the tight root LV) =="
mkdir -p "$BUILD_ROOT"

echo
echo "== Moving the .run into the build dir (makeresolvedeb requires a bare filename in its CWD) =="
mv -v "$RUN_FILE" "$BUILD_ROOT/"
RUN_BASENAME="$(basename "$RUN_FILE")"

echo
echo "== Running makeresolvedeb =="
( cd "$BUILD_ROOT" && "$MAKERESOLVEDEB_SCRIPT" "$RUN_BASENAME" )

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
# makeresolvedeb doesn't set an Installed-Size control field (dpkg-query/-s return nothing
# for it), so this measures real bytes instead of trusting package metadata: the staged
# /opt/resolve tree(s) in the build output vs. the currently-installed one. /opt/resolve
# itself is on the root LV (confirmed 2026-09-11 -- no separate mount), which was at 92%
# used (17G free) before this script even ran; a mid-install disk-full would leave a
# half-unpacked, broken Resolve, not just a failed command.
NEW_SIZE=0
for d in $NEW_DEBS; do
    SIZE=$(dpkg-deb -c "$d" | awk '{sum+=$3} END{print sum+0}')
    NEW_SIZE=$((NEW_SIZE + SIZE))
done
# /opt/resolve/Extras is NOT owned by either package (confirmed via dpkg -L -- it's
# downloaded at runtime by Resolve's own Extras Download Manager) so it isn't touched by
# this upgrade; exclude it from the "old" side of the comparison or the delta is wrong.
OLD_TOTAL=$(du -sb /opt/resolve 2>/dev/null | awk '{print $1}')
OLD_EXTRAS=$(du -sb /opt/resolve/Extras 2>/dev/null | awk '{print $1}')
OLD_SIZE=$((OLD_TOTAL - OLD_EXTRAS))
NEEDED=$((NEW_SIZE - OLD_SIZE))
AVAIL=$(df --output=avail -B1 / | tail -1)
SAFETY_MARGIN=$((2 * 1024 * 1024 * 1024))  # 2GB buffer, arbitrary but non-zero

human() { numfmt --to=iec-i --suffix=B "$1" 2>/dev/null || echo "${1}B"; }
echo "  new build's /opt/resolve payload:  $(human "$NEW_SIZE")"
echo "  currently installed (minus Extras): $(human "$OLD_SIZE")"
echo "  net new space needed:              $(human "$NEEDED")"
echo "  currently available on /:          $(human "$AVAIL")"

if [[ "$NEEDED" -gt 0 ]] && (( NEEDED + SAFETY_MARGIN > AVAIL )); then
    echo >&2
    echo "!! Not enough headroom on / for a safe install (need $(human "$NEEDED") plus a" >&2
    echo "   ${SAFETY_MARGIN}B safety margin, have $(human "$AVAIL"))." >&2
    echo "   A disk-full mid-'apt install' can leave Resolve half-unpacked and broken." >&2
    echo "   Free space first, e.g.:" >&2
    echo "     apt autoremove   # drops the orphaned 6.12.100 kernel apt already flagged" >&2
    echo "     apt-get clean    # clears /var/cache/apt/archives" >&2
    echo "   then re-run this script (it will re-detect the already-built .debs)." >&2
    exit 1
fi
echo "  OK — proceeding."

echo
echo "== Installing (this replaces the current 21.0.4-mrd1.10.0 build) =="
# shellcheck disable=SC2086
apt install $NEW_DEBS

echo
echo "== Installed version =="
dpkg -l | grep -i resolve | grep -v -- -data

echo
echo "== Fixing ownership of the build dir (ran as root) =="
chown -R iam:iam "$BUILD_ROOT"

echo
echo "== Now: launch Resolve and verify by hand (open a project, edit, render a frame) — =="
echo "== a process launching is not sufficient evidence, per this project's own rule.    =="
echo "== If anything's wrong: ./rollback.sh ${LATEST_BUNDLE:+$LATEST_BUNDLE}             =="
