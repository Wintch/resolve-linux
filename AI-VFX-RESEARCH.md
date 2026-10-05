# AI VFX Research

Reference log of AI/ML tools and techniques relevant to the post-production workflow
on this rig. Entries come from active community research (Reddit/HuggingFace/docs) —
not all of these are integrated pipelines yet, but every entry here was vetted enough
to be worth tracking.

---

## LTX-2.5 — VFX/Post-Production Toolset (Lightricks, 2026-10)

**Source**: [r/StableDiffusion — From 3D layout to compositing: new LTX VFX tools](https://www.reddit.com/r/StableDiffusion/comments/1wya9r3/from_3d_layout_to_compositing_new_ltx_vfx_tools/)
| ~234 upvotes | posted by u/ltx_model (Lightricks official account)

During VFX Week, Lightricks released **seven open-weight IC-LoRAs** for LTX-2.5 (22B
parameter model), targeting professional VFX and post-production workflows. All weights
are on Hugging Face; all workflows run in **ComfyUI**.

### The 7 tools

| Tool | What it does | Link |
|---|---|---|
| **Native Resolution** | Run AI edits on 4K/8K plates without downscaling. Overlapping tiles preserve fine detail on a single GPU. | [docs.ltx.io](https://docs.ltx.io/open-source-model/vfx-post-production/native-resolution) |
| **Refine** | Generates the fine detail a standard upscale/resize leaves soft, without drifting from the source. | [HuggingFace](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Refine-Details) |
| **Restore** | Takes archival footage from as low as 540p to 4K; enhances color to a modern digital-camera look. | [HuggingFace](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Restore) |
| **SDR to HDR** | Rebuilds shadow/highlight detail in SDR footage and outputs **16-bit EXR in ACEScg** — drop-in alongside HDR material. | [HuggingFace](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-SDR-To-HDR) |
| **Native HDR** | Bring in an EXR sequence; apply edits (relight, day-to-night, inpaint); get 16-bit EXR back with the full range intact. | [docs.ltx.io](https://docs.ltx.io/open-source-model/vfx-post-production/working-with-exr-hdr) |
| **Layout to Render** | Turns a **3D blockout into a rendered shot** — camera, framing and geometry stay locked to the layout; a prompt or reference image sets the look. | [HuggingFace](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Layout-To-Render) |
| **Alpha Gen** *(beta)* | Generates an **alpha matte from plain RGB footage** — no green screen, no masks, no prompt. Handles hair, fur, smoke, glass, water. ComfyUI workflow (distilled) already available; full-model workflow coming soon. | [HuggingFace](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Alpha-Gen) |

### Key facts

- All tools are **open-weight** (free to run locally).
- IC-LoRAs run on top of LTX-2.5 22B — the same base model, different task adapters.
- Workflows are ComfyUI-native. No proprietary runtime required.
- Output colorimetry where relevant: **ACEScg**, **16-bit EXR** — professionally
  compatible with standard VFX pipelines.

### Relevance to this rig

- **Alpha Gen** is the most immediately relevant: rotoscoping/matte generation without
  a green screen is a recurring need in event footage, and running it locally avoids
  cloud subscription costs.
- **Restore** maps directly to archival/upscale work (540p → 4K).
- **Native Resolution** + **Refine** could complement this rig's existing proxy workflow
  (see `PERFORMANCE.md`) — apply AI refinement on the export pass rather than on the
  proxy-resolution edit.
- **SDR to HDR / Native HDR**: not a current priority but relevant if the project ever
  targets HDR delivery. The ACEScg EXR output means it integrates cleanly with
  Resolve's color-managed pipeline rather than needing an extra conversion step.
- **Layout to Render**: interesting for pre-vis/blockout-to-photoreal shots if the
  project expands into compositing-heavy territory.

> [!NOTE]
> VRAM constraint: this rig has 8GB VRAM (RTX 3060 Ti), below what a full 22B model
> typically needs in one pass. The ComfyUI workflows may require tiling strategies or
> model offloading. Not yet tested on this hardware — benchmark before committing to
> any of these in a live pipeline.

---

## MiniMax H3 — 360° Orbit LoRA (community, 2026-10)

**Source**: [r/StableDiffusion — Orbiting LoRA + first and last frame in MiniMax gives fantastic results](https://www.reddit.com/r/StableDiffusion/comments/1wuzyq0/orbiting_lora_first_and_last_frame_in_minimax/)
| ~2,252 upvotes (99% ratio) | 223 comments | posted by u/AndrewJumpen

A community-discovered technique combining a **360° orbit LoRA for MiniMax H3** with
the model's first-and-last-frame conditioning to produce **frozen-world camera orbits**
around any still or video frame. The post author describes it as *"basically a new way
to extract 3D models from any movie."*

### What it does

Given an image (or a first + last frame pair), generates a video where:
- The **scene is completely frozen** — every person, object, airborne item stays in
  exactly the same world position, pose, and orientation throughout.
- The **camera orbits 360°** around the scene. Camera parallax is the *only* source of
  apparent movement.
- No cuts, zoom, morphing, or added objects.

### The LoRA

**[pablodawson/MiniMax-H3-360-Orbit-LoRA](https://huggingface.co/pablodawson/MiniMax-H3-360-Orbit-LoRA)**
on HuggingFace. Runs with MiniMax H3 (video diffusion model).

### Prompt that works

The LoRA is guided by a specific prompt structure that locks the freeze behavior:

```
One frozen instant. Only the camera moves. In a continuous 360 orbit. Preserve every
person and object in exactly the same world position, orientation, shape and pose
throughout the shot. Airborne objects remain suspended at the captured height and angle:
no wobbling, shaking, spinning, drifting, falling or continued action. Keep faces,
hands, clothing, liquids and the background motionless while retaining their natural
appearance. Camera parallax is the only source of apparent movement. No cuts, zoom,
morphing or added objects.
```

### Community reaction / context

- The result quality surprised the community significantly — upvote ratio 99%, 223
  comments.
- Multiple users independently noted this technique is convergent with **Gaussian
  Splatting** / 3D reconstruction workflows: the orbit video can serve as input to a
  splat reconstruction pipeline to get a 3D scene from any single image.
- u/BigWideBaker noted: *"We've had a lot of posts independently discovering this exact
  orbit camera + Gaussian splatter/3D model setup."* — confirms this is part of a
  broader emerging workflow, not a one-off trick.
- The author confirmed: *"Yep basically new way to extract 3D models from any movie."*

### Relevance to this rig / workflow

- **VFX pre-vis / hero-shot orbit**: quick orbit around a still or a live-action frame
  for review without a motion-control rig. Useful for director review / client
  presentation before committing to a physical shoot.
- **Gaussian Splatting input**: the frozen-world orbit provides a clean, parallax-only
  camera move — exactly the kind of input that 3D reconstruction tools expect. Combine
  with a splatter (e.g. `nerfstudio`, `gsplat`) to get a navigable 3D scene from a
  single photo or short clip.
- **Event footage**: photographing a group shot then generating an orbit around it is a
  realistic use case for the event-highlight pipeline documented in
  `pipelines/raw-highlight/`.

> [!NOTE]
> MiniMax H3 is a cloud/API-first model at the time of writing — running it fully
> locally requires access to the model weights, which may not be publicly available yet.
> Check HuggingFace availability before planning this into a local pipeline.

---

*Last updated: 2026-10-05*
