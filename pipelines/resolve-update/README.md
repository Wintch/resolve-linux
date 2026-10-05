# resolve-update

Procedure + scripts to update this rig from **Debian 13 (trixie), DaVinci Resolve Studio
21.0.4-mrd1.10.0** (the currently-validated, hands-on-tested state) to a newer Resolve —
concretely, Resolve **21.1**, which shipped 2026-09-08 with a native MCP server (see the
parent repo's discussion of that release) — with a rollback path back to the exact tested
build at every stage.

## The honest limit on "rollback, always" first

This rig's root filesystem is a single ext4 LV (`iashur-vg/root`, 211G) inside a 222.6G
LUKS-encrypted VG with **16MB of free VG space** (`vgs` confirms it) and 87% of the
filesystem already used. That means:

- **No LVM snapshot and no btrfs snapshot are possible right now** — there's no free
  space to hold one, and the filesystem is ext4, not btrfs. A true "revert the whole disk
  to this exact point in time" rollback does not exist on this rig as currently
  partitioned, and building one (shrinking the root LV+filesystem from a live boot, or
  adding another disk for a full image backup) is a separate, deliberate project of its
  own — not bundled into this one.
- What **is** achievable, and what this pipeline actually gives you:
  - **Resolve app rollback**: exact `.deb` packages of the currently-installed
    21.0.4-mrd1.10.0 build, repacked from the live system with `dpkg-repack` (the original
    `.run` installer is no longer on disk — only the `makeresolvedeb` wrapper script
    survived in `~/resolve-install/` — so this repack is the *only* way back to this exact
    build without re-downloading from Blackmagic).
  - **Resolve project/config rollback**: a snapshot via the existing
    [`resolve-backup/backup_resolve_config.py`](../resolve-backup/backup_resolve_config.py)
    (project library, preferences, Fusion/Fairlight user state).
  - **Kernel rollback**: this rig already keeps multiple old kernel images installed
    (6.12.94, .100, .101, .107 were all present as of 2026-09-11) — Debian does this by
    default and nothing here changes that. If a new kernel from the OS update causes
    problems, boot into an older one from GRUB's "Advanced options for Debian GNU/Linux"
    submenu (hold Shift during boot, or Esc if using UEFI fast boot).
  - **NVIDIA driver**: pinned at 595.71.05 via the installed
    `nvidia-driver-pinning-595.71.05` package — a plain `apt upgrade` will **not** bump the
    driver version even though newer ones (595.91.07, 610.x) are in the CUDA repo. DKMS
    (`nvidia-kernel-open-dkms`) already auto-rebuilds the `nvidia.ko` module for each new
    kernel — confirmed present for all 4 kernel versions on disk — so a kernel bump alone
    shouldn't break the driver. Still worth a hands-on check per this project's own rule:
    a module loading is not the same as Resolve actually rendering correctly.

### The driver is shared with reverb-g2 — treat this as a two-project risk, not one

This exact rig's `/usr/src/nvidia-595.71.05/dkms.conf` carries **reverb-g2's 4 kernel
patches** for the HP Reverb G2 90Hz fix (confirmed live, `PATCH[0..3]` all present). A
kernel or NVIDIA/EGL package bump from `update_os.sh` triggers the *same* DKMS rebuild that
project depends on — this is not a hypothetical, `reverb-g2/docs/24-safe-system-updates.md`
already documents losing a session to trusting a rebuild blindly, and
`reverb-g2/docs/73-nvidia-symlink-drift.md` found real driver-owned files (GLX, NVDEC,
VDPAU, CUDA symlinks) silently deleted from disk with zero warning from `apt`/`dpkg -l`/
`dkms status` — only `dpkg -V` caught it, and it broke a Resolve-adjacent X11 session too.

So `update_os.sh` and `post_update_driver_check.sh` don't re-implement drift/DKMS/patch
checking — they call `~/Documents/reverb-g2/scripts/pre-update-check.sh` directly (same
"reuse, don't fork" relationship `resolve_power.py` and `backup_resolve_config.py` already
have with that project). Concretely, that means:

- **Before any upgrade that might touch the kernel/NVIDIA/EGL**: `update_os.sh` runs
  reverb-g2's pre-check first — it reports current kernel/DKMS/patch state, flags silent
  on-disk drift (`dpkg -V`), and confirms a rollback kernel actually has a working DKMS
  build (not just an installed image, which is a distinction that has bitten that project
  before).
- **After rebooting into a new kernel**: run `./post_update_driver_check.sh` — confirms the
  reboot really landed on the new kernel (a stale GRUB default has silently kept the old
  one running before) and re-runs the same shared check.
- **Don't `apt autoremove --purge` old kernel images** until the new one is proven — same
  rule reverb-g2 already follows, it deletes the rollback.
- **Don't run the OS update mid a VR test session** (or mid a Resolve validation session,
  symmetrically) — confounds debugging whichever project you were already in.
