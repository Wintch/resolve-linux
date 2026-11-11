# Resolve 21.x scripting API additions: what was actually tested

Resolve's changelog (`get_whats_new`, 21.0 through 21.0.4) lists new Scripting API
calls. This file records which of them were exercised live, tested on Resolve Studio
21.1.1 through the native MCP `run_script` (Linux, RTX 3060 Ti 8 GB). The 21.1 and 21.1.1
additions (changelogs from the Blackmagic forum) were checked against the stubs and tested
live too: see "21.1 / 21.1.1 additions" below. The 21.2 additions (from the stub diff) are
listed in "21.2 additions", with the few that were tested live on Resolve Studio 21.2.

Test project: throwaway `zz_scripting_api_test` (not one of the real projects).
Fixtures, both inside a registered Media Storage volume (imports from outside one fail):

- `validate_client_testsrc_av.mov`: 6 s DNxHD 1280x720 + PCM, synthetic test pattern.
- `zz_real_dnxhr.mov`: 8 s of real footage (vertical, no people, no slate), transcoded
  with ffmpeg to DNxHR SQ 540x960 + PCM, because the original H.264/AAC cannot be imported
  without Studio's GPU decode path. Local media, not committed.

Everything below ran with the GPU free of other workloads, except where it says the GPU
was starved (see "Root cause of the import failures").

## Results

| API | Introduced | Result |
|---|---|---|
| `ProjectManager.GetProjectAttributesInCurrentFolder()` | 21.0.3 | Works. Dict of project name → `lastModifiedDate`, `creationDate`, `notes`, `liveCollaborationMode`. |
| `Resolve.GetLayoutPresetList()` | 21.0.3 | Works. Returned `[]` (no saved presets). |
| `Project.GetAnalyzeForSlateStatus()` | 21.0 | Works. `{"JobStatus": "Inactive"}` when idle. |
| `MediaPool.GetSelectedClips()` | not in the changelog | Works. Also returns a selected **timeline** (returned `["zz_tl"]`), not only clips. |
| `Timeline.GetSelectedClips()` | 21.0.4 | Works. `[]` with nothing selected. Not tested with a real selection. |
| `MediaPoolItem.PerformAudioClassification()` / `ClearAudioClassification()` | 21.0 | Work. `True`; classify took 0.4 s on a 6 s clip. |
| `Project.ResetIntellisearchAnalysis()` | 21.0 | Works. `True`. |
| `Project.GenerateSpeech({...})` | 21.0 | **Works** (after a clean Resolve relaunch): `{TextInput, VoiceModel: "Female 1", Filename}` produced a 2.8 s, 48 kHz mono WAV (538 KB) that `TranscribeAudio` read back as exactly the input text. The returned object is a MediaPoolItem, so do not put it in `result` (unserializable). **An earlier run in a long-lived session produced a 1080-byte header-only WAV** (no samples); cause unknown, same class of unexplained failure as the transcription `False` returns below. During a successful run the log showed transient `cudaMalloc failed` / TRT out-of-memory errors (8 GB GPU, 10 MiB free) and the audio was still generated. |
| `Resolve.DisableBackgroundTasksForCurrentResolveSession()` | 21.0 | Works. Returns `None`, no error. |
| `MediaPoolItem.AnalyzeForSlate(color)` / `Folder.AnalyzeForSlate(color)` | 21.0 | **Works, if you pass the constant, not a string.** `AnalyzeForSlate("Blue")` returns `False`; `AnalyzeForSlate(resolve.MARKER_BLUE)` (a float, `1.0`) returns `True`, status goes `Initializing` -> `Complete`. On the room footage (no slate) it finished with no markers and no metadata, which is the correct result. `dir(resolve)` does not list `MARKER_*`, but `getattr(resolve, "MARKER_BLUE")` resolves. A string argument is the usual reason it looks broken. A positive case (a real slate) was not tested. |
| `MediaPoolItem.RemoveMotionBlur(opts)` / `Folder.RemoveMotionBlur()` | 21.0 | **Still unresolved.** Returns `None` immediately and creates no clip, with `{}`, ProRes422/mov and H264/mp4 options (and the full `DeblurOptions` set), on both clips, also in a **freshly relaunched Resolve with the GPU free** (1.8 GB used). Nothing relevant in `ResolveDebug.txt` in that case. In a long session after speech generation, transcription and slate analysis, the same call coincided with `TRT ERROR ... OutOfMemory (Requested size was 311259140 bytes)` and a stack trace: the 8 GB GPU was full. So VRAM exhaustion is a second, separate way to fail; it is not the whole story. Which of the 9 Extras packages (if any) provides Motion Deblur could not be read from disk. |
| `MediaPoolItem.AnalyzeForIntellisearch(False, False)` | 21.0 | **Hangs Resolve.** Opens a modal "Analyze for IntelliSearch / Media Indexing - Initializing…" dialog that blocks the scripting thread. GPU stayed at 0 % for minutes; every later `run_script` timed out. `xdotool` (click on Cancel, Escape) could not close the dialog; it had to be dealt with by hand. **That run happened while the GPU was starved (see below) and before checking the Extras**, so the cause (VRAM, a missing *AI IntelliSearch* Extra, or the synthetic clip) is unknown, and the call has **not been retried**. |

