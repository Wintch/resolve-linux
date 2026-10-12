# Resolve 21.x scripting API additions: what was actually tested

Resolve's changelog (`get_whats_new`, 21.0 through 21.0.4) lists new Scripting API
calls. This file records which of them were exercised live on this rig
(Resolve Studio 21.1.1, native MCP `run_script`), on 2026-10-10. The 21.1 and 21.1.1
additions (changelogs from the Blackmagic forum, pasted by the user) were checked against
the stubs and tested live on 2026-10-11: see "21.1 / 21.1.1 additions" below.

Test project: throwaway `zz_scripting_api_test` (not one of the real projects).
Fixtures, both in `~/Videos/mcp-bench-scratch/` (a registered Media Storage volume;
imports from outside one fail):

- `validate_client_testsrc_av.mov`: 6 s DNxHD 1280x720 + PCM, synthetic test pattern.
- `zz_real_dnxhr.mov`: 8 s real footage (a vertical shot of a room with murals and
  wooden structures, no people), transcoded with ffmpeg to DNxHR SQ 540x960 + PCM from a
  source in `~/Videos/resolve_validation_sources/`. Resolve on this rig cannot import the
  original H.264/AAC files without Studio's GPU decode path, hence the transcode. It is
  untracked local media, not committed.

Everything below ran with the `llm` docker container stopped, except where it says the
GPU was starved (see "Root cause of the import failures").

## Results

| API | Introduced | Result |
|---|---|---|
| `ProjectManager.GetProjectAttributesInCurrentFolder()` | 21.0.3 | Works. Dict of project name → `lastModifiedDate`, `creationDate`, `notes`, `liveCollaborationMode`. |
| `Resolve.GetLayoutPresetList()` | 21.0.3 | Works. Returned `[]` (no saved presets on this rig). |
| `Project.GetAnalyzeForSlateStatus()` | 21.0 | Works. `{"JobStatus": "Inactive"}` when idle. |
| `MediaPool.GetSelectedClips()` | not in the changelog | Works. Also returns a selected **timeline** (returned `["zz_tl"]`), not only clips. |
| `Timeline.GetSelectedClips()` | 21.0.4 | Works. `[]` with nothing selected. Not tested with a real selection. |
| `MediaPoolItem.PerformAudioClassification()` / `ClearAudioClassification()` | 21.0 | Work. `True`; classify took 0.4 s on a 6 s clip. |
| `Project.ResetIntellisearchAnalysis()` | 21.0 | Works. `True`. |
| `Project.GenerateSpeech({...})` | 21.0 | **Corrected 2026-10-11: does not produce audio.** It created `zz_speech_test-Female 1-G1-001.wav` in the media pool, but the file is 1080 bytes (a WAV header, no samples; `ffprobe` duration N/A, Resolve shows duration 00:00:00:00). Earlier "Works" only meant the call returned success and a clip appeared. Cause not found (Extra missing, VRAM, or model). Used `TextInput`, `VoiceModel: "Female 1"`, `Filename`. |
| `Resolve.DisableBackgroundTasksForCurrentResolveSession()` | 21.0 | Works. Returns `None`, no error. |
| `MediaPoolItem.AnalyzeForSlate(color)` / `Folder.AnalyzeForSlate(color)` | 21.0 | **Inconclusive.** No job starts (status stays `Inactive`). Returned `None` on the synthetic clip and `False` on a real clip (`"Blue"`, `"Red"`, `"Green"`, `"Yellow"`). The docs say it requires the *AI Slate ID* Extra; whether it is installed was not checked. |
| `MediaPoolItem.RemoveMotionBlur(opts)` / `Folder.RemoveMotionBlur()` | 21.0 | **Inconclusive.** Returns `None` immediately, no clip created, with `{}`, ProRes422 and H264 options, on both clips (also after freeing the GPU). |
| `MediaPoolItem.AnalyzeForIntellisearch(False, False)` | 21.0 | **Hangs Resolve.** Opens a modal "Analyze for IntelliSearch / Media Indexing - Initializing…" dialog that blocks the scripting thread. GPU stayed at 0 % for minutes; every later `run_script` timed out. `xdotool` (click on Cancel, Escape) could not close the dialog; the user dealt with it by hand and Resolve was found closed afterwards. **That run happened while the GPU was starved (see below) and before checking the Extras**, so the cause (VRAM, a missing *AI IntelliSearch* Extra, or the synthetic clip) is unknown, and the call has **not been retried**. |