- Trusting the update means validating **both** projects by hand afterward, not just
  Resolve — a clean `post_update_driver_check.sh` run is necessary, not sufficient, exactly
  like reverb-g2's own rule for its VR checks.

If you want real point-in-time full-system rollback later, that means freeing VG space
(shrink the root filesystem+LV from a live/rescue boot) or backing up a full disk image to
external storage first — flag it separately if you want that built.

## Disk safety on the actual install step, not just the build

`/opt/resolve` lives on the root LV too (confirmed 2026-09-11, no separate mount) —
currently 35G, of which 23G is `Extras` (the AI packs, downloaded by Resolve's own Extras
Download Manager at runtime, **not** owned by either `.deb` per `dpkg -L` — confirmed it
survives an upgrade untouched, so it's not part of the real disk delta). The other ~12G is
what the package actually reinstalls. Moving the `.run` to `/mnt/resolve_test` (per
`install_resolve.sh`) reclaims ~11G on root before the build even starts, but the final
`sudo apt install` step still writes the new build into `/opt/resolve` on that same
92%-full root LV — a disk-full mid-unpack there would leave Resolve half-installed, not
just a failed command.

`install_resolve.sh` now measures this for real instead of guessing: makeresolvedeb
doesn't set an `Installed-Size` control field (checked — `dpkg -s`/`dpkg-query` return
nothing for it), so the script sums real byte sizes from the built `.deb`s'
`dpkg-deb -c` listing, compares against `/opt/resolve` minus `Extras`, and refuses to run
`apt install` if the net-new space needed plus a 2G margin doesn't fit in what's free on
`/` — printing the exact numbers either way, not just "should be fine."

## Rollback bundle location

Everything this pipeline produces goes to `/mnt/resolve_test/resolve-update-rollback/` —
the spare 488G ext4 partition on the second NVMe disk (`nvme0n1p3`), not the tight root LV.
Each `preflight_backup.sh` run creates a new timestamped subfolder; old ones are cheap to
keep (a couple hundred MB each) and are not pruned automatically.

## Root-only variant (this rig, right now)

There's no working `sudo` for the `iam` user on this rig (2026-09-11) — every script here
was rewritten to run **directly as root**, no `sudo` anywhere in them, with every path
that used to trust `$HOME` hardcoded to `/home/iam` instead (root's `$HOME` is `/root`,
and this exact mismatch silently emptied the config/project snapshot twice before this
was caught — `Path.home()` resolving to `/root` instead of the real user's data, no
error, just an empty destination). Run each script as root, e.g.:

```bash
su -           # or however you get a root shell
./preflight_backup.sh
```

If `sudo` for `iam` gets fixed later, these scripts still work as root — they just no
longer *require* it, and there's no more of the "don't run as root" guard this repo tried
first (it correctly rejected the wrong invocation, but couldn't fix it, since there was no
right invocation available here).

## Procedure

```bash
cd pipelines/resolve-update

# 1. Snapshot the current tested state — Resolve .debs (via dpkg-repack) + config/project
#    library. Safe, additive, no system changes beyond installing dpkg-repack itself.
./preflight_backup.sh

# 2. OS package update (apt upgrade, not dist-upgrade/full-upgrade — deliberately
#    conservative, won't remove packages to satisfy new dependencies). Runs reverb-g2's
#    shared driver/DKMS/drift check first, checks the NVIDIA pin survives, and reports
#    whether a reboot is needed.
./update_os.sh

# 2b. Only if update_os.sh touched the kernel or NVIDIA/EGL packages: reboot, then
#     verify the shared driver (reused reverb-g2 check, not a Resolve-only check) before
#     trusting anything else in this procedure.
./post_update_driver_check.sh

# 3. The one step Blackmagic makes unscriptable (see the parent README's "Blackmagic
#    gates the Linux download behind a free-account login" finding): log in with the
#    account tied to the Studio dongle and download the Resolve 21.1 .run installer
#    manually from the forum/support site. Before running makeresolvedeb against it, check
#    https://www.danieltufvesson.com/makeresolvedeb for a version confirmed against 21.1 —
#    the 1.10.0 copy in ~/resolve-install/ was only ever validated against 21.0.4.

./install_resolve.sh /path/to/DaVinci_Resolve_Studio_21.1_Linux.run

# 4. Open Resolve, hands-on verify (per this project's standing rule: a process launching
#    is not evidence — only actual editing/rendering is). If something's wrong:

./rollback.sh                      # reinstalls the archived 21.0.4-mrd1.10.0 .debs
# and/or, for a bad kernel: reboot, hold Shift, pick the previous kernel from
# "Advanced options for Debian GNU/Linux" in GRUB.
```

