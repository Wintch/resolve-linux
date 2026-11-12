# mcp-benchmark

The Resolve automation layer of this repo: a launcher for both DaVinci Resolve MCP servers, a
unified client over them, the live tests that validate it, and the native-vs-community
benchmarks. Everything here runs against a real, connected Resolve Studio; the tool/action
catalog it was tested against is [../../MCP-CAPABILITIES.md](../../MCP-CAPABILITIES.md).

The two servers:

- **native**: `/opt/resolve/bin/ResolveMCP`, bundled with Resolve Studio 21.1+. 18 tools on 21.2
  (14 on 21.1.1), code-execution model: you send Python to `run_script` (sandboxed Python 3.14).
- **community**: [`samuelgursky/davinci-resolve-mcp`](https://github.com/samuelgursky/davinci-resolve-mcp),
  a separate checkout (default `~/resolve-install/davinci-resolve-mcp`, override with
  `RESOLVE_MCP_COMMUNITY_REPO`). 37 compound tools with typed `action=` values, no
  caller-supplied code.

## What's here

| File | What |
|---|---|
| `resolve_mcp_wrapper.sh` | What the repo-root `.mcp.json` points MCP clients at. Resolves the environment Resolve needs (`DISPLAY`, the X11 cookie, `RESOLVE_SCRIPT_API`/`LIB`, `PYTHONPATH`) and starts one backend (see [Backends](#backends)). |
| `resolve_xvfb_stdio.py` | Starts Resolve **with its GUI inside a private Xvfb** and runs an MCP server against it; shared and reference-counted between several servers. |
| `resolve_headless_stdio.py` | Shim around the community checkout's `scripts/resolve_headless.py run` that keeps its status lines off stdout (the MCP channel). |
| `mcp_filter.py` | Stdio filter that hides and refuses chosen tools (used to remove the vendor's `run_script_unsafe`). Fails closed: batches, non-JSON lines and malformed calls never reach the server. |
| `resolve_client.py` | `ResolveClient`: native-first, raising, timeout-guarded client over both servers (`import_media`, `grab_still`, `export_frame`, `frame_image`, `render()` with a loudness gate). |
| `validate_client.py` | Live end-to-end test of `resolve_client.py` (19 checks, asserts on returned values, ends with negative checks). Each check reports `OK`, `FAIL` or `SKIP` (cannot run in this setup, not a failure): `grab_still` is skipped under `-nogui` (no Color page; detected from the running Resolve's command line in `/proc`), `frame_image` and the `AddEffect` + `ExportFrame` round trip are skipped before 21.2. On 21.2 under `-nogui` all checks pass, 1 skipped. Under the `xvfb` backend it is not reliable: `grab_still` opens the Color page, which aborts Resolve there (see [Backends](#backends)), and every later call returns `None`. |
| `setup_bench_project.py` | Idempotent fixture: project `MCP-Benchmark`, timeline `bench_tl` with 8 "Solid Color" generator clips. |
| `bench_lib.py` | Shared stdio-client helpers for both servers, `resolve_env()`, the fixture guard, timing records. |
| `bench_01_simple.py`, `bench_02_batch_rename.py`, `bench_03_subtitles.py` | Native-vs-community timing benchmarks; bench_03 also exercises `CreateSubtitlesFromAudio`. Raw timings go to `*_results.jsonl` next to the script (local output). |
| `grain_timeline_toggle.py` | Toggles an effect node on a *timeline-level* node graph and proves it by pixel-diffing stills. |
| `audio_loudness_check.py` | Export-time loudness gate (~-14 LUFS, true peak ≤ -1 dBTP) with `ffmpeg loudnorm`; `--selftest` needs no Resolve. |
| `mcp_probe.py`, `mcp_render_flow.py`, `mcp_filter_test.py` | Small stdio probes: start + one call, a full render through the community server, the filter. Each takes the server command as arguments. |
| `patches/app_control-headless-restart.patch` | Local patch to the community server: `restart_app` relaunches with `-nogui` on Linux. |
| `requirements.txt` | For a standalone venv (`mcp`, `numpy`, `pillow`); the community checkout's venv already has them. |

## Running

Run the scripts with the community checkout's interpreter (`<community repo>/venv/bin/python
<script>`) or a venv built from `requirements.txt`. Non-Python needs: `ffmpeg`/`ffprobe`,
`espeak-ng` (bench_03), `Xvfb` (xvfb backend).

**Only against the fixture.** The benchmarks and `validate_client.py` rename clips, import
media and create timelines in *the current project*. `bench_lib.REQUIRE_FIXTURE_PROJECT`
makes every one of them refuse to run unless the current project is `MCP-Benchmark`; run
`setup_bench_project.py` once to create it. They also select `bench_tl`, check its 8 clips and
pin the GUI to the Edit page first (the page is part of the measurement: the same 8-clip
native rename takes ~73 ms on Edit/Color and ~166 ms on Deliver).

**Over SSH, export the environment first.** A non-interactive `ssh host 'cmd'` does not read
`~/.bashrc`, and the community `scripts/resolve_headless.py` silently falls back to **macOS**
paths when `RESOLVE_SCRIPT_API`/`RESOLVE_SCRIPT_LIB` are missing: every poll then fails to
import `DaVinciResolveScript` and `start` times out, whatever Resolve is doing. Take the
values from `bench_lib.resolve_env()`:

```bash
cd pipelines/mcp-benchmark && PY=<community repo>/venv/bin/python
eval "$($PY -c "
import shlex, bench_lib
e = bench_lib.resolve_env()
for k in ('DISPLAY','XAUTHORITY','RESOLVE_SCRIPT_API','RESOLVE_SCRIPT_LIB','PYTHONPATH'):
    if k in e: print(f'export {k}={shlex.quote(e[k])}')
")"
$PY <community repo>/scripts/resolve_headless.py guard   # refuses if a Resolve is already running
$PY <community repo>/scripts/resolve_headless.py start   # ~6-10 s until scriptable
$PY validate_client.py
$PY <community repo>/scripts/resolve_headless.py stop
pgrep -af '^/opt/resolve/bin/resolve( |$)'             # must print nothing
```

`resolve_env()` (and the wrapper, which uses the same lookup order) finds the X11 cookie: an
existing `XAUTHORITY` that points at a file wins; else the newest
`$XDG_RUNTIME_DIR/.mutter-Xwaylandauth.*` (Wayland session, new name every login); else the
newest `/tmp/xauth_*` (sddm/lightdm X11 session; never the greeter's `/run/sddm/xauth_*`);
else `~/.Xauthority`. `DISPLAY` is taken from the Xwayland process that owns that cookie
(rootless Xwayland picks `:0`, `:1`, ... per login), `:0` only as a last resort. Without a
cookie Resolve's Qt init aborts ("Authorization required, but no authorization protocol
specified"); with the right cookie on the wrong display it dies with "Invalid
MIT-MAGIC-COOKIE-1 key". Resolve "not scriptable" plus that line in `ResolveDebug.txt` is a
display/cookie mismatch, not the External scripting preference.

## Backends

`resolve_mcp_wrapper.sh <backend> [server args...]`:

| Backend | Server | Resolve | Use |
|---|---|---|---|
| `native` | vendor `ResolveMCP` | one already open | someone at the desktop |
| `community` | community `src/server.py` | one already open | someone at the desktop |
| `headless` | community, via `resolve_headless_stdio.py` | `-nogui` on the desktop's X server, started and stopped by the backend (`guard`/`start`/`run`/`stop`) | one unattended client; edits and renders; no pages, a raised dialog blocks the call; dies with the desktop session; not shared (a second client's exit stops it) |
| `xvfb` | community, via `resolve_xvfb_stdio.py` | full GUI in a private Xvfb | unattended caller that needs a GUI page other than Color (opening Color aborts Resolve here) |
| `native-xvfb` | vendor, through `mcp_filter.py --block run_script_unsafe` | the same Xvfb session as `xvfb` | unattended caller, vendor tools without the shell |
| `nogui` | community, via `resolve_xvfb_stdio.py` with `RESOLVE_XVFB_NOGUI=1` | `-nogui` bound to the private Xvfb, shared and counted like `xvfb` | **recommended for unattended callers**: edits and renders; no pages, so nothing can open the Color page; independent of any desktop session |
| `native-nogui` | vendor, through `mcp_filter.py --block run_script_unsafe` | the same `-nogui` session as `nogui` | the vendor tools on that session: `run_script` (`Timeline.ExportFrame`, `TimelineItem.AddEffect` on 21.2), `get_image`, `describe_grade`, ... |

The `xvfb*` and `nogui*` backends share one session per display: the first client in starts Xvfb
and Resolve in its mode (GUI or `-nogui`), later clients reuse it, the last one out quits both.
Verified on 21.2 with a community and a vendor client at once: the community one leaving does not
stop the session, `run_script_unsafe` stays unlisted and refused (17 tools visible), and nothing is
left after the last client. `mcp_render_flow.py` renders H.264 + PCM through `nogui`, and
`validate_client.py` passes inside it (grab_still skipped).

**Why Xvfb.** `-nogui` has no pages (`OpenPage("color")` leaves the page unchanged) and cannot show a dialog it raises: the call that triggered it never returns.
A missing render `TargetDir` is enough ("Render Path Inaccessible"), which is the likely cause of
render calls that hang under `-nogui`; with the directory in place, `load_preset`, `set_settings`,
`add_job` and `start` complete there too. Started with its normal GUI inside `Xvfb :99 -screen 0 1920x1080x24 +extension GLX
+render -noreset` (nothing appears on any desktop), Resolve answers the scripting API the same
way and the render queue works: a 64 s 1080p clip renders to H.264 in ~4 s through the API and
~5 s through the community MCP. Cold start to the first MCP answer: 8-10 s.

**But not the Color page.** Xvfb only has software GL, and Resolve's CUDA-OpenGL interop fails
on it: opening the Color page logs `Failed to register OpenGL object for CUDA interop:
cudaErrorUnknown` and `Packer OpenGL interop failed`, then Resolve aborts and every later API
call returns `None` (same on 21.1.1 and 21.2). On 21.2 the reason to want pages for frames is
gone: `Timeline.ExportFrame` and the vendor `get_image` return graded frames under `-nogui`.
Rendering through the community MCP (`mcp_render_flow.py`: create project, import, timeline,
`load_preset` "H.264 Master", `set_settings`, `add_job`, `start`) works on 21.2 with both
backends and writes H.264 + PCM; under `xvfb` its final `project_manager delete` of the current
project can fail (`DeleteProject` returns `False`) and leave the `zz_mcp_render_test` project
behind, under `headless` and `nogui` it succeeds. So on 21.2 use `nogui` / `native-nogui` for
unattended callers, and keep `xvfb` only for work that needs a visible GUI page other than Color.

**`resolve_xvfb_stdio.py`** lets a client keep both servers connected at once: every instance
registers in a clients directory under `/tmp`; the first one starts Xvfb (`RESOLVE_XVFB_DISPLAY`,
default `:99`; `RESOLVE_XVFB_SIZE`) and Resolve and waits until it is scriptable, later ones
reuse it, and the last one out Quits Resolve (forced after a grace time, since a GUI Quit can ask
to save) and stops Xvfb. A Resolve running on any other display (a person's session) is refused
and never touched. Clean-up survives SIGTERM/SIGHUP/SIGINT, EOF and a closed pipe. Status goes to
stderr: stdout is the JSON-RPC channel.

**`run_script_unsafe` is a shell.** Its own description: "full system access (filesystem,
network, subprocess)". When the servers are reached through an MCP-only SSH key (a forced
command, no shell), exposing it would hand out the machine, so `native-xvfb` puts
`mcp_filter.py` between the server and the client: the tool disappears from `tools/list` (17
left on 21.2) and a `tools/call` for it is answered with an error, never forwarded. The filter runs on
the server side of the key, so the client cannot turn it off. `mcp_filter_test.py <command>`
checks it. Override the blocked list with `RESOLVE_MCP_NATIVE_BLOCK`.

**`headless` and stdout.** Upstream `resolve_headless.py run` prints `starting:` / `ready in` /
`reusing ...` to stdout; strict clients (the Python `mcp` SDK) then fail with "Invalid JSON:
expected value at line 1 column 1". The shim moves Python's `sys.stdout` to stderr and leaves
fd 1 to the server.

**`restart_app`.** Upstream relaunches Resolve on Linux with no arguments, which silently
drops `-nogui`: the restarted instance is a GUI one and `resolve_headless.py status` reports
`headless: False`. `patches/app_control-headless-restart.patch` adds `-nogui`. Under the `xvfb`
backends that patched relaunch would come back as a `-nogui` instance instead of the GUI inside Xvfb: avoid
`restart_app` there (not tested).

## Liveness and recovery

- `resolve_headless.py status` can say `running: True, headless: True` while Resolve answers
  nothing. A `-nogui` instance still binds to the X server it was launched against: when that
  graphical session ends (log out, switch to another session type), the process lives on and
  every `scriptapp("Resolve")` hangs. Check liveness with a real round trip under a timeout
  (`scriptapp` + `GetCurrentPage()` under `timeout 20`), and restart Resolve after a session
  switch.
- `resolve_headless.py stop --force` can report success and leave the process alive. Verify
  with `pgrep -f '^/opt/resolve/bin/resolve( |$)'`; once Qt is up the main thread is renamed
  "GUI Thread", so `pgrep -x resolve` stops matching.
- A Resolve left on a display that no longer exists makes every new launch exit with code 0
  (Qt single instance). Kill it and remove the stale `/tmp/qtsingleapp-DaVinc-*` socket and lock.
- Never `pkill -f '<pattern>'` from an `ssh host '...'` one-liner when the pattern is also in
  that command line: it kills its own shell.
- A modal dialog in the (virtual) display blocks the API: calls return `None` or
  `success: false` with no other signal. Seen with "Your GPU memory is full" when other
  processes held GPU memory, and with render-path and save-on-quit prompts. Look at the
  display (`ffmpeg -f x11grab -video_size 1920x1080 -i :99 -frames:v 1 shot.png`) whenever a
  call returns `None`; `xdotool` can click it away.
- **Interrupted renders.** Once a render is stopped or stuck, later calls can return `None`
  (blocked dialog) or the next render can stall at 0 % with a climbing estimate and write a
  corrupt file. Killing Resolve *while a render is running* triggered its crash handler;
  cancelling the job first and then quitting closed cleanly. The only reliable recovery for a
  wedged render is a kill and relaunch; `ResolveClient.render()` polls with a hard deadline and
  stops its own job instead of waiting forever.
- **AAC** sources do not decode on Linux: the render log fills with "Failed to decode the audio
  samples" retries and the job estimate climbs to hours. Transcode audio to PCM first
  (`ffmpeg -c:v copy -c:a pcm_s16le`, or `../prepare-for-resolve/`).
- First render calls right after `open_page` can fail once; a retry worked.

## Native vs community

On an RTX 3060 Ti 8 GB / 32 GB RAM / Debian 13 box, Resolve Studio 21.1.x, Edit page:

| Benchmark | community (compound) | native (`run_script`) |
|---|---|---|
| bench_01: 4 read-only asks (version, project, timeline count, current timeline), median per call | ~5.5-6 ms | ~66 ms |
| bench_02: rename the 8 clips of `bench_tl`, median wall time | ~100-160 ms, 9 round trips | ~76 ms, 1 round trip |

Native pays a flat ~60 ms per `run_script` (a fresh sandboxed interpreter per call); once a task
needs a handful of per-item round trips on community, native wins. Community's median and mean
differ on writes because its safety layer archives the timeline the first time a session
mutates it (`bench_tl_archived_vNN` copies accumulate in the fixture).

The two designs are different trade-offs, not a bigger and a smaller version of the same thing:

| | native | community |
|---|---|---|
| Model | `run_script` (sandbox: no `os`, files, network, processes; `resolve` and `project` pre-injected) and `run_script_unsafe` (same, full system access) | typed actions, `safe_*`/dry-run/risk layer, typed error envelopes with `remediation` |
| Extras | live `.pyi` stubs and docs (incl. Fusion UI Manager), `get_whats_new`, `generate_lut` (evaluates a Python transform per lattice point); on 21.2 also `describe_grade`, `get_image`, `get_thumbnails`, `list_extras_packages`/`install_extras_package` | transcription, visual similarity, beat detection (optional AI extras), offline `.drp`/`.drt`/`.drx` work through its Node server |
| Needs | Resolve running | Resolve running (except the offline Node server) |

`ResolveClient` routes **native first**: lookups come from one native round trip
(`get_context()`) and the community server is only started lazily when native cannot serve a
call. Every such fallback is recorded in `client.fallbacks` (capability + why);
`validate_client.py` asserts it stays empty.

## ResolveClient

- Every method raises `ResolveError` and returns parsed data. **Native `run_script` reports a
  script exception as a successful MCP result**: `isError` stays false and the traceback arrives
  as `{"error": "Traceback…"}` (a normal run is `{"output": …, "result": …}`).
  `bench_lib.timed_call` treats that envelope as an error.
- Every call carries a timeout (native's server-side one, max 60 s, plus a client read timeout
  above it), so the "scripting call never returns while the GUI is mid-playback" hang surfaces
  as an error and the session stays usable.
- Values go into scripts through `repr()`. Resolve refuses `"` in a project name
  (`CreateProject` returns `None`) and accepts an apostrophe.
- `import_media()`: registered-volume check, `AddItemListToMediaPool` instead of `ImportMedia`,
  path lookup instead of trusting the return value (see below).
- `grab_still()` is native-only (`Timeline.GrabStill` + `GalleryStillAlbum.ExportStills`, the PNG
  read back and the still deleted) and waits `STILL_SETTLE_S` (0.5 s held at 1080p; nothing in
  the API signals readiness, a heavier grade or 4K may need more) between grab and export.
- `export_frame(frame_offset, image_format='png', max_dimension=0)` (21.2+): bytes of the current
  timeline's graded frame (offset 0-based from the timeline start) through `Timeline.ExportFrame`
  in `run_script`. Full-resolution PNG by default: lossless and byte-identical for the same frame
  and grade, so it proves a grade or effect change like `grab_still`, but needs no GUI (works
  under `-nogui`) and leaves the playhead alone.
- `frame_image(timecode, source='timeline', max_dimension=None)` (21.2+): `(mime, bytes)` from the
  vendor `get_image` tool. A JPEG downscaled to 1024 px unless `max_dimension` is raised: good for
  looking at a frame or telling frames apart, too lossy for small grade diffs.
- `delete_lut()` calls the vendor `delete_lut_file` when the server lists it (21.2) and
  `delete_lut` otherwise (21.1).
- `render()`: refuses a target outside a Media Storage volume before queueing, raises on a
  silent `AddRenderJob` `None`, polls with a deadline (then `StopRendering`), only touches its
  own job, accepts only files written by this render, verifies with `ffprobe` and runs
  `audio_loudness_check` on the deliverable.
- `bench_lib._session` unwraps the MCP SDK's nested `ExceptionGroup`s so one failure is one
  line. Server stderr is dropped unless `RESOLVE_MCP_DEBUG=1`.
- `validate_client.py` guards its own mutations: the NEG rename-count check refuses to run unless
  `bench_tl` is the current timeline (on a one-clip timeline the rename would succeed and mutate
  it), and the `AddEffect` + `ExportFrame` round trip refuses if the fixture clip already has OFX
  effects and removes only the effect it added.

## Behaviors worth knowing

**Community server**

- Every tool is `tool(action="<name>", params={...})`. **All arguments go inside `params`**:
  top-level arguments are dropped by schema validation and the action answers `name is
  required`, `'track_type' is required` or `Provide clip_ids ...`. Keys are snake_case
  (`clip_ids`, `clip_infos`, `track_type`); camelCase is not read. A JSON string as `params` is
  accepted. Errors with a `remediation` field name the fix; `resolve_control describe_api`
  shows an action's signature.
- `safe_import_media` takes `paths`. `append_to_timeline` takes `clip_ids` (simple form) or
  positioned `clip_infos` (see below). `item_index` is 0-based, track indices are 1-based.
- `create_timeline` with an existing name returns a versioned name (`edit v02`): read `name`
  from the result. Mutating calls auto-archive the timeline (`archived_version` in the result).
  `safe_project_create`/`safe_project_delete` refuse names not starting with `_mcp_` unless
  `allow_non_mcp_name: true`; `delete_clips` needs the two-step `confirm_token`.
- Since v4.16: `timeline_id`/`timeline_name` *pin* timeline-scoped calls (switch, act, restore
  the caller's current timeline; verified on 21.1.1: pinned reads and writes land only on the
  pinned timeline, a pinned `duplicate` reports the restored timeline as current);
  `TIMELINE_NOT_FOUND`, `TIMELINE_SELECTOR_MISMATCH`, `ITEM_NOT_FOUND` errors carry
  `isError: true`; unknown keys come back in `ignored_params`; `timeline_item_id`
  (`GetUniqueId()`) is the stable item selector, because on 21.1 `item_index` counts
  transitions (an unknown id returns `ITEM_NOT_FOUND`; lookup verified on 21.2 too); `duplicate` keeps the original current
  unless `make_current: true`.
- FCPXML export writes a bundle directory (`<name>.fcpxml/Info.fcpxml`);
  `timeline probe_interchange_roundtrip` (format `fcpxml`) exports it, re-imports it and compares:
  structure matches, generator clips come back offline (on 21.2: match, 0 differences).
- `graph apply_grade_from_drx` with `source: "timeline"` refuses a `.drx` without a timeline grade
  (`<pTrackVer>`) with `DRX_NO_TIMELINE_GRADE` instead of emptying the timeline graph (verified on 21.2).
- Since v5.0 the server refuses caller-chosen commands and binaries: `apply_spec` no longer runs
  spec hooks (`SPEC_HOOKS_NOT_RUN`), the advanced server rejects binary overrides
  (`BINARY_OVERRIDE_REFUSED`), and media paths starting with `-` are refused
  (`OPTION_LIKE_PATH_REFUSED`; pass an absolute path).
- `set_property` answers `success: true` for `Pan`; color controls (Lift/Gamma/Gain) are not
  `set_property` keys.
- `timeline_frame capture` defaults to `quality="frame"` (`ExportCurrentFrameAsStill`: correct
  frames of real footage). **Avoid `quality="render"`**: it went through the render queue and
  hung Resolve after a "GPU failed to perform image processing ... Error Code: 999" while other
  processes shared the GPU; only `kill -9` of caller and Resolve recovered.
- `set_subtitle_preset` builds a new timeline revision and keeps the original. The installed
  presets (`list_title_presets`: 134, of which 23 subtitle) can ask for fonts Linux does not
  have: "Word Highlight" wants **Segoe UI Black**, and without it the Fusion comp renders no text
  (`Could not find font: Segoe UI: Black` in `ResolveDebug.txt`; silent on the still route, a
  failed job on the render route). Install a compatible font or remap the preset.
- `import_bounded_media` (`start_frame`/`end_frame`): the end frame is inclusive; the
  destination folder must exist.
- Upstream notes, not reproduced here: `Project.StartRendering` can return `False` and leave the
  job Ready (check job status); project-settings presets written before a project's first
  `SaveProject` can destroy the project (save once first); `set_high_priority` takes `high` and
  is reversible on 21.1+.

**Scripting API (both servers)**

- Right after `launch_resolve`, Resolve sits on the Project Manager: `GetCurrentProject()`
  returns a usable "Untitled Project" but `GetCurrentPage()` is `None` and Media Pool writes
  silently return `None`. Create or load a *named* project first.
- Source media and render targets must be inside a registered **Media Storage** volume
  (`MediaStorage.GetMountedVolumeList()`); anything else fails silently on import and with a
  blocking dialog on render.
- `MediaPool.ImportMedia()` can return an empty list for a valid file that
  `MediaStorage.AddItemListToMediaPool()` imports fine. Both dedup by path: re-importing a path
  already in the pool returns nothing (not the existing item), even if the file changed; use a
  unique name per run. Imports land in the *current* Media Pool folder.
- `AppendToTimeline` without `clip_infos` appends at the end of the **whole timeline**, whatever
  the track. To place a clip at a frame use `clip_infos` with `record_frame`,
  `record_frame_mode="absolute"` and the source `start_frame`/`end_frame`.
- `AddRenderJob` returns `''` on a timeline with no video item.
- A generator clip ("Solid Color") has no node graph (`GetNodeGraph()` is `None`), so LUT/CDL
  tests need a real imported clip. `validate_client.py` generates a DNxHR/PCM test clip under
  `~/Videos/mcp-bench-scratch`.
- `Timeline.GetNodeGraph()` is a timeline-level graph, distinct from each clip's. `Graph` has no
  `AddNode`/`AddTool`: a new OFX node in the color graph only arrives through `ApplyGradeFromDRX` with a
  pre-authored `.drx` (21.2's `TimelineItem.AddEffect`, below, adds an OFX effect to the clip instead). `GetToolsInNode()` keeps listing a disabled node's tool and before 21.2
  there is no `GetNodeEnabled()`; prove a grade change with a pixel diff of stills (or, on 21.2,
  of `Timeline.ExportFrame` PNGs).
- 21.2 adds `TimelineItem.AddEffect(category, id)` (`'fusion'|'ofx'|'audio'`, ids from
  `Resolve.GetEffectsList()`), `GetEffects(category)` and `DeleteEffects(list)`. Verified on the
  fixture under `-nogui`: adding OFX Film Grain changes the `ExportFrame` PNG, and after
  `DeleteEffects` it is byte-identical to the original again.
- `GrabStill()` returns before the still's image exists; an immediate `ExportStills` writes
  stale content with no error.
- `CreateSubtitlesFromAudio()` returns `False` on silent clips (correct); on synthesized speech
  it transcribes in well under a second for a short clip.
- The API can *set* a loudness target (`NormalizeAudioLevel`) but cannot read back measured
  loudness or true peak, hence `audio_loudness_check.py` on the exported file.
- The scripting connection blocks while the GUI is mid-playback; stop playback first.

## Upgrading the community server

The checkout carries local commits on top of an upstream tag: the `-nogui` restart patch and,
if npm needs it, an `allowScripts` approval for `better-sqlite3`.

```bash
cd <community repo>
git fetch --tags
git branch backup-<current>-local                      # keep the working state
git checkout -b <new>-local <new tag>
git cherry-pick <the local commits>                    # or: git apply <this repo>/pipelines/mcp-benchmark/patches/*.patch
venv/bin/pip install -r requirements.txt --upgrade
npm ci                                                 # npm 11 asks to re-approve install scripts (better-sqlite3)
npm run smoke                                          # prints the version
venv/bin/python scripts/doctor.py                      # core checks [OK]; the scripting [WARN] is expected with Resolve stopped
```

Then run the regression suite ([../regression/](../regression/), `./run.sh`): it compares the tool and
action lists with the recorded baselines (37 tools at v5.2.2), runs `validate_client.py` and the live
checks of both servers in a fresh `-nogui` session. Review any baseline FAIL, accept it with
`./run.sh --offline --update-baselines`, and update [MCP-CAPABILITIES.md](../../MCP-CAPABILITIES.md).
If a patch stops applying, re-apply it by hand and regenerate it with `git format-patch -1`.

Notes:
- The vendor's offline suite runs with `venv/bin/python -m unittest discover -s tests -t .`
  (the venv has no pytest). Some tests depend on the environment (platform paths, a `HOME`
  under `/tmp` counted as scratch space); compare against the previous tag before calling a
  failure a regression.
- A fresh venv also needs `numpy requests` beyond `requirements.txt`, and the optional AI extras
  are the packages `doctor.py` names (`librosa opencv-python transformers open_clip_torch
  openai-whisper`); pip picks CUDA-enabled torch on its own. A re-clone loses them silently.
- Upstream releases often; read the CHANGELOG for Linux-relevant changes and new actions.

## Other Resolve MCP servers

| Server | Status |
|---|---|
| `wassermanproductions/unofficial-davinci-mcp` | Code reviewed (no code execution, no telemetry, list-form subprocesses, dry-run then confirm on every live mutation, offline engines for loudness/beat grid/LUTs/captions; no MCP `annotations`), not adopted. |
| `lordhoell/davinci-resolve-mcp` | Declined: a single push, inactive; its "440+ tools" claim unverified. |
| `Tooflex/davinci-resolve-mcp` | Declined: small (32 tools), stale, nothing the community server lacks. |
| `DigitalWorkflowCompany/resolve-mcp`, `hoyt-harness/davinci-mcp-professional`, `apvlv/davinci-resolve-mcp`, `Iamkewl/Davinci-MCP` | Not evaluated. |

See the main [README.md](../../README.md#scripting-api-and-mcp) for how this fits the rest of
the repo, and [Known limitations on Linux](../../README.md#known-limitations-on-linux).