## 21.1 / 21.1.1 additions (tested 2026-10-11)

Every API named in the 21.1 and 21.1.1 changelogs exists in the stubs (presets, multicam,
DCTL, fades, speed, blanking, normalize, transitions, clone, audio mapping, auto align,
async jobs, `ApplyGradeFromDRX(..., applyToAllLayers)`). Live results, project
`zz_scripting_api_test`, `llm` container stopped (VRAM 2.7 GB used of 8 GB, 24 GB RAM free):

| API | Result |
|---|---|
| `Resolve.ValidateDCTL(src)` | Works. Returns `None` for a valid file, `"DCTL Error: ..."` otherwise. The parser is picky: a one-line `transform` and a variable named `out` were rejected, a multi-line one with `float3 o` passed. |
| `Resolve.EncryptDCTL(path, {Name, OutputFolder, Expiry})` | Works. Wrote `zz_pass_enc.dctle` (1776 bytes, binary). Default output folder is the home directory. |
| `TimelineItem.GetFades()/SetFades()` | Works. Values in frames; read back `{FadeIn: 12.0, FadeOut: 6.0}`. |
| `TimelineItem.GetSpeed()/SetSpeed()` | Works. `{Percentage, PitchCorrection}`. |
| `Timeline/TimelineItem.Get/SetOutputBlanking()` | Works, but the values are **edge coordinates, not margins**: the timeline default reads `{Top:0, Bottom:1080, Left:0, Right:1920}`. A clip returns `{}` while it uses the timeline's blanking. |
| `Timeline.NormalizeAudioLevel(items, {normalizationMode})` / `GetNormalizeAudioModes()` | Works. 14 modes (EBU R128, ITU-R BS.1770-4, Netflix, YouTube, ...). |
| `TimelineItem.AddTransition({type, category, position, alignment, duration})` | Works. Returned the new item `Cross Dissolve`. |
| `Timeline.DetectSceneCuts()` + `Project.GetDetectSceneCutsStatus()` | Works, async: `Analyzing` (with `AnalysisSpeed`, `EstimatedTimeRemaining`), then `Complete`. |
| `Project.RenderWithQuickExport(preset, {TargetDir, CustomName})` + status | Works. Rendered a 5.3 s 1080p timeline in ~1 s (status `Render Complete`). Note: the output was `qe_test.mov` (H.264 + PCM in QuickTime), not an `.mp4`, even with the "H.264 Master" preset. |
| `Project.SaveAsNewRenderPreset / UpdateRenderPreset / DeleteRenderPreset`, `Resolve.ExportRenderPreset` | Work. Export wrote a **directory** named `zz_rp.xml`, not a file. |
| `Project.SetQuickExportEnabledForRenderPreset(name, True)` | Returns `True`, but `GetQuickExportRenderPresets()` did not list the new preset afterwards. Unclear. |
| `Project.SaveCurrentProjectSettingsAsNewPreset / ExportProjectSettingsPreset / DeleteProjectSettingsPreset` | Work. Exported file is a binary blob starting with `PresetType: ProjectSettingsPreset`. |
| `Resolve.GetKeyboardPresetList / ExportKeyboardPreset` | Work. 5 factory presets; export is 19 KB. |
| `Resolve.Save/Get/DeleteUserPreferencesPreset` | Work. |
| `MediaPoolItem.GetAudioMapping()` | Works. JSON string, e.g. `{"embedded_audio_channels":1, "track_mapping":{"1":{"channel_idx":[1],"mute":false,"type":"mono"}}}`. |
| `MediaStorage.GetCloneStatus()` / `StartCloneMedia()` | Status works (`Complete`, 100 %). `StartCloneMedia` on a missing dir returns `False`. A real clone was not run. |
| `MediaPoolItem.TranscribeAudio()`, `Timeline.CreateSubtitlesFromAudio()` + their `Get...Status()` | **Work** (see "Transcription and subtitles" below), but in the first Resolve instance of the day they returned `False` for ~20 minutes with no explanation. |
| Not run | `ApplyGradeFromDRX`, `SmartReframe`, `CreateMulticamClip`, `FlattenMulticam`, `PerformMulticamSmartSwitch`, `AutoAlignClips`, `SetAudioMapping`, `GetBurnInPresetList` (empty). |

