#!/usr/bin/env bash
# Snapshot the currently-installed, hands-on-validated Resolve build + its config/project
# state before touching anything. Safe and additive: installs dpkg-repack if missing,
# repacks the live davinci-resolve-studio{,-data} packages into .debs (the original .run
# installer no longer exists on this rig), and runs the existing resolve-backup script.
# See ../README.md for why this lives on /mnt/resolve_test instead of the root LV.
#
# ROOT-ONLY VARIANT (2026-09-11): this rig has no working sudo for the 'iam' user, so this
# runs directly as root, no 'sudo' anywhere. That reintroduces the exact bug an earlier
# version of this script hit twice (Path.home() -> /root instead of /home/iam, silently
# emptying the config/project snapshot) -- fixed here by forcing HOME=/home/iam on the one
# step that depends on it, and chown'ing the output back to iam afterward instead of
# leaving everything root-owned.
set -euo pipefail

IAM_HOME="/home/iam"
ROLLBACK_ROOT="/mnt/resolve_test/resolve-update-rollback"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE="$ROLLBACK_ROOT/$STAMP"

echo "== Checking installed Resolve packages =="
if ! dpkg -l davinci-resolve-studio davinci-resolve-studio-data >/dev/null 2>&1; then
    echo "davinci-resolve-studio / davinci-resolve-studio-data not found installed — nothing to back up." >&2
    exit 1
fi
dpkg -l davinci-resolve-studio davinci-resolve-studio-data | tail -n +6

echo
echo "== Preparing rollback bundle dir: $BUNDLE =="
mkdir -p "$BUNDLE"

echo
echo "== Ensuring dpkg-repack is installed =="
if ! command -v dpkg-repack >/dev/null 2>&1; then
    apt-get install -y dpkg-repack
fi

echo
echo "== Repacking currently-installed Resolve .debs =="
( cd "$BUNDLE" && dpkg-repack davinci-resolve-studio davinci-resolve-studio-data )
ls -la "$BUNDLE"/*.deb

echo
echo "== Snapshotting Resolve config/project state (resolve-backup script) =="
# HOME=/home/iam, not whatever root's $HOME is -- backup_resolve_config.py reads from
# Path.home()/.local/share/DaVinciResolve, which must resolve to the real user's data.
HOME="$IAM_HOME" python3 "$(dirname "$0")/../resolve-backup/backup_resolve_config.py" --dest "$BUNDLE/resolve-config"

echo
echo "== Writing manifest =="
{
    echo "timestamp: $STAMP"
    echo
    echo "--- dpkg -l resolve packages ---"
    dpkg -l | grep -i resolve
    echo
    echo "--- dpkg -l nvidia packages ---"
    dpkg -l | grep -i nvidia
    echo
    echo "--- kernel ---"
    uname -r
    dpkg -l | grep linux-image
    echo
    echo "--- nvidia-smi ---"
    nvidia-smi --query-gpu=driver_version,name --format=csv,noheader 2>/dev/null || echo "nvidia-smi unavailable"
    echo
    echo "--- disk/VG free space (reminder: no snapshot capacity, see ../README.md) ---"
    df -h /
    vgs
    lvs
    echo
    echo "--- shared driver state (reverb-g2's pre-update-check.sh, if present) ---"
    REVERB_PRECHECK="$IAM_HOME/Documents/reverb-g2/scripts/pre-update-check.sh"
    if [[ -x "$REVERB_PRECHECK" ]]; then
        "$REVERB_PRECHECK" 2>&1
    else
        echo "$REVERB_PRECHECK not found — skipped."
    fi
} > "$BUNDLE/manifest.txt"
cat "$BUNDLE/manifest.txt"

echo
echo "== Fixing ownership (everything above ran as root) =="
chown -R iam:iam "$BUNDLE"
ls -la "$BUNDLE"

echo
echo "== Done =="
echo "Rollback bundle: $BUNDLE"
echo "Keep this path handy — pass it to rollback.sh if the update goes wrong."
