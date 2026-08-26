#!/usr/bin/env bash
# Reformat the same dedicated benchmark partition (see setup_test_partition.sh) to a
# different filesystem, for direct comparison. Currently ext4 -> xfs; edit TARGET_FS/
# MKFS_CMD below for a different target filesystem later, deliberately, same reasoning
# as setup_test_partition.sh's hardcoded-device rationale -- this is a scoped, reviewed
# script, not a generic "reformat any partition" tool.
#
# Usage: sudo ./reformat_test_partition.sh
#
# Requires xfsprogs (mkfs.xfs) -- not installed by this script; `sudo apt install
# xfsprogs` first, same reasoning as io_profile_benchmark.py's fio requirement: a
# system package install is a deliberate, explicit action.

set -euo pipefail

DEVICE="/dev/nvme0n1p3"
CURRENT_MOUNT="/mnt/resolve_test"
EXPECTED_UUID="38efefa4-5d18-476d-9dec-90b66ad8317c"   # current ext4 UUID, safety check
TARGET_FS="xfs"
NEW_LABEL="resolve_test"
REAL_USER="${SUDO_USER:-iam}"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

if ! command -v "mkfs.$TARGET_FS" >/dev/null; then
  echo "mkfs.$TARGET_FS not found -- install it first (e.g. sudo apt install xfsprogs)." >&2
  exit 1
fi

echo "== Safety check =="
CURRENT_UUID=$(lsblk -no UUID "$DEVICE" || true)
CURRENT_FSTYPE=$(lsblk -no FSTYPE "$DEVICE" || true)
echo "  device:       $DEVICE"
echo "  current type: $CURRENT_FSTYPE"
echo "  current UUID: $CURRENT_UUID"

if [[ "$CURRENT_UUID" != "$EXPECTED_UUID" ]]; then
  echo "ABORT: $DEVICE's UUID ($CURRENT_UUID) doesn't match the expected benchmark" \
       "partition's UUID ($EXPECTED_UUID). Not proceeding without a human re-checking" \
       "DEVICE/EXPECTED_UUID above." >&2
  exit 1
fi

USED=$(df --output=pcent "$CURRENT_MOUNT" 2>/dev/null | tail -1 | tr -d ' %' || echo "?")
echo "  current usage at $CURRENT_MOUNT: ${USED}%"
if [[ "$USED" -gt 1 ]]; then
  echo "ABORT: expected <=1% used (this is a disposable benchmark partition, should be" \
       "near-empty) but found ${USED}%. Not proceeding -- check what's on it first." >&2
  exit 1
fi

echo
CONFIRM=""
read -r -p "About to WIPE $DEVICE (currently $CURRENT_FSTYPE at $CURRENT_MOUNT, ${USED}% used) and format it $TARGET_FS. Type \"yes\" to continue: " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
  echo "Aborted, nothing changed."
  exit 1
fi

echo "== Unmounting $CURRENT_MOUNT =="
umount "$CURRENT_MOUNT"

echo "== Formatting $DEVICE as $TARGET_FS (label: $NEW_LABEL) =="
mkfs.xfs -f -L "$NEW_LABEL" "$DEVICE"

echo "== Updating fstab (in place, same mountpoint) =="
NEW_UUID=$(blkid -s UUID -o value "$DEVICE")
sed -i "\|$CURRENT_MOUNT|d" /etc/fstab
echo "UUID=$NEW_UUID $CURRENT_MOUNT $TARGET_FS defaults,nofail 0 2" >> /etc/fstab

echo "== Mounting =="
mount "$CURRENT_MOUNT"
chown "$REAL_USER":"$REAL_USER" "$CURRENT_MOUNT"

echo "== Trimming (avoids the untrimmed-block write-speed artifact found on the ext4 pass) =="
fstrim -v "$CURRENT_MOUNT"

echo
echo "Done. $DEVICE is now $TARGET_FS, mounted at $CURRENT_MOUNT, owned by $REAL_USER, trimmed."
df -h "$CURRENT_MOUNT"