### Transcription and subtitles (investigated 2026-10-11)

Fixture: `espeak-ng -v en-us -s 140 -w zz_espeak_speech.wav "Hello. This is a test of the
DaVinci Resolve transcription scripting interface. The quick brown fox jumps over the lazy
dog."` (9.7 s, mono 22 kHz), placed in `~/Videos/mcp-bench-scratch/` (a registered volume).

**Works, and it is accurate.** Both calls are async: they return `True` and the job is
followed through the status call.

- `clip.TranscribeAudio(True)` -> status `Initializing` -> `Complete` in a few seconds.
  `clip.GetTranscription()` returns `{"language": "en", "segments": [...]}`; each segment has
  `text`, `start` and `end` as timecode strings (`"00:00:00:02"`, `"00:00:09:07"`). It came
  back as one segment: ` Hello! This is a test of the DaVinci Resolve Transcription
  Scripting Interface. The quick brown fox jumps over the lazy dog.` (leading space, and
  `Transcription` capitalised by the model).
- `timeline.CreateSubtitlesFromAudio()` -> `Initializing` -> `Analyzing` (with
  `CompletionPercentage`, `AnalysisSpeed`, `EstimatedTimeRemaining`) -> `Complete`. Produced
  5 subtitle items: "Hello! This is a test of the DaVinci" / "Resolve Transcription" /
  "Scripting Interface." / "The quick brown fox" / "jumps over the lazy dog." A subtitle
  track did not have to exist beforehand (tested with 0 tracks; adding one first made no
  difference).
- The status dict has an `Error` field that carries the real reason on failure, e.g.
  `{"JobStatus": "Failed", "Error": "Auto Caption: There is no audio from frame 86400 to
  frame 86947 to analyze."}` (seen on `bench_tl_subs`). **Read it**: a `False` return alone says nothing.
- `TranscribeAudio` on a clip with no usable audio (the 1080-byte header-only WAV from
  `GenerateSpeech`, and the silent `zz_real_dnxhr.mov`) returns `True` and goes straight to
  `Complete` with nothing to show. `True` does not mean there is a transcript: check
  `GetTranscription()`.

**The `False` returns, unexplained.** In the first Resolve instance of the session (started
with `launch_resolve`), every `TranscribeAudio` (clip and folder, with and without speaker
detection) and every `CreateSubtitlesFromAudio` returned `False` with status `Inactive`, on
valid speech, in two different projects, and the original `bench_03_subtitles.py` (which
passed in September on 21.1) failed the same way. No line in `ResolveDebug.txt`; the log
only showed `BgTranscribeManager: Starting thread...`. Roughly 20 minutes in, the same calls
worked right after a script that called `Resolve.DisableBackgroundTasksForCurrentResolveSession()`
and reloaded the project. After quitting and relaunching Resolve they worked from the first
call, without that function, even after a quick export and after transcribing the empty
and silent clips first. So none of these is a confirmed cause: `DisableBackgroundTasks`,
quick export and prior failed calls were each ruled out or inconclusive, and the RAM
(32 GB now, 15 GB when it passed in September) and VRAM were not the factor either.
If it recurs, try: relaunch Resolve, and check the status `Error` text before anything else.
Other 21.0 AI calls that "did nothing" on 2026-10-10 (`AnalyzeForSlate`, `RemoveMotionBlur`)
were probably run in a similarly affected instance and are worth re-testing after a clean
relaunch.