## What's NOT handled here

- Reinstalling the MCP tooling / MCP-CAPABILITIES.md tool surface against a rolled-back
  Resolve version — that's a separate check, do it by hand after either an upgrade or a
  rollback.
- Testing Resolve 21.1's *native* MCP server itself once installed — separate follow-up,
  and per the parent discussion, not a replacement for the existing custom MCP bridge
  until proven out on this rig specifically (Blackmagic only lists Rocky Linux 8.6 as
  supported Linux for 21.1; Debian works in practice for the rest of Resolve via
  `makeresolvedeb` but is unverified for the new MCP feature specifically).
- Full VR-hardware verification (`verify-bpc.sh`, `preflight.sh`, an actual headset
  session) is `reverb-g2`'s own territory, not duplicated here — `post_update_driver_check.sh`
  only covers the driver/kernel/DKMS layer both projects share. Run `reverb-g2`'s own
  `post-update-verify.sh` too before a VR session, not just this pipeline's check.

## Status log

### 2026-10-04 — Resolve 21.1.1 staged, install NOT run yet

Installed now: `21.1-mrd1.10.1` (Resolve 21.1.0.17). Target: **21.1.1**
(zip downloaded manually; `unzip -t` clean).

- Rollback bundle for the current build already exists:
  `/mnt/resolve_test/resolve-update-rollback/20261004-192249/` (the two
  `21.1-mrd1.10.1` `.deb`s + config snapshot). `preflight_backup.sh` does
  not need to be re-run.
- Zip moved off the root LV (95% used, 11G free at the time) to
  `/mnt/resolve_test/resolve-update-staging/` and extracted there; the
  `.run` is `DaVinci_Resolve_Studio_21.1.1_Linux.run` (11.2GB). Root went to
  21G free after the move.
- `makeresolvedeb` 1.10.1 is still the latest (2026-09-10). Its page does
  not mention 21.1.1 specifically; it claims to handle all releases up to
  its date. Untested against 21.1.1 on this rig.
- **Trap found:** `install_resolve.sh` installs every
  `davinci-resolve-studio*.deb` at the top level of the build dir, and the
  21.1 `.deb`s from 2026-09-11 were still there. Left alone, `apt install`
  would be handed two versions of the same package. They were moved to
  `/mnt/resolve_test/resolve-update-build/stale-21.1-debs/` and the script
  now refuses to run if any `.deb` is already present in the build dir.
  The old 21.1 `.run` (11GB) is still in the build dir; the script does not
  touch it.
- `sudo` still asks for a password for `iam`, so the install must be run
  as root (`su -`).

### 2026-10-05 — Resolve 21.1.1 installed and live-validated
 
- `install_resolve.sh` run successfully with `DaVinci_Resolve_Studio_21.1.1_Linux.run`.
- Deb packages built and installed cleanly:
  - `davinci-resolve-studio` `21.1.1-mrd1.10.1`
  - `davinci-resolve-studio-data` `21.1.1-mrd1.10.1`
- Binary version verified: `DaVinci Resolve Studio Version 21.1.1.0010`.
- `post_update_driver_check.sh`: all checks passed (Kernel 6.12.111, NVIDIA 595.71.05-1 DKMS installed, all 4 patches applied, rollback kernel readiness confirmed).
- Full live verification battery against Resolve 21.1.1 and community MCP v4.8.28:
  - `validate_client.py` (headless): **ALL 17 CHECKS PASSED** (timeline editing, batch clip rename, LUT roundtrip, media import, native still grab, render + loudness gate, negative assertion checks).
  - `bench_01_simple.py`: 4 read ops x 10 reps clean on both native and community servers.
  - `bench_02_batch_rename.py`: batch write passed, clip reset confirmed.
  - `grain_timeline_toggle.py`: run on real project `test1`, Film Grain node toggle confirmed by pixel diff (3,223 px changed, 0 px on restore).
  - `bench_03_subtitles.py`: neural speech-to-subtitles (`Timeline.CreateSubtitlesFromAudio`) verified (9 subtitle items generated, 564.6ms).
  - `render_benchmark.py --list-presets`: all 31 delivery presets listed and ready.