## 21.1 / 21.1.1 additions

Every API named in the 21.1 and 21.1.1 changelogs exists in the stubs (presets, multicam,
DCTL, fades, speed, blanking, normalize, transitions, clone, audio mapping, auto align,
async jobs, `ApplyGradeFromDRX(..., applyToAllLayers)`). Live results, project
`zz_scripting_api_test`, GPU mostly free (VRAM 2.7 GB used of 8 GB, 24 GB RAM free):

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

### Transcription and subtitles

Fixture: `espeak-ng -v en-us -s 140 -w zz_espeak_speech.wav "Hello. This is a test of the
DaVinci Resolve transcription scripting interface. The quick brown fox jumps over the lazy
dog."` (9.7 s, mono 22 kHz), placed in a registered Media Storage volume.

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

**The `False` returns, unexplained.** In one Resolve instance (started with `launch_resolve`), every `TranscribeAudio` (clip and folder, with and without speaker
detection) and every `CreateSubtitlesFromAudio` returned `False` with status `Inactive`, on
valid speech, in two different projects, and `bench_03_subtitles.py` (which passes normally)
failed the same way. No line in `ResolveDebug.txt`; the log
only showed `BgTranscribeManager: Starting thread...`. Roughly 20 minutes in, the same calls
worked right after a script that called `Resolve.DisableBackgroundTasksForCurrentResolveSession()`
and reloaded the project. After quitting and relaunching Resolve they worked from the first
call, without that function, even after a quick export and after transcribing the empty
and silent clips first. So none of these is a confirmed cause: `DisableBackgroundTasks`,
quick export and prior failed calls were each ruled out or inconclusive, and RAM and VRAM
were not the factor either.
If it recurs, try: relaunch Resolve, and check the status `Error` text before anything else.
Re-tested after a clean relaunch: `GenerateSpeech` and `AnalyzeForSlate` turned out to work
(see their rows); `RemoveMotionBlur` still returns `None`.

Side finding: `~/.local/share/DaVinciResolve/logs/mcp.log` was 552 MB. It logs every
tool call in full, including large doc payloads.

## 21.2 additions

The stub `/opt/resolve/Developer/Scripting/DaVinciResolveScript.pyi` grows from 410
methods (21.1) to 530 (21.2): **+120, none removed**. Added, by class:

