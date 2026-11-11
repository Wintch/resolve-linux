# Shared settings for the resolve-update scripts. Sourced, not run.
#
# These scripts run as root (dpkg/apt), but Resolve's config and projects live in the
# desktop user's home. Root's $HOME is /root, so trusting $HOME or Path.home() would
# silently snapshot an empty directory. The target user is therefore explicit:
#
#   TARGET_USER         the user whose Resolve data is backed up (default: $SUDO_USER)
#   RESOLVE_UPDATE_DIR  where build dirs and rollback bundles go
#                       (default: $TARGET_HOME/resolve-update). Point it at a disk with
#                       tens of GB free: the .run is ~11 GB and the build roughly doubles it.
#   MAKERESOLVEDEB      path to the makeresolvedeb script (install_resolve.sh only)
#   PRE_UPDATE_HOOK     optional command run before installing, e.g. a check of local
#                       DKMS patches on the NVIDIA driver; a non-zero exit aborts

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (sudo, or su -): these scripts call dpkg/apt directly." >&2
    exit 1
fi

TARGET_USER="${TARGET_USER:-${SUDO_USER:-}}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    echo "Set TARGET_USER to the desktop user whose Resolve data should be used" >&2
    echo "(e.g. TARGET_USER=alice $0 ...). It cannot be root." >&2
    exit 1
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
    echo "No home directory found for user '$TARGET_USER'." >&2
    exit 1
fi

RESOLVE_UPDATE_DIR="${RESOLVE_UPDATE_DIR:-$TARGET_HOME/resolve-update}"
ROLLBACK_ROOT="$RESOLVE_UPDATE_DIR/resolve-update-rollback"
BUILD_ROOT="$RESOLVE_UPDATE_DIR/resolve-update-build"
