#!/usr/bin/env bash
# Reinstalls the archived, hands-on-validated Resolve build from a preflight_backup.sh
# bundle. Defaults to the most recent bundle under /mnt/resolve_test/resolve-update-rollback/
# if none is given.
#
# ROOT-ONLY VARIANT (2026-09-11): no working sudo for 'iam' on this rig -- run this
# directly as root, no 'sudo' anywhere below.
set -euo pipefail

ROLLBACK_ROOT="/mnt/resolve_test/resolve-update-rollback"

BUNDLE="${1:-}"
if [[ -z "$BUNDLE" ]]; then
    BUNDLE="$(ls -1dt "$ROLLBACK_ROOT"/*/ 2>/dev/null | head -1 || true)"
    if [[ -z "$BUNDLE" ]]; then
        echo "No rollback bundle found under $ROLLBACK_ROOT and none given." >&2
        exit 1
    fi
fi
BUNDLE="${BUNDLE%/}"
echo "Using bundle: $BUNDLE"

DEBS=$(find "$BUNDLE" -maxdepth 1 -iname "davinci-resolve-studio*.deb")
if [[ -z "$DEBS" ]]; then
    echo "No .debs found in $BUNDLE — is this a valid preflight_backup.sh bundle?" >&2
    exit 1
fi
echo "$DEBS"

if pgrep -f "bin/resolve$" >/dev/null; then
    echo "Resolve is currently running — close it first so the downgrade doesn't fight a" >&2
    echo "live install." >&2
    exit 1
fi

echo
echo "== Reinstalling archived build (--allow-downgrades, since this is a version rollback) =="
# shellcheck disable=SC2086
apt install --allow-downgrades $DEBS

echo
echo "== Installed version after rollback =="
dpkg -l | grep -i resolve | grep -v -- -data

echo
echo "== Config/project state =="
echo "The bundle also has a config/project snapshot at: $BUNDLE/resolve-config"
echo "This was NOT restored automatically — the app rollback alone is usually enough, and"
echo "restoring project files could overwrite newer work done in the meantime. If Resolve"
echo "won't open projects saved by the newer version, restore by hand:"
echo "  cp -a \"$BUNDLE/resolve-config/resolve-config/project_library\"/* \\"
echo "     /home/iam/.local/share/DaVinciResolve/\"Resolve Project Library\"/"
echo "(check the actual subfolder names under $BUNDLE/resolve-config first — this is a guide, not a guarantee)"

echo
echo "== Kernel rollback (if the OS update, not Resolve, is the problem) =="
echo "This script doesn't touch the kernel. If a newer kernel is the issue: reboot, hold"
echo "Shift (BIOS) or Esc (UEFI) to reach GRUB, pick 'Advanced options for Debian"
echo "GNU/Linux', and select the previously-running kernel. Old kernel packages are not"
echo "removed by 'apt upgrade' by default, so it should still be there."
