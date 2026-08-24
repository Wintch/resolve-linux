# DaVinci Resolve (Studio) on Linux — Debian 13, NVIDIA 595-open

Rig: `iashur`, dedicated Debian 13 box also used for an unrelated VR project (see
"Why this matters" at the bottom). Path used: [makeresolvedeb](https://www.danieltufvesson.com/makeresolvedeb),
which converts Blackmagic's official `.run` installer into clean `.deb` packages for
Debian — Blackmagic doesn't ship a native `.deb`.

## Status (2026-08-24)

- **Activation dongle confirmed present and genuine**: `lsusb` shows `ID 096e:0201
  Feitian Technologies, Inc. USB DONGLE` — Feitian is Blackmagic's actual OEM hardware
  vendor for the Resolve Studio dongle, not a generic/clone brand.
- **NVIDIA driver already satisfies Resolve's GPU requirement**: `595.71.05`
  (`nvidia-open` + `nvidia-driver-cuda` + `nvidia-kernel-open-dkms`), `nvidia-smi`
  confirms an RTX 3060 Ti with CUDA 13.2 live. No extra driver install needed.
- **Installer**: `DaVinci_Resolve_Studio_21.0.4_Linux.zip`, downloaded from Blackmagic's
  site (login-gated — see "The one manual step" below), unzipped to a `.run` (~11GB).
- **`makeresolvedeb` v1.10.0** fetched from `danieltufvesson.com` (no login wall there,
  only Blackmagic's own download requires an account). Auto-detects Free vs. Studio
  from the archive, no special flag needed.
- **Conversion in progress / build details below.**

## The one manual step that can't be scripted around

Blackmagic gates the Linux download behind a free-account login on
[blackmagicdesign.com/support/family/davinci-resolve-and-fusion](https://www.blackmagicdesign.com/support/family/davinci-resolve-and-fusion).
Log in with the account tied to your Studio dongle and download **DaVinci Resolve
Studio for Linux** — not Free. Two reasons Studio matters here specifically: it's the
edition that will actually be used, so it's the one worth validating; and **Studio
decodes H.264/HEVC/AAC natively**, so the DNxHR-transcode workaround below (a real
limitation of the Free edition) very likely doesn't apply to Studio — not independently
confirmed yet with real footage, don't assume either way until checked.

## Install

**Gotcha, found live**: `makeresolvedeb` requires the `.run` file to be in the same
working directory the script is invoked from — it refuses ("does not exist or is
located outside the working directory") if you pass a path elsewhere, even an absolute
one. Copy the script alongside the `.run` first.

```bash
unzip DaVinci_Resolve_Studio_*_Linux.zip        # produces the .run + an install-instructions HTML
cp /path/to/makeresolvedeb_*.sh .               # must be in the SAME dir as the .run
./makeresolvedeb_*.sh DaVinci_Resolve_Studio_*_Linux.run
sudo dpkg -i davinci-resolve-studio_*.deb davinci-resolve-studio-data_*.deb   # both packages
```

**Disk space, found live**: the script does a real `cp -rp` (not a hardlink) to build
each package's staging tree from the unpacked `.run` payload — budget roughly 2-3x the
`.run`'s size in free scratch space during the build (an 11GB `.run` unpacks to ~14GB,
then gets copied again into the package trees). The original downloaded `.zip` is safe
to delete immediately after `unzip` succeeds — nothing downstream reads it again, only
the `.run` and the unpacked tree matter from that point on. Delete it early if space is
tight rather than waiting for the whole build to finish.

GPU requirement (general case, already satisfied on this rig — see Status above): any
Debian with `nvidia-open` + `nvidia-driver-cuda` finds CUDA with no extra steps.

## The limitation you need to know about (Free edition on Linux only)

**Resolve Free on Linux does NOT decode H.264/HEVC or AAC** (Studio does). Typical
camera/phone footage comes in silent or doesn't come in at all. Workaround: transcode
to DNxHR first — NVDEC hardware decode makes this nearly free on CPU:

```bash
ffmpeg -hwaccel cuda -i input.mp4 \
       -c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p \
       -c:a pcm_s16le output.mov
```

DNxHR HQ 4K ≈ 700 GB/hour — plan storage accordingly (a native partition beats a
cross-filesystem NTFS mount for sustained write throughput at that rate).

## davinci-resolve-mcp — AI-driven control, not tried yet

[samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)
exposes Resolve's official scripting API (project/timeline/color/Fairlight/render/Fusion)
to an MCP client. Confirmed to support Linux + Resolve Studio 18.5+ via the app's
external-scripting "Local" mode (Free edition needs an in-app bridge script instead,
launched from Workspace → Scripts). Install: `npx davinci-resolve-mcp setup`, or clone +
`python install.py`. Could let an AI session drive or verify parts of a Resolve session
programmatically instead of only reading screenshots — worth setting up once Resolve
itself is confirmed working, not before. Not installed or tested yet as of this writing.

## Why this matters (context, not a how-to)

Resolve was already validated working on the user's main system. This separate rig
re-validates it specifically on the patched NVIDIA 595-open driver used for an unrelated
VR headset project running on the same machine — the goal is to catch any
artifacts/instability under that driver BEFORE the user migrates their main system to
it. **Visual/editing-quality verification needs the user's own eyes** — a process that
launches and stays up is not the same as "it works well"; don't claim the latter from
logs or a process check alone.

There's also a longer-term, not-yet-scoped idea: eventually let this machine be rented
out (via a separate tool, "pmadminka") for either a gaming session or a Resolve editing
session. Irrelevant to getting the install itself working, just context for why Resolve
running well on *this specific* machine matters beyond one person's own use.
