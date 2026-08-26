#!/usr/bin/env bash
# Same pattern as reformat_test_partition.sh (ext4 -> xfs), this one goes xfs -> btrfs on
# the same dedicated benchmark partition. See that script's header for the full
# reasoning on hardcoding the device instead of parameterizing it.
#
# Usage: sudo ./reformat_test_partition_btrfs.sh
#
# Requires btrfs-progs (mkfs.btrfs) -- install first: sudo apt install btrfs-progs

set -euo pipefail

DEVICE="/dev/nvme0n1p3"
CURRENT_MOUNT="/mnt/resolve_test"
EXPECTED_UUID="3c1ec894-b64a-4707-b517-020609872c2f"   # current xfs UUID, safety check
TARGET_FS="btrfs"
NEW_LABEL="resolve_test"
REAL_USER="${SUDO_USER:-iam}"
MKFS_BIN="/sbin/mkfs.btrfs"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

if [[ ! -x "$MKFS_BIN" ]]; then
  echo "$MKFS_BIN not found -- install it first: sudo apt install btrfs-progs" >&2
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
if [[ "$USED" -gt 2 ]]; then
  echo "ABORT: expected <=2% used (this is a disposable benchmark partition, should be" \
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
"$MKFS_BIN" -f -L "$NEW_LABEL" "$DEVICE"

echo "== Updating fstab (in place, same mountpoint) =="
NEW_UUID=$(blkid -s UUID -o value "$DEVICE")
sed -i "\|$CURRENT_MOUNT|d" /etc/fstab
echo "UUID=$NEW_UUID $CURRENT_MOUNT $TARGET_FS defaults,nofail 0 2" >> /etc/fstab

echo "== Mounting =="
mount "$CURRENT_MOUNT"
chown "$REAL_USER":"$REAL_USER" "$CURRENT_MOUNT"

echo "== Trimming =="
fstrim -v "$CURRENT_MOUNT" || echo "(fstrim not supported/needed here, continuing)"

echo
echo "Done. $DEVICE is now $TARGET_FS, mounted at $CURRENT_MOUNT, owned by $REAL_USER."
df -h "$CURRENT_MOUNT"
