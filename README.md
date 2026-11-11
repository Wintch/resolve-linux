# resolve-linux

Automating video editing on Linux with [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve)
Studio and its scripting API (directly and through MCP servers), plus small ffmpeg/Python tools for the parts that
prepare footage for Resolve. Install notes, Linux-specific gotchas, the codec picture, MCP capability mapping and
the pipelines live together in one repo.

## Goal

Get Resolve on Linux solved and ready to work with: install, gotchas, and automation wherever it makes sense. For
each editing task covered, the aim is:

1. **A guide for doing it well in Resolve itself**, including Resolve's own AI-assisted features.
2. **Automation of the same task** through the scripting API or MCP, so it does not have to be redone by hand.
3. **A Resolve-free path** where one exists and is worth having (for example `prepare-for-resolve`, which fixes
   footage with ffmpeg before import).
4. **Free vs. Studio noted explicitly** wherever a feature is Studio-gated.

## Documents

| File | What |
|---|---|
| this README | install on Debian, GPU/codec findings, known limitations, scripting/MCP overview, plugins |
| [MCP-CAPABILITIES.md](MCP-CAPABILITIES.md) | the full tool → action catalog of the community MCP server and the vendor's own `ResolveMCP` |
| [SCRIPTING-API-21.md](SCRIPTING-API-21.md) | which Resolve 21.x scripting API additions work when exercised live, which hang or stay inconclusive |
| [PERFORMANCE.md](PERFORMANCE.md) | hardware performance guide: storage and filesystems, RAM, cache placement, proxies, VRAM, swap |
| [pipelines/](#pipelines) | the tools, one folder each (see below) |

## Reference machine

All measurements in this repo come from one machine: **RTX 3060 Ti 8 GB, Ryzen 5 5600X, 32 GB RAM, Debian 13,
NVIDIA 595 driver (open kernel module)**, Resolve Studio 21.x installed with `makeresolvedeb`, editing media on a
SATA SSD and an NVMe drive. Numbers are for orientation; re-measure on other hardware.

## Install on Debian

Blackmagic only supports Rocky Linux 8.6 and ships no `.deb`.
[makeresolvedeb](https://www.danieltufvesson.com/makeresolvedeb) converts the official `.run` installer into two
Debian packages. Works in practice, not vendor-supported.

**Download (the one step that cannot be scripted).** Blackmagic gates the Linux download behind a free-account login
on [blackmagicdesign.com/support](https://www.blackmagicdesign.com/support/family/davinci-resolve-and-fusion). Log in
with the account tied to the Studio dongle and download **DaVinci Resolve Studio for Linux**, not Free (see
[Codecs on Linux](#codecs-on-linux) for why Studio matters).

```bash
unzip DaVinci_Resolve_Studio_*_Linux.zip        # the .run + an install-instructions HTML
cp /path/to/makeresolvedeb_*.sh .               # must be in the SAME directory as the .run
./makeresolvedeb_*.sh DaVinci_Resolve_Studio_*_Linux.run
sudo dpkg -i davinci-resolve-studio_*.deb davinci-resolve-studio-data_*.deb   # both packages
sudo apt install -y libglu1-mesa                # libGLU.so.1 is NOT optional
```

Gotchas:

- **`makeresolvedeb` refuses a `.run` outside its working directory**, even by absolute path ("does not exist or is
  located outside the working directory"). Copy the script next to the `.run`.
- **`libGLU.so.1` (`libglu1-mesa`) is required and undocumented**: without it the binary fails at load time with
  "error while loading shared libraries".
- **Disk space**: the script `cp -rp`s the unpacked payload into each package tree. Budget 2-3× the `.run` size of
  scratch space (an 11 GB `.run` unpacks to ~14 GB, then is copied again). The `.zip` can be deleted right after
  `unzip`.
- **The two packages**: `davinci-resolve-studio` (~2.9 GB) is the program; `davinci-resolve-studio-data` (~5 GB) is
  LUTs, Fusion/Fairlight templates and presets. Both are required.
- **Only one `.deb` set in the build directory**: stale packages from earlier builds lead to mixed-version installs.
  `pipelines/resolve-update/install_resolve.sh` checks for this.
- **The Studio dongle** (USB, Feitian `096e:0201`, Blackmagic's OEM) is picked up with no extra setup.
- **A Media Storage location is mandatory on first run** (Preferences → Media Storage). It also limits where imports
  and renders can go; see [Known limitations](#known-limitations-on-linux).
- **GNOME false "not responding" dialogs** under heavy Resolve load: Mutter pings windows every 5 s.
  `gsettings set org.gnome.mutter check-alive-timeout 20000` applies live.
- **Use a native X11 session**, not Wayland. See [GPU detection](#gpu-detection-x11-vs-wayland).

Updating Resolve later, with a rollback path: [pipelines/resolve-update/](pipelines/resolve-update/).

### Official requirements vs. the reference machine

Blackmagic's Linux spec (Rocky Linux 8.6, Resolve Studio 21):

| Requirement | Official minimum | Reference machine | OK? |
|---|---|---|---|
| OS | Rocky Linux 8.6 | Debian 13 via makeresolvedeb | unofficial, works |
| RAM | 32 GB | 32 GB | yes |
| VRAM, baseline | discrete GPU, ≥ 4 GB | 8 GB | yes |
| VRAM, AI tools | ≥ 16 GB | 8 GB | **no** |
| Background render | ≥ 32 GB RAM + ≥ 12 GB VRAM | 32 GB + 8 GB | **no** (VRAM) |
| GPU compute | OpenCL 1.2 or CUDA 12.8 | CUDA 13.2 | yes |
| NVIDIA driver | Studio driver ≥ 580.119.02 | 595.71.05 | yes |

With 15 GB of RAM, Resolve's main process alone held ~4.6 GB on the lightest possible timeline (one 1080p clip, no
effects) and swap kept growing: below 32 GB, expect pressure on real projects. 8 GB of VRAM is a hard ceiling for
the AI tools: GPU memory errors are the root cause of several scripting failures (see
[SCRIPTING-API-21.md](SCRIPTING-API-21.md)), and other GPU workloads on the same card make them worse.

## GPU detection: X11 vs Wayland

**The single most important Linux finding.** Under Wayland (Resolve runs as a rootless Xwayland client), Resolve's
`GPUDetect` cannot correlate the CUDA device with the NVML/OpenCL one. It falls back to OpenCL silently, and then:

- the H.264/H.265 NVIDIA encoders disappear from `Project.GetRenderCodecs()` in every container;
- renders livelock: `CompletionPercentage` stuck at 0, `EstimatedTimeRemainingInMs` climbing without bound, one
  thread at ~100 % CPU spinning inside `libnvidia-opencl.so.1` (`gdb -p <pid> -batch -ex "thread apply all bt"`),
  no output file, `StopRendering()` does not unstick it. Only a kill and relaunch recovers.

The CUDA stack itself is healthy in that state (calling `cuInit`/`cuDeviceGetPCIBusId` from Python `ctypes` works);
the failure is inside Resolve's bundled `libgpudetect.so` ("Failed to find CUDA Driver API device ID", preceded by
"No Main Display GPU found and no monitors found to match").

**Diagnose** in `~/.local/share/DaVinciResolve/logs/ResolveDebug.txt` at launch:

| Log line | Meaning |
|---|---|
| `supports CUDA 13.2` … `Matches: CUDA, NVML, OpenCL, XOrg` … `defaulting to CUDA` | good |
| `Matches: NVML, OpenCL` (no CUDA, no XOrg) | Wayland/Xwayland: GPU not correlated |
| `supports CUDA -1.-1` | the CUDA driver library is not up at all; switching session type alone will not fix it |

**Fix: log into a native X11 session and do a full reboot.** Switching session type without a reboot was not enough
(CUDA stayed at `-1.-1`). After a reboot straight into X11 it is reproducible, and it does not depend on the desktop:
GNOME on Xorg and KDE Plasma on X11 behave identically. With CUDA detected, ProRes 422 HQ and H.264/H.265 NVIDIA
renders complete normally. There is no client-side fix for Wayland; re-test when Blackmagic ships a `GPUDetect` change.
Swapping the open kernel module for the proprietary one was not needed.

**Scripts started outside the desktop session** (SSH, MCP clients, systemd) need `DISPLAY` and `XAUTHORITY` or Qt
aborts at launch ("Authorization required, but no authorization protocol specified", `SIGABRT` in
`QApplicationPrivate::init`). Under Wayland the cookie is `/run/user/<uid>/.mutter-Xwaylandauth.<random>` (new name at
every login) and the display number is not always `:0`: take `DISPLAY` from the Xwayland process that owns the cookie,
or an `Invalid MIT-MAGIC-COOKIE-1 key` makes Resolve exit during Qt init and the client only sees a timeout.
`pipelines/mcp-benchmark/bench_lib.resolve_env()` and `resolve_mcp_wrapper.sh` do this. Running Resolve in a private
`Xvfb` display avoids the question entirely (see [Scripting API and MCP](#scripting-api-and-mcp)).

## Codecs on Linux

From Blackmagic's "Supported Formats and Codecs" (Rocky Linux table), confirmed with real footage:

| Codec | Linux, any edition | Notes |
|---|---|---|
| H.264 decode/encode | **Studio only**, NVIDIA GPU-accelerated | no CPU fallback on Linux (macOS/Windows have one) |
| H.265/HEVC decode/encode | **Studio only**, NVIDIA GPU-accelerated | same shape as H.264 |
| **AAC** (aac/m4a) | **unsupported, decode and encode** | also excluded as embedded audio in containers (PCM/mp3/flac only) |
| AC3 | decodes | fixed long ago, no special handling needed |
| AV1 | decode and encode, NVIDIA GPU-accelerated | |

- **AAC is the real gotcha**: typical phone/camera footage plays silent and renders throw hundreds of
  `IO.Audio | ERROR | Failed to decode the audio samples` lines. The Media Pool still shows `Audio Codec: AAC`, which
  proves nothing about decoding. When the video codec is fine, an audio-only remux fixes it:
  `ffmpeg -i in.mp4 -c:v copy -c:a pcm_s16le out.mov`. For a whole folder (plus VFR and interlacing) use
  [pipelines/prepare-for-resolve/](pipelines/prepare-for-resolve/).
- **H.264/H.265 encoders only appear when `GPUDetect` found CUDA** ([previous section](#gpu-detection-x11-vs-wayland)).
  Without them, ProRes, DNxHD/DNxHR, CineForm, FFV1, JPEG 2000 and uncompressed remain.
- **Free edition, no NVIDIA GPU, or stuck on Wayland**: pre-transcode to DNxHR + PCM:

  ```bash
  ffmpeg -hwaccel cuda -i input.mp4 -c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p -c:a pcm_s16le output.mov
  ```

  A maintained batch tool for whole libraries:
  [ChrisTitusTech/resolve-linux `resolve_convert.sh`](https://github.com/ChrisTitusTech/resolve-linux) (five DNxHR
  tiers, mirrors the folder tree).
- **DNxHR HQ 4K is ~700 GB/hour** (~194 MB/s sustained write). A budget SATA SSD sustains far less once its cache is
  full; see [PERFORMANCE.md](PERFORMANCE.md) before a long DNxHR export.
- Blackmagic publishes no Linux-specific changelog. Across the 21.0.x and 21.1.x releases nothing changed the AAC or
  H.264/HEVC picture; re-check on new point releases.

## Known limitations on Linux

| Limitation | Mitigation |
|---|---|
| AAC unsupported, any edition | transcode audio to PCM before import ([Codecs](#codecs-on-linux)) |
| H.264/HEVC need Studio + NVIDIA, no software fallback | Studio with CUDA detected, or pre-transcode to DNxHR |
| Wayland: CUDA not detected → no NVENC codecs, render livelock | native X11 session + reboot ([GPU detection](#gpu-detection-x11-vs-wayland)) |
| `dvr.scriptapp('Resolve')` blocks forever while the GUI is in a playback loop | stop playback first; put a timeout on every connection |
| Imports and render output must be inside a registered **Media Storage** volume | register the directory first; outside it, `ImportMedia` returns `[]` silently and a render pops a blocking "Render Path Inaccessible" dialog |
| A queued render job keeps pointing at its directory; deleting that directory later breaks the queue with "Render Path Inaccessible" | delete only the output file, or `DeleteRenderJob` first |
| No scripting method adds an OFX/ResolveFX effect (Film Grain, Super Scale, Speed Warp, Noise Reduction) or a new node | GUI, or a pre-built `.drx` via `Graph.ApplyGradeFromDRX` |
| `CreateMagicMask()` does nothing without clicks on the subject | a person clicks in the Magic Mask palette once; `RegenerateMagicMask()` works afterwards |
| A failed or interrupted render does not recover cleanly: silent `None` returns, GUI dialogs block calls, `Quit()` hangs on its save dialog | treat an unexpected `None` as a blocked dialog; kill and relaunch; `ResolveClient.render()` wraps this |
| `-nogui` instances cannot use the render queue (`LoadRenderPreset` returns False, renders hang) | run the GUI inside a private `Xvfb` display |
| VRAM below the 16 GB AI-tool / 12 GB background-render minimums on 8 GB cards | hardware ceiling; stop other GPU workloads first |
| Single-seat Studio license: no second Resolve to view a Remote Monitor stream on another desktop | the free iOS/Android Remote Monitor app, or a desktop stream/VNC |
| NDI not built in | paid third-party plugin (Nobe Display) |
| Third-party OFX Linux support is vendor-specific | check per vendor ([Plugins](#plugins-on-linux)) |
| Login-gated download | manual, once per version |
| Only Rocky Linux 8.6 is supported | makeresolvedeb on Debian/Ubuntu |

## Scripting API and MCP

Enable it once in the GUI: **Preferences → General → External scripting using → Local**, then restart Resolve.
Environment for any script or server:

```bash
export RESOLVE_SCRIPT_API="/opt/resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/opt/resolve/libs/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
```

### Two MCP servers

| | Vendor `ResolveMCP` (`/opt/resolve/bin/ResolveMCP`, Studio 21.1+) | Community [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) |
|---|---|---|
| Tools | 14; `ResolveMCP --dump-tools` prints them | 37 compound tools, ~720 `action=` values; `--full` gives one tool per API method; an optional Node server edits `.drp/.drt/.drx` files offline |
| Model | send Python to `run_script` (sandboxed: no files, network, processes); `run_script_unsafe` has full system access | typed actions, safety layer (dry-run, risk ratings, typed errors), no caller-supplied code |
| Extras | API stubs and docs from the installed build (including the Fusion UI Manager API), `generate_lut` builds a `.cube` from a Python function | optional ML extras (whisper, librosa, open_clip, transformers, opencv, numpy) for transcription, beat detection, similarity |

Public coverage of 21.1 claimed "~88 tools" for the vendor server; the shipped binary has 14 and a different design.
Full catalogs: [MCP-CAPABILITIES.md](MCP-CAPABILITIES.md). The repo-root [`.mcp.json`](.mcp.json) registers both
servers (`resolve-native`, `resolve-community`) through
[`resolve_mcp_wrapper.sh`](pipelines/mcp-benchmark/resolve_mcp_wrapper.sh), which resolves `DISPLAY`/`XAUTHORITY`
per call because a static `env` block cannot.

**Speed** (Edit page, `MCP-Benchmark` fixture): a single lookup costs ~5.5 ms on the community server vs ~66 ms
through native `run_script` (a fresh sandbox per call). Renaming 8 clips costs 9 round trips on community (~101 ms) vs
1 `run_script` (~76 ms). Native wins once a task needs a handful of per-item calls. The GUI page is part of the
measurement: the same native rename takes ~73 ms on Edit/Color and ~166 ms on Deliver.

**Unified client.** [`resolve_client.py`](pipelines/mcp-benchmark/resolve_client.py) (`ResolveClient`) is the entry
point for scripted work: native first, community only as a recorded fallback (`client.fallbacks`), every method raises
`ResolveError` and returns parsed data, every call has a timeout, values are interpolated with `repr()`. It encodes the
import, still and render gotchas below as code, and `render()` gates the deliverable with an ffprobe check and a
loudness check. [`validate_client.py`](pipelines/mcp-benchmark/validate_client.py) is its live test (17 checks against
the disposable `MCP-Benchmark` project only, including negative checks that failures are loud). Details and every
harness: [pipelines/mcp-benchmark/](pipelines/mcp-benchmark/).

### Headless and virtual-display operation

For callers with nobody at the machine (an agent over SSH, an MCP client on another host):

- **`-nogui`** (`resolve_headless.py` from the community repo) boots in ~6-10 s and supports timeline edits,
  transforms and imports, but **not the render queue**.
- **GUI inside a private `Xvfb`** (`Xvfb :99 -screen 0 1920x1080x24 +extension GLX +render -noreset`, then
  `DISPLAY=:99 /opt/resolve/bin/resolve`, no `-nogui`) answers the API the same way **and renders**: a 64 s 1080p clip
  rendered to H.264 in ~4-5 s, through the API and through the community MCP. `resolve_xvfb_stdio.py` lets both servers
  share one such session (first in starts it, last out quits it, never touches a Resolve on another display).
- `run_script_unsafe` is a shell. Behind a restricted SSH key, put the vendor server behind
  [`mcp_filter.py`](pipelines/mcp-benchmark/mcp_filter.py), which hides it from `tools/list` and refuses calls to it.

Traps in this mode: a modal **"Your GPU memory is full"** inside the invisible display makes calls return `None`
(screenshot the display with `ffmpeg -f x11grab -i :99 -frames:v 1 shot.png`); Resolve renames its main thread to
"GUI Thread", so use `pgrep -f '^/opt/resolve/bin/resolve( |$)'`, not `pgrep -x resolve`; a Resolve left on a dead
display makes every new launch exit 0 (Qt single-instance lock in `/tmp/qtsingleapp-DaVinc-*`); status lines printed
to stdout break the MCP JSON-RPC channel (the wrappers send them to stderr).

### Scripting API gotchas

Found by driving real projects; each costs time because the failure is silent.

- **Fresh launch sits on the Project Manager**: `GetCurrentProject()` returns "Untitled Project", but
  `GetCurrentPage()` is `None` and Media Pool writes silently return `None`. `CreateProject`/`LoadProject` a named
  project first.
- **Native `run_script` reports a script exception as success**: `isError` stays false and the traceback arrives as
  `{"error": "Traceback…"}`. Check the payload, not the flag.
- **Community tools take `tool(action, params)`**: arguments must be nested in `params={...}`; top-level keys are
  dropped by schema validation and the action sees empty params (`name is required`, `missing track_type`). Keys are
  snake_case; `safe_import_media` takes `paths`.
- **Imports**: `MediaPool.ImportMedia()` can return `[]` for a valid file even inside Media Storage;
  `MediaStorage.AddItemListToMediaPool()` works. Re-importing a path already in the Media Pool returns nothing (not the
  existing item); use unique filenames. Imports land in the *current* Media Pool folder, not the root.
- **`Timeline.GrabStill()` returns before the still exists**: `ExportStills()` right after it exports stale content,
  no error. Wait ~0.5 s at 1080p (`resolve_client.STILL_SETTLE_S`); nothing in the API signals readiness.
- **Generator clips ("Solid Color") have no node graph**: `GetNodeGraph()` is `None`, so LUT/CDL calls cannot apply.
  Color tests need a real imported clip.
- **`Timeline.GetNodeGraph()` is the timeline-level grade**, separate from each clip's graph. Proving a toggle changed
  the image needs an exact per-pixel diff of stills: a low grain amount moves ~0.16 % of pixels by one luma level,
  invisible to a whole-frame average.
- **`GetToolsInNode()` lists tools on bypassed nodes too**, there is no `GetNodeEnabled()`, and it returns a generic
  name: AI Ultra NR and Temporal NR both read `"Noise Reduction"`. No OFX parameter read-back exists.
- **`Stabilize()` on an already-stabilized clip is a fast no-op**; `SmartReframe()` re-analyses every call.
- **`CreateSubtitlesFromAudio()` returns False on silent clips** (correct, not a no-op).
- **Loudness can be set** (`NormalizeAudioLevel`, target in LKFS) **but never read back**: measure the exported file
  ([`audio_loudness_check.py`](pipelines/mcp-benchmark/audio_loudness_check.py), ffmpeg `loudnorm`).
- **Renders run server-side**: a job keeps rendering after the calling script dies. Submit and exit, then poll.
- **Several new `dvr.scriptapp()` connections at once contend**; one script holding a connection while others connect
  made the others time out.
- **`fusionscript.so` calls hold the GIL** while blocked: a Python sampling thread in the same process gets no CPU
  time during a long call. Sample GPU/CPU from a subprocess (`nvidia-smi -lms`).
- **Blind GUI automation** (`xdotool`): screenshots of a window are in window coordinates, clicks in screen
  coordinates; add the window offset (`xdotool getwindowgeometry`). The render Location field is not persisted from
  project defaults. Prefer the API whenever a method exists.

## Measured costs

From [pipelines/effect-benchmark/](pipelines/effect-benchmark/) and real renders on the reference machine.

| Operation | Result |
|---|---|
| `Stabilize()`, 8030-frame compound clip, first pass | 28.6 s, GPU 47-54 %, ~105 W: genuinely GPU-bound |
| `SmartReframe()`, GPU capped at 100 W vs uncapped | 19.5 s vs 15.5 s (~20 % faster for ~2.4× the power) |
| `CreateMagicMask()` / voice isolation get/set | instant (needs clicks / metadata only, work deferred to playback/render) |
| 4-effect timeline (AI Ultra NR, Temporal NR, Film Grain, Analog Damage), 335 s, uncapped | H.265 Master 173 s, H.264 Master 179 s, ProRes 422 HQ 201 s; GPU at 100 % peak |
| same timeline, H.264 Master, 100 W cap | 272 s (uncapped is ~34 % faster) |
| 16172-frame 1080p24 timeline, H.265 NVIDIA, 100 W cap vs uncapped | 225.8 s vs 180.1 s (~25 % faster) |
| light cut-only render, capped vs uncapped | no measurable difference (run-to-run noise larger than the gap) |
| one 1080p clip, playback loop, no effects | GPU 5 %, ~1.9 GB VRAM, CPU ~1.5 cores, RSS ~4.6 GB |

Takeaways: a power cap costs real time on graded timelines and AI analysis, not on light cuts. In the 16172-frame
render, one section ran at ~15 fps while the rest ran at ~450 fps: portrait 25 fps sources being scaled and conformed
into a 1080p24 landscape timeline. That is CPU/scaling-bound and more GPU power does not fix it; pre-conform such
clips instead. [pipelines/resolve-power/](pipelines/resolve-power/) holds full power while Resolve runs.

### Bundled tools and benchmarks

No official full-pipeline Resolve benchmark exists for Linux (PugetBench is Windows/macOS only). What ships in
`/opt/resolve`:

| Tool | What |
|---|---|
| `bin/TestIO <path> <frameMB> <frames> <threads> <keepCpuBusy> <directIO>` | Blackmagic's disk benchmark, CLI. Prints only at the end of a full run: a `timeout` kill loses all output. Use `directIO=1` for real hardware numbers (cached runs measure page cache). |
| `BlackmagicRAWSpeedTest`, `BlackmagicRAWPlayer` | BRAW decode benchmark and viewer, GUI only |
| `bin/sqlite3`, `bin/ShowDpxHeader`, `bin/OFXLoader`, `bin/VstScanner` | SQLite CLI for Disk Database files, DPX header dump, OFX load check, VST scanner |
| `BMDPanelDaemon`, `DaVinci Control Panels Setup`, `Fairlight Studio Utility` | control-surface daemons and setup; a Speed Editor works with no extra setup |
| `bin/Blackmagic Remote Monitor` | started from Workspace → Remote Monitoring only; streams the viewer as H.264/H.265, needs an RTX GPU on Linux, has a local-IP mode without Blackmagic Cloud |

## Plugins on Linux

**Bundled**: `/opt/resolve/plugins` (~4.8 GB) is the Neural Engine feature bundle (Magic Mask, Speed Warp, Super
Scale, face/voice tools) as opaque `.bin` blobs, plus the CineForm encoder/decoder libraries. Nothing to install.

**AI Extras** (Extras Download Manager on first launch, ~24 GB): AI IntelliSearch (Better/Faster), AI Slate ID, AI
Speech Generator, AI Voice Training, Extended AI Transcription Language Support, TensorRT engines. The scripting calls
`AnalyzeForIntellisearch`, `AnalyzeForSlate` and `GenerateSpeech` need them and report Resolve's own reason when they
are missing. They are the workloads the 16 GB VRAM minimum is about; see [SCRIPTING-API-21.md](SCRIPTING-API-21.md)
for what runs on 8 GB.

**Third-party OFX**: "supports Resolve" and "supports Resolve on Linux" are often different claims.

| Vendor / product | Linux | Notes |
|---|---|---|
| Boris FX Sapphire | **yes** | RHEL 7-9 / Rocky listed |
| Boris FX Mocha Pro | **yes** | CentOS/RHEL/Ubuntu builds, Resolve listed as OFX host |
| Boris FX Continuum | unclear | Linux not broken out on the supported-hosts page |
| Red Giant / Maxon (Magic Bullet, Universe, Trapcode) | **no** | Windows/macOS only |
| Neat Video | partial | Linux builds exist but are license-gated (v4 Pro OFX / v5 Pro for Resolve) |
| FilmConvert, Digital Anarchy | unknown | Resolve builds exist, Linux unverified |
| Video Copilot | n/a | After Effects plugins, not OFX |

Boris FX is the one paid suite worth trying first. None of these has been installed here; this is a compatibility
survey.

## Pipelines

| Folder | What |
|---|---|
| [mcp-benchmark/](pipelines/mcp-benchmark/) | the automation layer: `ResolveClient`, its live test, native-vs-community benchmarks, the `-nogui`/Xvfb launchers, `mcp_filter.py`, `resolve_mcp_wrapper.sh` (what `.mcp.json` points at), loudness gate, pixel-diff grade check |
| [prepare-for-resolve/](pipelines/prepare-for-resolve/) | normalizes a folder of footage before import: AAC → PCM, VFR → nearest standard constant rate, interlacing, unsupported codecs; prefers NVENC and warns when it falls back to CPU |
| [resolve-update/](pipelines/resolve-update/) | updating Resolve with makeresolvedeb, with a rollback path: repacked `.deb`s of the working build (`dpkg-repack`) and a project/config snapshot |
| [resolve-backup/](pipelines/resolve-backup/) | on-demand snapshot of Resolve's Disk Database projects and user config (Fusion, Fairlight), timestamped, outside git |
| [resolve-power/](pipelines/resolve-power/) | holds CPU/GPU at full performance while Resolve runs and restores afterwards; can pause a named systemd power service meanwhile (`--pause-unit`); `--adaptive` goes full only while the timeline has heavy effects or a render is running |
| [effect-benchmark/](pipelines/effect-benchmark/) | per-effect and per-delivery-preset timing with GPU sampling from a subprocess, one JSON line per run |

Run the Python tools with the community MCP checkout's venv, or a venv built from
[pipelines/mcp-benchmark/requirements.txt](pipelines/mcp-benchmark/requirements.txt).

### Not covered yet

- **Multicam AI auto-edit** (speaker detection across synced angles): a guide for doing it in Resolve, and how much of
  it the scripting API exposes.
- **Pre-conforming mismatched sources** (resolution, aspect, frame rate) to a target timeline in `prepare-for-resolve`.
- **Separate benchmarks for AI Ultra NR vs Temporal NR**: neither has a scripting method, so each has to be applied by
  hand on its own test clip.

## License

MIT; see [LICENSE](LICENSE).