| Class | Added |
|---|---|
| `ColorGroup` (+1) | `GetPhotosInPhotoAlbum` |
| `Effect` (+6) | `GetFusionComp`, `GetIndex`, `GetInfo`, `GetIsEnabled`, `SetIndex`, `SetIsEnabled` |
| `Folder` (+1) | `GetParentFolder` |
| `Graph` (+4) | `GetNodeColor`, `GetNodeEnabled`, `SetNodeColor`, `SetNodeLabel` |
| `MediaPool` (+4) | `AddToPhotoAlbum`, `CreateEmptyPhotoAlbum`, `CreatePhotoAlbumFromPhotos`, `DeletePhotoAlbums` |
| `MediaPoolItem` (+5) | `ExportFrame`, `ExportThumbnails`, `GetParentFolder`, `GetPhotoAlbum`, `SetTranscription` |
| `PhotoAlbum` (+11) | `DeletePhotos`, `ExportThumbnails`, `GetCurrentItem`, `GetMediaPoolItem`, `GetName`, `GetNodeGraph`, `GetPhotoList`, `GetUniqueId`, `GrabAllStills`, `GrabStill`, `SetName` |
| `PhotoAlbumItem` (+48) | Grading, versions, flags, Fusion, CDL and AutoColor methods mirroring `TimelineItem` (`AddEffect`, `ApplyAutoColor`, `GetCDL`, `SetCDL`, `ExportFrame`, `ExportLUT`, `CopyGrades`, `GetNodeGraph`, ...) |
| `Project` (+13) | `GetAnalyzeForSlateStatus`, `GetCreateSubtitlesFromAudioStatus`, `GetCurrentPhotoAlbum`, `GetDetectSceneCutsStatus`, `GetPhotoAlbumByIndex`, `GetPhotoAlbumCount`, `GetPhotoQuickExportRenderPresets`, `GetRenderWithQuickExportStatus`, `GetSmartReframeStatus`, `GetTranscribeAudioStatus`, `RenderPhotosWithQuickExport`, `SetCurrentPhotoAlbum`, `SetPhotoRenderSettings` |
| `Resolve` (+4) | `GetEffectsList`, `GetExtrasPackages`, `InstallExtrasPackage`, `UninstallExtrasPackage` |
| `Timeline` (+6) | `DeleteGaps`, `ExportFrame`, `ExportThumbnails`, `GetIsEnabled`, `MoveItems`, `SetIsEnabled` |
| `TimelineItem` (+17) | `AddEffect`, `AnalyzeAudioLevels`, `ApplyAutoColor`, `ClearAutoColor`, `CopyAttributes`, `DeleteEffects`, `ExportFrame`, `ExportThumbnails`, `GetAutoColorEnabled`, `GetCDL`, `GetEffects`, `GetPosition`, `RippleDeleteSilence`, `SetAutoColorEnabled`, `SetLeftOffset`, `SetPosition`, `SetRightOffset` |

Signatures (from the stub) of the calls that were tested:

- `Resolve.GetEffectsList(mediaType)`: `mediaType` is `'video'`, `'photo'` or `'audio'`;
  returns a list of `{category, id, name}`.
- `TimelineItem.AddEffect(category, id)`: `category` is `'fusion'`, `'ofx'` or `'audio'`;
  returns an `Effect` or `None`.
- `TimelineItem.GetEffects(category)` -> `list[Effect]`;
  `TimelineItem.DeleteEffects(list[Effect])` -> `bool`.
- `Timeline.ExportFrame(frameOffset, ExportFrameOptions)`: `frameOffset` is 0-based from
  the timeline start; returns `{success, error, data, width, height}`, `data` being base64.
- `ExportFrameOptions` fields: `outputType` `'file'` | `'encodedString'` (default
  `encodedString`), `encodedImageFormat` `'jpg'` | `'png'` (default `jpg`), `filePath`,
  `maxDimension` (0 = no scaling), `quality` 1-100 (jpg), and `roiX` / `roiY` /
  `roiWidth` / `roiHeight` as fractions. Only `encodedImageFormat` `'png'` and `maxDimension` 0 were exercised;
  the `file` output, `quality` and the `roi*` fields are untested.

Live results on the reference machine (Resolve Studio 21.2.0.6, started with `-nogui`,
MCP-Benchmark fixture project):

| API | Result |
|---|---|
| `Resolve.GetEffectsList('video')` | Works. 130 video effects, categories `fusion` and `ofx`. |
| `TimelineItem.AddEffect` / `GetEffects` / `DeleteEffects` | Work. `AddEffect('ofx', 'Resolve FX/Resolve FX Film Emulation/Film Grain')` returned an `Effect`. The exported frame changes with Film Grain applied and is byte-identical to the original again after `DeleteEffects`. |
| `Timeline.ExportFrame(10, {encodedImageFormat: 'png'})` | Works without a GUI. Returned a 1920x1080 PNG as base64 (default `outputType` `encodedString`), byte-identical on repeat (deterministic). |
| Everything else in the table above | Not tested on 21.2. |

