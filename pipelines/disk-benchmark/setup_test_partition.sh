#!/usr/bin/env bash
# One-shot, scoped setup for a dedicated ext4 test partition on this rig, used to get a
# clean apples-to-apples IOPS/throughput baseline against the root filesystem (also ext4)
# without the NTFS-3G/FUSE overhead that /mnt/videos carries. Run with sudo.
#
# Target device is hardcoded on purpose, not passed as an argument -- this touches a real
# partition on a drive with other real data on sibling partitions (see PERFORMANCE.md
# section 1a: this NVMe has a documented history of silent corruption on this exact rig,
# per the user). A wrong device here is not recoverable. If the target ever needs to
# change, edit DEVICE/OLD_MOUNT/OLD_UUID below deliberately, don't parameterize it away.
#
# What this does, in order:
#   1. Unmounts the old NTFS filesystem at /mnt/win3 (nvme0n1p3, freed by the user
#      specifically for this -- confirmed empty, 0% used, before this script was written)
#   2. mkfs.ext4 the partition, labeled "resolve_test"
#   3. Removes the stale /etc/fstab line that mounted it as ntfs-3g at /mnt/win3 (left in
#      place, it would fail at boot with a wrong-fstype error every time -- `nofail` means
#      it wouldn't block boot, but it would sit unmounted and confusing)
#   4. Adds a new fstab line, by UUID, mounting it ext4 at /mnt/resolve_test
#   5. mkdir + mount + chown to the invoking (non-root) user, so no sudo is needed for
#      actual benchmark runs against it afterward
#
# Usage: sudo ./setup_test_partition.sh

set -euo pipefail

DEVICE="/dev/nvme0n1p3"
OLD_MOUNT="/mnt/win3"
OLD_UUID="C6CEA2C7CEA2AEDD"          # current NTFS UUID, from /etc/fstab, for a safety check
NEW_MOUNT="/mnt/resolve_test"
NEW_LABEL="resolve_test"
REAL_USER="${SUDO_USER:-iam}"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

echo "== Safety check =="
CURRENT_FSTYPE=$(lsblk -no FSTYPE "$DEVICE" || true)
CURRENT_UUID=$(lsblk -no UUID "$DEVICE" || true)
echo "  device:       $DEVICE"
echo "  current type: $CURRENT_FSTYPE"
echo "  current UUID: $CURRENT_UUID"

if [[ "$CURRENT_UUID" != "$OLD_UUID" ]]; then
  echo "ABORT: $DEVICE's UUID ($CURRENT_UUID) doesn't match the expected old NTFS UUID" \
       "($OLD_UUID). Something changed since this script was written -- not proceeding" \
       "without a human re-checking DEVICE/OLD_UUID above." >&2
  exit 1
fi

USED=$(df --output=pcent "$OLD_MOUNT" 2>/dev/null | tail -1 | tr -d ' %' || echo "?")
echo "  current usage at $OLD_MOUNT: ${USED}%"
# Threshold raised from 0% to 1%, 2026-08-26: checked live, the 1% here is confirmed
# Steam-uninstall residue (empty SteamLibrary dirs, $RECYCLE.BIN, System Volume
# Information -- ~1.3GB total, no real data) -- not a re-check of "did something new
# show up", a one-time acknowledged exception for this specific partition's actual state.
if [[ "$USED" -gt 1 ]]; then
  echo "ABORT: expected <=1% used (1% confirmed as harmless Steam-uninstall residue) but" \
       "found ${USED}%. Not proceeding -- check what's actually on this partition before" \
       "formatting it." >&2
  exit 1
fi

echo
CONFIRM=""
read -r -p "About to WIPE $DEVICE (currently mounted at $OLD_MOUNT, NTFS, ${USED}% used) and format it ext4. Type \"yes\" to continue: " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
  echo "Aborted, nothing changed."
  exit 1
fi

echo "== Unmounting $OLD_MOUNT =="
umount "$OLD_MOUNT"

echo "== Formatting $DEVICE as ext4 (label: $NEW_LABEL) =="
mkfs.ext4 -L "$NEW_LABEL" "$DEVICE"

echo "== Removing stale fstab line for $OLD_UUID =="
sed -i "\|UUID=$OLD_UUID|d" /etc/fstab

echo "== Adding new fstab line =="
NEW_UUID=$(blkid -s UUID -o value "$DEVICE")
mkdir -p "$NEW_MOUNT"
echo "UUID=$NEW_UUID $NEW_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab

echo "== Mounting =="
mount "$NEW_MOUNT"
chown "$REAL_USER":"$REAL_USER" "$NEW_MOUNT"

echo
echo "Done. $DEVICE is now ext4, mounted at $NEW_MOUNT, owned by $REAL_USER."
df -h "$NEW_MOUNT"
