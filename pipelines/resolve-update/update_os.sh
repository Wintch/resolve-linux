#!/usr/bin/env bash
# Conservative OS package update: `apt upgrade`, not `full-upgrade`/`dist-upgrade` — won't
# remove packages to satisfy new dependencies.
#
# This rig's NVIDIA driver (595.71.05) is shared with the reverb-g2 VR project and carries
# its 4 kernel patches (confirmed present in /usr/src/nvidia-595.71.05/dkms.conf,
# PATCH[0..3] — the HP Reverb G2 90Hz fix series). A kernel or NVIDIA/EGL package bump here
# triggers the exact same DKMS rebuild that project depends on, so this script reuses its
# check tooling (`~/Documents/reverb-g2/scripts/pre-update-check.sh`) instead of
# re-deriving drift/DKMS/patch-verification logic in this repo — same relationship
# resolve_power.py and backup_resolve_config.py already have with that project: not a
# fork, reuse as-is. See reverb-g2/docs/24-safe-system-updates.md for the full rationale,
# including the dpkg -V silent-drift finding (docs/73) this pulls in for free.
set -euo pipefail

# ROOT-ONLY VARIANT (2026-09-11): no working sudo for 'iam' on this rig, so this runs
# directly as root -- no 'sudo' anywhere below. Paths are hardcoded to /home/iam rather
# than trusting $HOME, which would be /root here.
IAM_HOME="/home/iam"
PIN_PKG="nvidia-driver-pinning-595.71.05"
REVERB_PRECHECK="$IAM_HOME/Documents/reverb-g2/scripts/pre-update-check.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== Shared-driver pre-check (reverb-g2's own script) =="
if [[ -x "$REVERB_PRECHECK" ]]; then
    "$REVERB_PRECHECK"
else
    echo "WARNING: $REVERB_PRECHECK not found — skipping the shared driver/DKMS/drift check." >&2
    echo "At minimum verify 'dpkg -l $PIN_PKG' shows installed before continuing." >&2
fi

echo
echo "== Pre-check: NVIDIA driver pin (Resolve-specific) =="
if ! dpkg -l "$PIN_PKG" 2>/dev/null | grep -q ^ii; then
    echo "WARNING: $PIN_PKG not found installed. The driver version this rig was validated" >&2
    echo "against (595.71.05) may not be pinned — an upgrade could silently bump it." >&2
    read -rp "Continue anyway? [y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1
fi

echo
echo "== apt-get update =="
apt-get update

echo
echo "== Packages available to upgrade =="
PENDING=$(apt list --upgradable 2>/dev/null | tail -n +2)
echo "$PENDING"
TOUCHES_KERNEL_OR_NVIDIA=0
if echo "$PENDING" | grep -qiE "^linux-(image|headers|libc-dev)|nvidia|libegl-nvidia"; then
    TOUCHES_KERNEL_OR_NVIDIA=1
    echo
    echo "!! This upgrade touches the kernel and/or NVIDIA/EGL packages — the shared-risk"
    echo "   path. Don't run this mid VR-test-session (confounds debugging either project,"
    echo "   same rule reverb-g2 already follows). A reboot + the post-update check below"
    echo "   will be required, not optional, before trusting either project again."
fi

echo
echo "== apt-get upgrade (will prompt for confirmation) =="
apt-get upgrade

echo
echo "== Post-check: NVIDIA driver pin still installed? =="
if dpkg -l "$PIN_PKG" 2>/dev/null | grep -q ^ii; then
    echo "OK — $PIN_PKG still installed."
else
    echo "WARNING: $PIN_PKG is gone after the upgrade — check 'nvidia-smi' and" >&2
    echo "'dpkg -l | grep nvidia-driver' by hand before trusting this rig's driver state." >&2
fi

echo
echo "== Kernel check =="
echo "Running kernel:  $(uname -r)"
echo "Installed kernels:"
dpkg -l | grep linux-image | awk '{print "  " $2 " " $3}'
echo
echo "Do NOT 'apt autoremove --purge' old linux-image-* packages until the new kernel is"
echo "confirmed solid — that deletes the rollback (reverb-g2's own rule, applies here too)."

if [[ -f /var/run/reboot-required ]] || [[ "$TOUCHES_KERNEL_OR_NVIDIA" == "1" ]]; then
    echo
    echo "== Reboot required before trusting this state =="
    echo "After rebooting, run:"
    echo "  $HERE/post_update_driver_check.sh"
    echo "before trusting Resolve OR reverb-g2 on this build. If it fails: GRUB ->"
    echo "'Advanced options for Debian GNU/Linux' -> the previously-running kernel."
else
    echo
    echo "== No kernel/NVIDIA/EGL packages touched — routine update, no reboot/re-verify needed. =="
fi
