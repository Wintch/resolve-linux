# davinci-resolve-mcp capability map (live, 2026-08-24)

Not the vendor's README repeated — this is extracted directly from the running
code (`src/server.py`, MCP SDK v2.103.1) on this rig, then a sample of it was
actually called against a connected DaVinci Resolve Studio 21.0.4.5, to know
what's real vs. documented-but-untested. See `../resolve-linux/README.md` for
the install/validation context this sits inside.

## How this was produced

1. **Tool + action extraction**: connected to the already-running compound
   server (`src/server.py`) in-process, called `mcp.list_tools()`, and
   regex-parsed each tool's docstring for its `name(args) -> result` / `name(args) — description`
   action lines. 36 tools, **667 actions** total — the true current count, not
   a doc snapshot (differs slightly from the vendor README's advertised
   "36 compound / 353 granular" tool-level number, since granular mode exposes
   roughly one MCP tool per API method whereas this counts the compound
   server's internal `action=` values, a different unit).
2. **Live probe**: a 41-action, curated **read-only** sample (one representative
   safe action per tool — `get_*`, `list`, `capabilities`, `probe_*`, `*_report`,
   never `set_*`/`create`/`delete`/`execute_*`/`safe_*` mutating actions) run
   against the connected Studio instance, checkpointed to disk every 10 calls
   so a crash mid-run wouldn't lose progress. **41/41 returned without
   exceptions.**
3. Test project at probe time: a fresh, empty `Untitled Project` (no timeline,
   no clips) — so most calls correctly reported `"No current timeline"` /
   empty lists rather than exercising real data. That's the right behavior to
   see, not a failure: every error came back as **structured, typed JSON**
   (`{code, category, retryable, remediation}`), not a raw exception or a
   silent wrong answer — e.g. `graph.get_num_nodes` with no timeline open:
   ```json
   {"error": {"message": "No current timeline", "code": "NO_CURRENT_TIMELINE",
     "category": "precondition", "retryable": false,
     "remediation": "Open a timeline via timeline(action='set_current', params={'index': N}) or in the Resolve UI."}}
   ```
   That's a genuinely useful error shape for an agent to act on — worth noting
   as a real quality signal, not just "it didn't crash."

## Full tool → action catalog

| Tool | Actions | Names |
|---|---|---|
| `setup` | 4 | `schema`, `get_defaults`, `set_defaults`, `clear_defaults` |
| `resolve_control` | 32 | `launch`, `runtime_mode`, `get_version`, `mcp_update_status`, `set_mcp_update_policy`, `ignore_mcp_update`, `snooze_mcp_update`, `clear_mcp_update_preferences`, `api_truth`, `check_version_support`, `verification_stats`, `job_status`, `list_jobs`, `get_page`, `open_page`, `get_keyframe_mode`, `set_keyframe_mode`, `quit`, `get_fairlight_presets`, `set_high_priority`, `disable_background_tasks_for_current_session`, `list_user_preferences_presets`, `save_user_preferences_preset`, `load_user_preferences_preset`, `delete_user_preferences_preset`, `import_user_preferences_preset`, `export_user_preferences_preset`, `open_control_panel`, `control_panel_status`, `close_control_panel`, `save_state`, `restore_state` |
| `layout_presets` | 7 | `list`, `save`, `load`, `update`, `export`, `import_preset`, `delete` |
| `render_presets` | 6 | `import_render`, `export_render`, `import_burnin`, `export_burnin`, `list_burnin`, `delete_burnin` |
| `project_manager` | 31 | `list`, `list_attributes`, `get_current`, `create`, `load`, `save`, `close`, `delete`, `import_project`, `export_project`, `archive`, `restore`, `project_capabilities`, `probe_project_lifecycle`, `probe_project_settings`, `safe_project_create`, `safe_project_export`, `safe_project_import`, `safe_project_archive`, `safe_project_restore`, `safe_project_delete`, `safe_set_project_settings`, `project_settings_snapshot`, `database_capabilities`, `safe_set_current_database`, `preset_lifecycle_probe`, `project_boundary_report`, `lint`, `diff_to_spec`, `plan_spec`, `apply_spec` |
| `project_manager_folders` | 7 | `list`, `get_current`, `create`, `delete`, `open`, `goto_root`, `goto_parent` |
| `project_manager_cloud` | 4 | `create`, `load`, `import_project`, `restore` |
| `project_manager_database` | 3 | `get_current`, `list`, `set_current` |
| `project_settings` | 19 | `get_name`, `set_name`, `get_setting`, `set_setting`, `get_unique_id`, `get_presets`, `set_preset`, `refresh_luts`, `get_gallery`, `export_frame_as_still`, `project_summary`, `load_burnin_preset`, `insert_audio`, `get_color_groups`, `add_color_group`, `delete_color_group`, `apply_fairlight_preset`, `generate_speech`, `reset_intellisearch_analysis` |
| `render` | 36 | `add_job`, `delete_job`, `delete_all_jobs`, `list_jobs`, `get_job_status`, `start`, `stop`, `is_rendering`, `get_formats`, `get_codecs`, `get_format_and_codec`, `set_format_and_codec`, `get_mode`, `set_mode`, `get_resolutions`, `get_settings`, `set_settings`, `list_presets`, `load_preset`, `save_preset`, `delete_preset`, `quick_export_presets`, `quick_export`, `render_capabilities`, `probe_render_matrix`, `probe_render_settings`, `validate_render_settings`, `safe_set_render_settings`, `prepare_render_job`, `render_job_lifecycle_probe`, `quick_export_capabilities`, `safe_quick_export`, `export_render_boundary_report`, `list_delivery_targets`, `resolve_delivery_target`, `prepare_delivery_job` |
| `media_storage` | 7 | `get_volumes`, `get_subfolders`, `get_files`, `reveal`, `import_to_pool`, `add_clip_mattes`, `add_timeline_mattes` |
| `media_pool` | 48 | `get_root_folder`, `get_current_folder`, `set_current_folder`, `add_subfolder`, `delete_folders`, `move_folders`, `refresh`, `create_timeline`, `create_timeline_from_clips`, `setup_multicam_timeline`, `import_timeline`, `delete_timelines`, `append_to_timeline`, `import_media`, `delete_clips`, `move_clips`, `relink`, `unlink`, `export_metadata`, `get_unique_id`, `create_stereo_clip`, `auto_sync_audio`, `get_selected`, `set_selected`, `get_clip_mattes`, `get_timeline_mattes`, `delete_clip_mattes`, `import_folder`, `ingest_capabilities`, `probe_media_pool`, `probe_ingest_item`, `safe_import_media`, `safe_import_sequence`, `safe_import_folder`, `organize_clips`, `copy_metadata`, `normalize_metadata`, `probe_clip_properties`, `metadata_field_inventory`, `safe_relink`, `safe_unlink`, `check_proxy_media_compatibility`, `link_proxy_checked`, `link_full_resolution_checked`, `set_clip_marks`, `clear_clip_marks`, `copy_clip_annotations`, `media_pool_boundary_report` |
| `folder` | 13 | `get_clips`, `get_name`, `get_subfolders`, `is_stale`, `get_unique_id`, `export`, `transcribe_audio`, `clear_transcription`, `perform_audio_classification`, `clear_audio_classification`, `analyze_for_intellisearch`, `analyze_for_slate`, `remove_motion_blur` |
| `media_pool_item` | 33 | `get_name`, `get_metadata`, `get_third_party_metadata`, `set_third_party_metadata`, `get_media_id`, `get_clip_property`, `set_clip_property`, `get_clip_color`, `set_clip_color`, `clear_clip_color`, `link_proxy`, `unlink_proxy`, `replace_clip`, `set_name`, `link_full_resolution_media`, `monitor_growing_file`, `replace_clip_preserve_sub_clip`, `get_unique_id`, `transcribe_audio`, `clear_transcription`, `get_transcription`, `extract_frames`, `perform_audio_classification`, `clear_audio_classification`, `analyze_for_intellisearch`, `analyze_for_slate`, `remove_motion_blur`, `get_audio_mapping`, `get_mark_in_out`, `set_mark_in_out`, `clear_mark_in_out`, `get_timeline`, `open_in_viewer` |
| `media_pool_item_markers` | 15 | `add`, `get_all`, `get_by_custom_data`, `update_custom_data`, `get_custom_data`, `delete_by_color`, `delete_at_frame`, `delete_by_custom_data`, `add_flag`, `get_flags`, `clear_flags`, `set_name`, `link_full_resolution_media`, `monitor_growing_file`, `replace_clip_preserve_sub_clip` |
| `media_analysis` | 43 | `capabilities`, `install_guidance`, `get_caps`, `set_caps_preset`, `get_usage`, `get_resolve_ai_usage`, `get_ai_governance`, `set_ai_governance`, `resolve_output_root`, `plan`, `analyze_file`, `analyze_clip`, `analyze_bin`, `analyze_project`, `analyze_sequence`, `analyze_timeline`, `detect_sync_events`, `add_sync_event_markers`, `publish_clip_metadata`, `commit_vision`, `review_timeline_markers`, `summarize`, `get_report`, `build_index`, `index_status`, `query_index`, `start_batch_job`, `run_batch_job_slice`, `batch_job_status`, `list_batch_jobs`, `cancel_batch_job`, `resume_batch_job`, `cleanup_artifacts`, `strata_status`, `backfill_words`, `strata_run`, `take_diff`, `cut_candidates`, `strata_query`, `timeline_strata`, `plan_story_beats`, `commit_story_beats`, `list_story_beats` |
| `timeline_versioning` | 10 | `begin_run`, `end_run`, `list_runs`, `archive_current`, `list_versions`, `diff_timelines`, `get_history`, `rollback`, `prune`, `registry` |
| `edit_engine` | 8 | `plan_selects`, `execute_selects`, `plan_tighten`, `execute_tighten`, `plan_silence_ripple`, `execute_silence_ripple`, `plan_swap`, `execute_swap` |
| `timeline` | 90 | `list`, `get_current`, `set_current`, `get_name`, `set_name`, `get_start_frame`, `get_end_frame`, `get_start_timecode`, `set_start_timecode`, `get_track_count`, `add_track`, `delete_track`, `get_track_sub_type`, `set_track_enable`, `get_track_enabled`, `set_track_lock`, `get_track_locked`, `get_track_name`, `set_track_name`, `get_items`, `clip_where`, `delete_clips`, `set_clips_linked`, `duplicate`, `duplicate_clips`, `copy_clips`, `move_clips`, `ripple_insert`, `overwrite_range`, `lift_range`, `story_spine_report`, `create_variant_from_ranges`, `bulk_set_item_properties`, `apply_look_to_items`, `thumbnail_contact_sheet`, `marker_thumbnail_review`, `edit_kernel_capabilities`, `probe_edit_kernel_item`, `title_property_scan`, `set_title_text`, `bulk_set_title_text`, `create_compound_clip`, `create_fusion_clip`, `import_into_timeline`, `export`, `get_setting`, `set_setting`, `insert_generator`, `insert_fusion_generator`, `insert_fusion_composition`, `insert_ofx_generator`, `insert_title`, `insert_fusion_title`, `get_unique_id`, `get_node_graph`, `get_media_pool_item`, `get_transcript`, `propose_cuts`, `apply_cuts`, `get_mark_in_out`, `set_mark_in_out`, `clear_mark_in_out`, `convert_to_stereo`, `get_items_in_track`, `get_voice_isolation_state`, `set_voice_isolation_state`, `extract_source_frame_ranges`, `conform_capabilities`, `probe_timeline_structure`, `detect_gaps_overlaps`, `source_range_report`, `export_timeline_checked`, `import_timeline_checked`, `import_from_drp`, `compare_timelines`, `probe_interchange_roundtrip`, `detect_missing_media`, `build_relink_plan`, `conform_boundary_report`, `audio_capabilities`, `probe_audio_item`, `probe_audio_track`, `safe_set_audio_properties`, `audio_mix_capability_report`, `voice_isolation_capabilities`, `audio_mapping_report`, `safe_auto_sync_audio`, `transcription_capabilities`, `subtitle_generation_probe`, `fairlight_boundary_report` |
| `timeline_markers` | 22 | `add`, `get_all`, `get_by_custom_data`, `update_custom_data`, `get_custom_data`, `delete_by_color`, `delete_at_frame`, `delete_by_custom_data`, `get_current_timecode`, `set_current_timecode`, `get_current_video_item`, `get_thumbnail`, `get_thumbnail_image`, `annotation_capabilities`, `probe_annotations`, `normalize_marker_payload`, `copy_annotations`, `move_annotations`, `sync_marker_custom_data`, `clear_annotations_by_scope`, `export_review_report`, `annotation_boundary_report` |
| `timeline_frame` | 2 | `capture`, `capabilities` |
| `timeline_ai` | 5 | `create_subtitles`, `detect_scene_cuts`, `analyze_dolby_vision`, `grab_still`, `grab_all_stills` |
| `timeline_item` | 42 | `get_name`, `get_property`, `set_property`, `get_duration`, `get_start`, `get_end`, `get_source_start_frame`, `get_source_end_frame`, `get_source_start_time`, `get_source_end_time`, `get_left_offset`, `get_right_offset`, `set_clip_enabled`, `get_clip_enabled`, `update_sidecar`, `get_unique_id`, `get_media_pool_item`, `get_stereo_convergence`, `get_stereo_left_window`, `get_stereo_right_window`, `get_linked_items`, `get_track_type_and_index`, `get_source_audio_mapping`, `load_burnin_preset`, `set_name`, `get_voice_isolation_state`, `set_voice_isolation_state`, `get_retime`, `set_retime`, `get_transform`, `set_transform`, `get_crop`, `set_crop`, `get_composite`, `set_composite`, `get_audio`, `set_audio`, `get_keyframes`, `add_keyframe`, `modify_keyframe`, `delete_keyframe`, `set_keyframe_interpolation` |
| `timeline_item_markers` | 14 | `add`, `get_all`, `get_by_custom_data`, `update_custom_data`, `get_custom_data`, `delete_by_color`, `delete_at_frame`, `delete_by_custom_data`, `add_flag`, `get_flags`, `clear_flags`, `get_clip_color`, `set_clip_color`, `clear_clip_color` |
| `timeline_item_fusion` | 12 | `add_comp`, `get_comp_count`, `get_comp_names`, `get_comp_by_name`, `get_comp_by_index`, `export_comp`, `import_comp`, `delete_comp`, `load_comp`, `rename_comp`, `get_cache_enabled`, `set_cache` |
| `timeline_item_color` | 35 | `grade_evidence_base`, `grade_capabilities`, `probe_grade_item`, `probe_node_graph`, `grade_version_snapshot`, `color_group_capabilities`, `gallery_capabilities`, `grade_boundary_report`, `get_current_version`, `get_version_names`, `get_node_graph`, `get_color_group`, `get_color_cache`, `get_fusion_cache`, `safe_set_cdl`, `safe_copy_grade`, `safe_apply_drx`, `safe_export_lut`, `grade_version_restore`, `set_cdl`, `copy_grades`, `export_lut`, `reset_all_node_colors`, `add_version`, `load_version`, `rename_version`, `delete_version`, `assign_color_group`, `remove_from_color_group`, `set_color_cache`, `set_fusion_cache`, `stabilize`, `smart_reframe`, `create_magic_mask`, `regenerate_magic_mask` |
| `timeline_item_takes` | 7 | `add`, `get_count`, `get_selected_index`, `get_by_index`, `select`, `delete`, `finalize` |
| `gallery` | 8 | `get_album_name`, `set_album_name`, `get_current_album`, `set_current_album`, `get_still_albums`, `get_power_grade_albums`, `create_still_album`, `create_power_grade_album` |
| `gallery_stills` | 7 | `get_stills`, `get_label`, `set_label`, `import_stills`, `export_stills`, `grab_and_export`, `delete_stills` |
| `graph` | 11 | `get_num_nodes`, `get_lut`, `get_node_cache`, `get_node_label`, `get_tools_in_node`, `set_lut`, `set_node_cache`, `set_node_enabled`, `apply_grade_from_drx`, `apply_arri_cdl_lut`, `reset_all_grades` |
| `color_group` | 6 | `list`, `get_name`, `set_name`, `get_clips`, `get_pre_clip_graph`, `get_post_clip_graph` |
| `fusion_comp` | 41 | `add_tool`, `delete_tool`, `get_tool_list`, `find_tool`, `connect`, `disconnect`, `get_inputs`, `get_outputs`, `set_input`, `get_input`, `set_attrs`, `get_attrs`, `add_keyframe`, `get_keyframes`, `delete_keyframe`, `get_comp_info`, `get_position`, `set_position`, `copy_tool`, `auto_arrange`, `set_frame_range`, `get_frame_range`, `render`, `start_undo`, `end_undo`, `bulk_set_inputs`, `bulk_set_expressions`, `group_settings_export`, `group_settings_splice_inputs`, `group_settings_load`, `probe_group_published_inputs`, `fusion_graph_capabilities`, `probe_fusion_comp`, `probe_fusion_tool`, `safe_add_tool`, `safe_set_inputs`, `safe_connect_tools`, `fusion_boundary_report`, `add_fusion_mask`, `set_text_plus`, `get_text_plus` |
| `fuse_plugin` | 8 | `path`, `list`, `install`, `remove`, `read`, `validate`, `template`, `list_templates` |
| `dctl` | 8 | `path`, `list`, `install`, `remove`, `read`, `validate`, `template`, `list_templates` |
| `script_plugin` | 19 | `path`, `categories`, `list`, `install`, `remove`, `read`, `validate`, `template`, `list_templates`, `execute`, `run_inline`, `extension_capabilities`, `probe_fuse_lifecycle`, `probe_dctl_lifecycle`, `probe_script_lifecycle`, `safe_install_extension`, `safe_remove_extension`, `refresh_or_restart_required`, `extension_boundary_report` |
| `knowledge` | 4 | `topics`, `get`, `search`, `capabilities` |

## Live probe results — 41/41 read-only calls, all succeeded

Ran via a checkpointing script (`mcp_probe.py`, saved every 10 calls) against
the connected Studio instance. All 41 returned structured JSON with no
exceptions. Selected results:

```
resolve_control.get_version   -> product="DaVinci Resolve Studio", version_string="21.0.4.5"
project_manager.list          -> {"projects": ["test1"]}
project_manager.get_current   -> {"name": "Untitled Project", "id": "6964d1e6-..."}
project_manager_database.get_current -> {"db_type": "Disk", "db_name": "Local Database"}
media_storage.get_volumes     -> {"volumes": ["/home/iam/Videos"]}
media_pool.get_root_folder    -> {"name": "Master", "id": "49848a3b-..."}
render.get_formats            -> 22 formats (AVI, BRAW, DCP, DPX, EXR, JPEG2000/HT-J2K, MXF OP1A/Atom, MOV, ...)
media_analysis.capabilities   -> ffprobe, ffmpeg, and whisper_cli all detected as available
knowledge.capabilities        -> 35 knowledge topics indexed (2 repo, 12 workflow, 7 guide, 10 kernel, 4 reference), 62 aliases
graph.get_num_nodes (no timeline open) -> {"error": {"code": "NO_CURRENT_TIMELINE", "category": "precondition",
                                             "retryable": false, "remediation": "Open a timeline via ..."}}
```

`media_analysis.capabilities` correctly auto-detected the optional extras
installed earlier in this session — including finding `whisper` at
`~/resolve-install/davinci-resolve-mcp/venv/bin/whisper`, proving the venv
wiring is actually correct end-to-end, not just `pip install`-successful.

## Real workflow test: AAC audio, timeline edit, draft render — and what it actually found

The 41-action probe above was read-only and against an empty project. This section is
the opposite: a real mutating workflow (import real footage, build a timeline, render)
against the user's own `test1` project and real source clips
(`~/Videos/oldback_nvme/*.mp4`, H.264 video + AAC audio, matching the codec finding in
the main README). It surfaced problems the read-only probe never could.

### 1. AAC decode failure — confirmed at the sample level, not just a spec-sheet claim

Importing an AAC-audio clip into the Media Pool succeeded and even reported
`Audio Codec: AAC` as a clip property — that's just container-metadata reflection,
not proof of decode. Actually pushing it through the render pipeline surfaced the
real behavior, directly in `~/.local/share/DaVinciResolve/logs/ResolveDebug.txt`:

```
IO.Audio | ERROR | Failed to decode clip <.../sample_footage.mp4>, track: 0,
                    position: 2192640 - Failed to decode the audio samples.
```

Repeated **hundreds of times**, at the same handful of sample positions, in an
apparent retry loop — never succeeding, never failing fast. This is the first
first-party, log-level confirmation (not a vendor PDF, not a container-metadata read)
that **AAC genuinely does not decode on this Linux/Studio/NVIDIA combination** — matches
the official codec table in the main README exactly.

**The retry storm is what actually broke the render**, not raw slowness: a render job
covering ~6.6 seconds of 720p footage reported an estimated **5 hours** remaining. The
renderer wasn't grinding through real work — it was stuck retrying audio decode that
was never going to succeed, with no failure/timeout path exposed to the caller.

**Fix confirmed working, for the decode error specifically**: `ffmpeg -c:v copy -c:a
pcm_s16le` (video stream-copied untouched since H.264 decode itself is fine on
Studio+NVIDIA, only audio transcoded to PCM) eliminated every `IO.Audio` error in a
re-run — a real, working workaround for the AAC problem itself, not just an assumption.

### 2. A second, independent problem: the render pipeline doesn't recover cleanly after being interrupted

Stopping a render (`StopRendering()`, called because of the 5-hour estimate) left the
application in a degraded state that outlasted the specific AAC bug:

- **In GUI mode**: every subsequent `ImportMedia`, `SetCurrentFolder`, and even
  `Quit()` call **silently returned `None`** — no exception, no error field, nothing.
  Root cause, found by directly enumerating X11 windows (`xwininfo -tree`) since the
  API itself gave no signal: a modal dialog (a render-path validation prompt, then
  later a save-changes-on-quit prompt) was silently blocking every scripting call
  until a human clicked it — exactly what the project's own docs warn about
  (`resolve_control.runtime_mode`'s docstring), confirmed the hard way.
- **Killing the process while blocked on that dialog caused a real crash** (Blackmagic's
  own "Problem Report" crash handler triggered), not a clean exit — reproduced twice.
  `resolve.Quit()` does not pre-empt or avoid the save-changes prompt it triggers.
- **Headless mode (`-nogui`) avoided the dialog-blocking class of problem** (confirmed:
  a `DeleteRenderJob` call that returned `None` in GUI mode returned `True` immediately
  after relaunching headless) — but did **not** fully fix the underlying issue. After
  the render pipeline had been in a stopped/interrupted state, a fresh render attempt
  in headless mode got stuck at **0% completion with the estimated time climbing** (9s →
  148s and rising) while the process burned ~90% CPU with **zero new log output** for
  4+ minutes and 0% GPU utilization — a real internal stall/deadlock, not a dialog, since
  headless has no GUI to block on. The partial output file it did write had no `moov atom`
  (`ffprobe`: "Invalid data found when processing input") — genuinely corrupt, not just slow.
- **The only reliable recovery found**: a full process kill + clean relaunch. Confirmed
  twice — once fixed the GUI-mode `ImportMedia` lockup, but the fresh headless instance
  hit its own stall on the very next render attempt, suggesting the trigger is something
  about *interrupting a render*, not something specific to one process instance.
- **Important nuance, found afterward**: the crash-on-close only reproduced when the
  process was killed *while a render was still actively stuck/in-progress*. When the
  user instead **cancelled the stuck render job through the GUI first, then exited
  normally**, the app closed cleanly with no crash dialog. So the dangerous sequence
  specifically is kill-while-rendering, not "close Resolve after a render has gone
  wrong" in general — cancel the job before quitting, and normal shutdown works.
  Reinforces that this is a render-lifecycle-specific fragility, not a general
  instability in the app.
- **User-reported prior data point, tempered**: the user recalls H.265 rendering working
  fine previously on a Debian 12 install with an older Resolve version — but explicitly
  flagged that setup had **no MCP/scripting connection active at all**, so it's not a
  clean A/B comparison. Worth keeping as a lead (Debian 12 vs. 13, older Resolve version,
  or the scripting connection itself could each be a variable) but not yet isolated to a
  specific cause — flagged as a hypothesis, not a finding.
- **Also reproduced live in this session**: setting the render codec to H.265 through
  the GUI dropdown (which *does* list it as selectable, unlike what
  `Project.GetRenderCodecs()` reports via the API — a real inconsistency between what
  the GUI offers and what the scripting API says is available) and starting that render
  led to the same stuck-with-blocking-dialog pattern. The user separately reported that
  **cancelling an in-progress render job can itself hang** — not yet disentangled from
  the `File Destination` dialog re-appearing at the same time, which was confirmed
  independently blocking the GUI in that window. Needs a cleaner repro (confirm no
  dialog is open, then cancel a genuinely running render) before treating "cancel itself
  hangs" as a distinct bug rather than the same dialog-blocking issue recurring.
- **Converged root cause, confirmed the following round**: the same H.265 attempt
  reproduced with the `File Destination` window still present per `xwininfo` but
  confirmed `Map State: IsUnMapped` (a stale/invisible Qt object, not actually blocking
  anything) — yet the app was still fully unresponsive to the user. Process inspection
  (`ps -o stat,wchan`, `/proc/<pid>/status` thread states) found **no threads in
  uninterruptible I/O wait (D state)**, one thread pinned at ~99% CPU in `do_sys_poll`,
  377 threads total. That rules out "waiting on a dialog" or "waiting on disk/network
  I/O" as the mechanism here — this is a **genuine livelock/spin**, not a blocked wait.
  Combined with the earlier headless stall (~90% CPU, 0% GPU, zero log output, corrupt
  output file) and the AAC retry storm (hundreds of repeated failed decode attempts,
  never giving up), **the pattern converges across three independent triggers — AAC
  audio decode, ProRes video encode, H.265 video encode — and two independent modes
  (GUI, headless): once the render pipeline gets into a bad state, it does not time out,
  does not fail, and does not recover. It spins.** The only fix found across every
  reproduction was killing the process and relaunching.

### What this means for "is it worth forking"

The read-only/informational surface (Section above, 41/41) is genuinely solid — clean,
typed, well-documented. The gap is specifically in **render-pipeline resilience once a
render has been started and then interrupted or hit a slow/failing clip** — silent
`None` returns with no diagnostic signal, dialog-blocking in GUI mode, and a distinct
stall/deadlock mode even in headless mode that the project's own headless-mode
documentation doesn't claim to fix. This is a narrower, more specific gap than "the
whole project needs rework" — the fix that seems warranted isn't a full fork, but a
**resilience wrapper**: detect a stuck render (0% completion + static output file size
over N seconds), detect a `None` return where a real value was expected and treat it as
a probable-blocked-dialog signal, and default to "kill and restart" as an explicit,
logged recovery path rather than something a human has to diagnose by hand (as was done
here). Worth checking `docs/reference/api-limitations.md` in the checkout and possibly
filing this upstream before assuming a local patch is needed — this may already be a
known, curated gap.

## Final data point before stopping: thumbnails stopped rendering too

After the last kill+restart cycle (following the H.265 livelock), Media Pool clip
thumbnails came back as generic music-note icons instead of real video previews, and
the Cut page's viewer stayed black with the playhead sitting mid-clip and a waveform
visibly present underneath. GPU showed memory allocated (1.4GB/8GB) but near-zero
utilization (5%) — alive, just not being used for thumbnail/preview generation. Not
investigated further (session paused here by the user's call) — plausibly the same
underlying media/GPU pipeline degradation as everything else in this section, but not
confirmed. **Worth checking first thing next session**: whether a full graphical
session restart (not just Resolve) clears it, which would point at GPU/driver-level
state (texture cache, GL context) rather than something inside Resolve's own process.

## Investigation paused here — what's next, not done in this pass

The user called a stop to live debugging at this point given how much was already
found and documented; this is a deliberate pause, not a dead end. Picking this back up:

- **Root-cause the render livelock** before anything else — three independent triggers
  (AAC decode retry storm, ProRes encode, H.265 encode) and two modes (GUI, headless)
  all converge on the same spin/livelock signature. That convergence is the strongest
  lead: whatever's common to all three (the render/encode subsystem's internal state
  machine, most likely) is where the actual bug lives, not the codec-specific paths.
- **Check whether a full graphical session restart** (not just Resolve) clears the
  thumbnail/preview rendering gap noted above — cheap to test, would meaningfully
  narrow whether this is Resolve-process-local or GPU/driver/session state.
- Only 41 of 667 actions were exercised, and only against an empty project —
  deliberately conservative (no `set_*`/`create`/`delete`/`execute_*` against
  real project state without asking first). Mutating-action testing belongs
  in a real editing/export workflow test, not a blind capability sweep.
- The Node "advanced" (offline `.drp`/`.drt`/`.drx`) server's 18 tools are
  cataloged in the README but not yet live-probed the way the Python server
  was here.
- Granular mode (`--full`, 353 tools) not probed — the compound server is
  what's actually planned for use.
