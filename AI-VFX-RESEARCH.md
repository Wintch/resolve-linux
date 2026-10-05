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

## AuK — Foundational Model for Speech Generation & Editing (Tencent Hunyuan, 2026-09)

**Source**: [Tencent-Hunyuan/AuK on GitHub](https://github.com/Tencent-Hunyuan/AuK)
| arxiv: [2609.08936](https://arxiv.org/abs/2609.08936)
| [HuggingFace Demo](https://huggingface.co/spaces/tencent/AuK)

**AuK** is a **1.5B open-source foundation model** for speech generation and editing,
trained on millions of hours of diverse audio data. Every task is exposed through the
same **natural-language instruction interface** — no task-specific heads or separate
models needed.

### Two variants

| Model | Description | Weights |
|---|---|---|
| **AuK** | Base model, high-quality generation | [HuggingFace](https://huggingface.co/tencent/AuK) · [ModelScope](https://modelscope.cn/models/Tencent-Hunyuan/AuK) |
| **AuK-Flash** | Distilled, fast **4-step inference** | [HuggingFace](https://huggingface.co/tencent/AuK-Flash) · [ModelScope](https://modelscope.cn/models/Tencent-Hunyuan/AuK-Flash) |

### Full task coverage (single instruction interface)

| Category | Tasks |
|---|---|
| **Speech Generation** | Zero-shot TTS (voice cloning from reference audio), Instruct TTS (voice from description alone, no reference) |
| **Content Editing** | Speech content editing (replace/insert/remove text while preserving voice), Lyric editing (rewrite lyrics while preserving melody and voice) |
| **Acoustic Editing** | Pitch (semitone control), Speed (rate × output length), Volume (dB adjustment) |
| **Paralinguistic Editing** | Emotion, Timbre, De-accent (remove regional accent while preserving speaker), Nonverbal editing (add/remove breaths/laughs/coughs), Whisper ↔ normal conversion |
| **Enhancement & Separation** | Speech enhancement (denoise/dereverberate), Speech separation (isolate speaker by talking order), Music source separation (vocals/instruments), Binaural conversion |

### Ecosystem / inference backends

- **ComfyUI**: native workflow support
- **SGLang-Omni**: Day 0 support, two-stage pipeline on single H100
- **vLLM-Omni**: serve AuK + AuK-Flash as two-stage pipeline
- **MLX (Apple Silicon)**: official support via `feat/mlx-apple-silicon` branch
- **CUDA CPU offload**: available since 2026-09-13
- **Memory optimization**: encoder footprint reduced ~7.5 GiB (2026-09-16), making
  local inference on consumer GPUs practical

### GGUF port — `audio.cpp`

**[audio-cpp/AuK-Base-and-Flash-GGUF](https://huggingface.co/audio-cpp/AuK-Base-and-Flash-GGUF)**
on HuggingFace — GGUF quantizations of both Base and Flash variants for offline
inference via [`audio.cpp`](https://github.com/0xShug0/audio.cpp) (`--family auk`).

Community docs: [audio.cpp AuK model doc](https://github.com/0xShug0/audio.cpp/blob/main/docs/community_models/auk.md)

Key usage notes for the GGUF port:
- Run with `audiocpp_cli --family auk`
- Keep `config/` and `tokenizer/` directories alongside the model files
- Switch between Base and Flash: `--session-option auk.variant=flash`
- Components bundled: base model + VAE + Qwen2.5-Omni-3B (for instruction processing)

> [!NOTE]
> `audio.cpp` is the audio-domain equivalent of `llama.cpp` — not the same engine.
> The GGUF port runs AuK locally without Python/PyTorch; inference requirements are
> significantly lower than the full torch stack.

### Notable external recognition

- Selected as the **end-to-end baseline for the Single Model Track** of the
  [ICASSP 2027 Audio Editing Challenge](https://audio-editing-challenge.github.io/)

### Relevance to this rig / workflow

- **Voice cloning / zero-shot TTS**: generate narration or commentary in a specific
  voice from a reference clip — no re-recording needed. Direct use case for
  documentary-style edits in the event-highlight pipeline.
- **Speech content editing**: fix a line without re-recording the speaker. "Replace
  this word, preserve the voice" is exactly what a dialogue editor spends time on.
- **Speech enhancement**: denoise/dereverberate interview or field-recording audio
  before importing into Resolve. Complementary to Resolve's own Fairlight noise
  reduction — useful for clips that need heavier restoration than Fairlight handles
  cleanly.
- **Audio separation**: isolate vocals or individual instruments from a mixed track.
  Practical for event footage where music was playing in the background and needs to
  be attenuated cleanly.
- **AuK-Flash + GGUF**: the 4-step distilled variant via `audio.cpp` is the most
  realistic path to running this on this rig's 8GB VRAM without a full PyTorch stack.
  Worth benchmarking before committing to the full Base model.

> [!NOTE]
> AuK outputs audio, not video — it doesn't touch the visual pipeline at all. Its
> place in the workflow is in audio post: TTS, dialogue cleanup, enhancement, and
> separation. All of those happen in Resolve's Fairlight page or as preprocessing
> steps before import.

---

*Last updated: 2026-10-05*
