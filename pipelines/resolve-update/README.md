# resolve-update

Procedure and scripts to move a Debian install of DaVinci Resolve Studio (built with
[makeresolvedeb](https://www.danieltufvesson.com/makeresolvedeb)) to a newer Resolve
release, with a way back to the exact build you had at every stage.

## What "rollback" can and can't mean

A true point-in-time rollback of the whole system needs an LVM or btrfs snapshot. On a
common Debian layout (ext4 root on an LVM volume group with no free extents, often inside
LUKS) there is no room for one, and making room (shrinking the root filesystem and LV from
a rescue boot, or imaging the disk to external storage) is a separate project. Check with
`vgs` before assuming you have it.

What this pipeline gives you without a snapshot:

- **Resolve app rollback**: the currently-installed `davinci-resolve-studio` and
  `davinci-resolve-studio-data` packages, repacked from the live system with
  `dpkg-repack`. If the original `.run` is gone, this is the only way back to the exact
  build without downloading it again from Blackmagic.
- **Project/config rollback**: a snapshot via
  [`resolve-backup/backup_resolve_config.py`](../resolve-backup/backup_resolve_config.py)
  (project library, preferences, Fusion/Fairlight user state).
- **Kernel rollback**: Debian keeps older kernel images installed; boot one from GRUB's
  "Advanced options for Debian GNU/Linux" (hold Shift, or Esc with UEFI). Don't
  `apt autoremove --purge` old kernels until the new one is proven.

If your NVIDIA driver carries local DKMS patches or is pinned to a version, check that the
patches and the pin survive before and after any kernel or driver update (`dkms status`,
`dpkg -V` on the driver packages); `PRE_UPDATE_HOOK` (below) is the place to run such a
check automatically.

## Disk space

- **Build**: the `.run` is about 11 GB and makeresolvedeb's unpack dir plus the two
  resulting `.deb`s roughly double it. Put `RESOLVE_UPDATE_DIR` on a disk with tens of GB
  free, not a tight root filesystem.
- **Install**: `/opt/resolve` is rewritten in place. `/opt/resolve/Extras` (the AI packs,
  often over 20 GB) is downloaded at runtime by Resolve's Extras Download Manager, is not
  owned by either package, and survives an upgrade. `install_resolve.sh` sums the real
  payload of the built `.deb`s (makeresolvedeb sets no `Installed-Size`), compares it with
  `/opt/resolve` minus `Extras`, and refuses to run `apt install` without the net-new space
  plus a 2 GB margin: a disk-full mid-unpack leaves Resolve half-installed.
- **Rollback bundles** are a few hundred MB each, timestamped, and never pruned
  automatically.

## Configuration

The scripts run as root (they call `dpkg`/`apt` directly, no `sudo` inside), but Resolve's
data lives in the desktop user's home, and root's `$HOME` is `/root`: trusting it would
snapshot an empty directory without any error. So the user is explicit (see `lib.sh`):

| Variable | Meaning | Default |
|---|---|---|
| `TARGET_USER` | desktop user whose Resolve data is backed up; owner of the output | `$SUDO_USER` |
| `RESOLVE_UPDATE_DIR` | where `resolve-update-build/` and `resolve-update-rollback/` go | `~TARGET_USER/resolve-update` |
| `MAKERESOLVEDEB` | path to the makeresolvedeb script (`install_resolve.sh`) | required |
| `PRE_UPDATE_HOOK` | optional command run right before `apt install`; non-zero aborts | unset |

## Procedure

```bash
cd pipelines/resolve-update
export TARGET_USER=alice RESOLVE_UPDATE_DIR=/mnt/bigdisk

# 1. Snapshot the current, tested state: repacked .debs + config/project library + a
#    manifest (packages, kernel, driver, disk and LVM state). Additive; installs only
#    dpkg-repack if missing.
sudo -E ./preflight_backup.sh

# 2. Download the new .run by hand. Blackmagic gates the Linux download behind a
#    free-account login, so this can't be scripted. Check that your makeresolvedeb release
#    supports that Resolve version (1.10.1 is needed for 21.1: it packages the new
#    Immersive directory and the bundled ResolvePython).

# 3. Build and install. Refuses to run with stale .debs in the build dir (apt would get
#    two versions of the same package) or without enough disk space.
sudo -E MAKERESOLVEDEB=/path/to/makeresolvedeb_1.10.1_multi.sh \
    ./install_resolve.sh /path/to/DaVinci_Resolve_Studio_<version>_Linux.run

# 4. Verify by hand: open a project, edit, render a frame. A process that launches is not
#    evidence that it works. Re-run the MCP regression (pipelines/mcp-benchmark/) too.
#    If something is wrong:
sudo -E ./rollback.sh            # reinstalls the newest bundle's .debs (or pass a bundle dir)
```

`rollback.sh` does not restore project files automatically (that could overwrite newer
work); it prints the `cp` command to use if the older Resolve cannot open projects saved
by the newer one.

## Not handled here

- OS package upgrades and driver updates: do them separately, with your own checks, not
  in the same session as a Resolve upgrade (it confounds debugging).
- Re-validating the MCP tooling and the capability catalog against the new or
  rolled-back Resolve: run `pipelines/mcp-benchmark/validate_client.py` and refresh
  [MCP-CAPABILITIES.md](../../MCP-CAPABILITIES.md) by hand.