Side finding: `~/.local/share/DaVinciResolve/logs/mcp.log` was 552 MB. It logs every
tool call in full, including large doc payloads.

## Things that differ from the stubs

- The stub types `markerColor` as "one of the `resolve.*` constants: `MARKER_BLUE`, …",
  but `dir(resolve)` has **no** `MARKER_*` attributes. This may be why
  `AnalyzeForSlate` does nothing; not confirmed.
- `MediaPool.GetSelectedClips()` / `Timeline.GetSelectedClips()` return Resolve objects;
  returning them directly from `run_script` makes the result unserializable
  (`'NoneType' object has no attribute '__name__'`). Map to `GetName()` first.
- `run_script` has a hard timeout of 60 s at most, and a blocking modal dialog
  (IntelliSearch) outlives it: the script times out but Resolve stays blocked.
- On this rig the Resolve window is only reachable with `DISPLAY=:0` plus the
  `XAUTHORITY` of Xwayland (`/run/user/1000/.mutter-Xwaylandauth.*`); without it
  `xwininfo` lists nothing. `import -window <id>` fails on unmapped windows.

## Root cause of the import failures: GPU memory (confirmed)

The GPU has 8 GB. The docker container `llm` (llama-server, ~5.1 GB) plus two `python3`
containers (~1.3 GB) and `sunshine` left Resolve ~0.8 GB. Symptoms: Resolve showed a
"GPU memory exhausted" popup, the debug log had `DVIP | ERROR | cudaMalloc failed:
Requested size 16.0 MiB` (also 8 and 140 MiB), and `MediaPool.ImportMedia` /
`MediaStorage.AddItemListToMediaPool` returned `None` for every file (H.264, ProRes, DNxHR),
even though `MediaStorage.GetFileList` listed them.

After `docker stop llm` (GPU use 7.7 GB -> 2.6 GB) the same `ImportMedia` call on the same
DNxHR file succeeded without relaunching Resolve. **A scripting call that returns `None`
without an exception can mean "out of VRAM"**; check `nvidia-smi` first.

The `llm` container has restart policy `unless-stopped`: it stays stopped until
`docker start llm`.

## What the API docs say about the AI calls

`get_scripting_docs` section "Studio and AI Scripting APIs": these calls return `False`
(or an error status) when the minimum system requirements are not met or the required
**Extras** are not downloaded. Required Extras:

- `AnalyzeForIntellisearch(identifyFaces, isBetterMode)`: *AI IntelliSearch - Faster*
  (`isBetterMode=False`) or *- Better* (`True`).
- `AnalyzeForSlate(markerColor)`: *AI Slate ID*.
- `GenerateSpeech(...)`: *AI Speech Generator* (worked here, so it is installed).

The Extras live in `/opt/resolve/Extras/<hash>/` (9 packages, 19 MB to 6.8 GB); their
manifests are binary and the directory names are hashes, so which package is which could
not be read from disk. Check in Resolve: Extras Download Manager.

## To finish

1. Check the Extras Download Manager for *AI Slate ID* and *AI IntelliSearch*; install
   what is missing, then re-run `AnalyzeForSlate` and `AnalyzeForIntellisearch`
   (the latter last, since it hung Resolve once; that run was under VRAM starvation).
2. Find out what `RemoveMotionBlur` needs (it returns `None` with no message).
3. Test `Timeline.GetSelectedClips()` with an actual selection.
