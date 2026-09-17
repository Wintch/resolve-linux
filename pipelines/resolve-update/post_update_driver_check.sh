#!/usr/bin/env bash
# Run AFTER rebooting from an update_os.sh run that touched the kernel or NVIDIA/EGL
# packages. Refuses to just say "looks fine" — checks the reboot actually landed on the
# newest kernel (reverb-g2 has been bitten by a stale GRUB default before, docs/06), then
# delegates the DKMS/patch/drift verification to reverb-g2's own pre-update-check.sh
# rather than re-deriving 595.71.05-specific patch names in this repo (that script reports
# current state regardless of framing, so it's reusable post-reboot as-is).
set -euo pipefail

# ROOT-ONLY VARIANT (2026-09-11): no working sudo for 'iam' on this rig -- hardcoded to
# /home/iam rather than trusting $HOME, which would be /root when run as root.
REVERB_CHECK="/home/iam/Documents/reverb-g2/scripts/pre-update-check.sh"

echo "== 1: did the reboot actually pick up the newest installed kernel? =="
CURRENT=$(uname -r)
LATEST=$(dpkg -l 'linux-image-6.*-amd64' 2>/dev/null | awk '/^ii/{print $2}' | sed 's/linux-image-//' | sort -V | tail -1)
echo "  running:          $CURRENT"
echo "  newest installed: $LATEST"
if [[ "$CURRENT" == "$LATEST" ]]; then
    echo "  OK"
else
    echo "  !! Running an OLDER kernel than what's installed — the reboot may not have picked"
    echo "     up the new one (stale GRUB default has bitten reverb-g2 before). Check"
    echo "     /etc/default/grub's GRUB_DEFAULT and 'update-grub' if it needs fixing."
fi

echo
echo "== 2: shared driver/DKMS/patch/drift check (reverb-g2's own script, reused) =="
if [[ -x "$REVERB_CHECK" ]]; then
    "$REVERB_CHECK"
else
    echo "  !! $REVERB_CHECK not found — can't verify the shared driver's patch state."
    echo "     At minimum by hand: 'dkms status' shows the module installed for $CURRENT,"
    echo "     and 'grep PATCH\\[ /usr/src/nvidia-595.71.05/dkms.conf' shows all 4 lines."
fi

echo
echo "== Now validate BOTH projects that share this driver before trusting the update =="
echo "  - Resolve: open a project, edit, render a frame — a process launching or a clean"
echo "    log is not evidence, per this project's own rule."
echo "  - reverb-g2, if VR is planned soon: a real jack-in-wayland.sh session at 90Hz."
echo "If either looks wrong: reboot, GRUB -> 'Advanced options for Debian GNU/Linux' ->"
echo "the previously-running kernel, then debug without time pressure (rollback.sh in this"
echo "same directory handles the Resolve app version specifically, not the kernel)."