Several `Project` status getters in the 21.2 list (`GetAnalyzeForSlateStatus`, `GetDetectSceneCutsStatus`,
`GetRenderWithQuickExportStatus`, `GetTranscribeAudioStatus`, `GetCreateSubtitlesFromAudioStatus`) already worked at
runtime on 21.1.1 (see the tables above): 21.2 adds them to the stub, not to the API.

## Things that differ from the stubs

- The stub types `markerColor` as "one of the `resolve.*` constants: `MARKER_BLUE`, …" (a float).
  `dir(resolve)` has **no** `MARKER_*` attributes, but `getattr(resolve, "MARKER_BLUE")` works.
  Passing the colour name as a string makes `AnalyzeForSlate` return `False`.
- `MediaPool.GetSelectedClips()` / `Timeline.GetSelectedClips()` return Resolve objects;
  returning them directly from `run_script` makes the result unserializable
  (`'NoneType' object has no attribute '__name__'`). Map to `GetName()` first.
- `run_script` has a hard timeout of 60 s at most, and a blocking modal dialog
  (IntelliSearch) outlives it: the script times out but Resolve stays blocked.
- Under a GNOME Wayland session the Resolve window is only reachable with `DISPLAY=:0` plus the
  `XAUTHORITY` of Xwayland (`/run/user/1000/.mutter-Xwaylandauth.*`); without it
  `xwininfo` lists nothing. `import -window <id>` fails on unmapped windows.

## Root cause of the import failures: GPU memory (confirmed)

The GPU has 8 GB. Other GPU workloads on the same card (a llama-server container at
~5.1 GB, two more containers at ~1.3 GB and a desktop streaming server) left Resolve ~0.8 GB. Symptoms: Resolve showed a
"GPU memory exhausted" popup, the debug log had `DVIP | ERROR | cudaMalloc failed:
Requested size 16.0 MiB` (also 8 and 140 MiB), and `MediaPool.ImportMedia` /
`MediaStorage.AddItemListToMediaPool` returned `None` for every file (H.264, ProRes, DNxHR),
even though `MediaStorage.GetFileList` listed them.

After stopping the llama-server container (GPU use 7.7 GB -> 2.6 GB) the same `ImportMedia` call on the same
DNxHR file succeeded without relaunching Resolve. **A scripting call that returns `None`
without an exception can mean "out of VRAM"**; check `nvidia-smi` first.

## What the API docs say about the AI calls

`get_scripting_docs` section "Studio and AI Scripting APIs": these calls return `False`
(or an error status) when the minimum system requirements are not met or the required
**Extras** are not downloaded. Required Extras:

- `AnalyzeForIntellisearch(identifyFaces, isBetterMode)`: *AI IntelliSearch - Faster*
  (`isBetterMode=False`) or *- Better* (`True`).
- `AnalyzeForSlate(markerColor)`: *AI Slate ID*.
- `GenerateSpeech(...)`: *AI Speech Generator* (produced audio in the test, so it was installed).

The Extras live in `/opt/resolve/Extras/<hash>/` (9 packages, 19 MB to 6.8 GB); their
manifests are binary and the directory names are hashes, so which package is which could
not be read from disk. Check in Resolve: Extras Download Manager.

## To finish

1. `RemoveMotionBlur`: find out what it needs. Check the Extras Download Manager in the GUI for
   an AI Motion Deblur package, and try the same operation from the GUI to see its own error.
2. `AnalyzeForIntellisearch`: check the *AI IntelliSearch* Extra first, then run it last in a
   fresh Resolve with the GPU free (it hung Resolve once, under VRAM starvation); not retried.
3. `AnalyzeForSlate` with a real slate clip, to see markers and metadata populated.
4. Test `Timeline.GetSelectedClips()` with an actual selection.
5. Not run: `ApplyGradeFromDRX`, `SmartReframe`, multicam APIs, `AutoAlignClips`, `SetAudioMapping`.
