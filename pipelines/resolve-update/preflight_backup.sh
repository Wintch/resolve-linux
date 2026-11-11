#!/usr/bin/env bash
# Snapshot the currently-installed, hands-on-validated Resolve build and its config/project
# state before touching anything. Safe and additive: installs dpkg-repack if missing,
# repacks the live davinci-resolve-studio{,-data} packages into .debs (the only way back to
# this exact build if the original .run is gone), and runs resolve-backup.
#
# Runs as root; Resolve data is read from TARGET_USER's home (see lib.sh), and the output
# is chown'ed back to that user.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

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
# backup_resolve_config.py reads Path.home()/.local/share/DaVinciResolve, so HOME must be
# the target user's, not root's.
HOME="$TARGET_HOME" python3 "$(dirname "$0")/../resolve-backup/backup_resolve_config.py" --dest "$BUNDLE/resolve-config"

echo
echo "== Writing manifest =="
{
    echo "timestamp: $STAMP"
    echo
    echo "--- dpkg -l resolve packages ---"
    dpkg -l | grep -i resolve
    echo
    echo "--- dpkg -l nvidia packages ---"
    dpkg -l | grep -i nvidia || true
    echo
    echo "--- kernel ---"
    uname -r
    dpkg -l | grep linux-image || true
    echo
    echo "--- nvidia-smi ---"
    nvidia-smi --query-gpu=driver_version,name --format=csv,noheader 2>/dev/null || echo "nvidia-smi unavailable"
    echo
    echo "--- disk free space ---"
    df -h / "$RESOLVE_UPDATE_DIR"
    if command -v vgs >/dev/null 2>&1; then
        echo
        echo "--- LVM (free extents decide whether a snapshot is possible, see README.md) ---"
        vgs || true
        lvs || true
    fi
} > "$BUNDLE/manifest.txt"
cat "$BUNDLE/manifest.txt"

echo
echo "== Fixing ownership (everything above ran as root) =="
chown -R "$TARGET_USER": "$BUNDLE"
ls -la "$BUNDLE"

echo
echo "== Done =="
echo "Rollback bundle: $BUNDLE"
echo "Keep this path handy — pass it to rollback.sh if the update goes wrong."
