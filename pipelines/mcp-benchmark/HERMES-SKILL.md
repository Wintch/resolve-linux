# davinci-resolve skill for Hermes (headless MCP on iashur)

Source of truth for the Hermes-side `davinci-resolve` skill (lives in `bridgeai`; copy from here).
Every call below was run live against Resolve Studio 21.1.1 `-nogui` through
`resolve_mcp_wrapper.sh headless` on 2026-10-05, except where marked *not verified*.

## The one rule

Every community tool is `tool(action="<name>", params={...})`. **All arguments go inside
`params`.** Top-level arguments are dropped silently by schema validation, so the action sees
empty params and answers `name is required` / `'track_type' is required` / `Provide clip_ids ...`.
Keys are **snake_case** (`clip_ids`, `clip_infos`, `track_type`); camelCase such as `clipInfos`
is not read. `params` given as a JSON string is also accepted.

## Verified recipe (flip a clip)

```python
project_manager(action="safe_project_create", params={"name": "hermes-work", "allow_non_mcp_name": True})
r = media_pool(action="safe_import_media", params={"paths": ["/home/iam/Videos/x.mov"]})   # "paths", not "file_paths"
clip_id = r["clips"][0]["id"]
media_pool(action="create_timeline", params={"name": "edit"})        # a new timeline already has video track 1
media_pool(action="append_to_timeline", params={"clip_ids": [clip_id]})   # simple form
timeline(action="get_items", params={"track_type": "video", "index": 1})  # -> items with id/start/end/duration
timeline_item(action="set_transform",
              params={"track_type": "video", "track_index": 1, "item_index": 0, "FlipX": True})
timeline_item(action="get_transform", params={"track_type": "video", "track_index": 1, "item_index": 0})  # FlipX: true
```

Other verified calls: `media_pool.create_timeline_from_clips {name, clip_ids}`,
`timeline.get_track_count {track_type}`, `timeline.add_track {track_type}` (count 1 -> 2),
`project_manager.close {}`, `project_manager.safe_project_delete {name, allow_non_mcp_name: true}`.

## Gotchas

- Import paths must be inside a registered Media Storage volume (`/home/iam/Videos`, `/mnt/videos`).
- `item_index` is 0-based; `track_index` and `index` for tracks are 1-based.
- `create_timeline` with an existing name returns a versioned name (`edit v02`); read `name` from the result.
- Destructive/timeline-mutating calls auto-archive the timeline (`archived_version` in the result); that is expected.
- `safe_project_create` / `safe_project_delete` refuse names not starting `_mcp_` unless `allow_non_mcp_name: true`.
- `set_property` returned `success:true` for `Pan`; read-back not verified. Color controls
  (Lift/Gamma/Gain...) are not `set_property` keys: use the node graph / `timeline_item_color` (not verified here).
- Brightness, text overlays and other filters are not transform properties: do them in ffmpeg.

## The only real headless limit

The render queue does not advance under `-nogui` (`render.start`/`is_rendering`). Edit and verify in Resolve,
deliver with ffmpeg on iashur. Do not claim an ffmpeg output as "made in Resolve".

## Diagnosing a failure

1. Is it `... is required`? You passed arguments outside `params` or used a wrong key; re-read the action's
   signature with `resolve_control(action="describe_api", ...)` or the tool docstring.
2. Errors carrying a `remediation` field name the fix; follow it.
3. Do not conclude "headless can't do X" without trying the call with a correctly nested `params`.
