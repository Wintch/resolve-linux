# How to build an event highlight from RAW — the full rundown

We built this to process ~300 RAW photos from a live event (plus whatever RAW video
clips the camera happened to grab) and turn them into a short highlight video with
music, good color, and pacing. The idea of this guide is that it works for
**anyone** — another AI, an editor on a Mac, someone on a Windows PC — without
assuming what software you have installed. That's why it's written in terms of
*what to do and why*, not commands copy-pasted from one specific terminal. Where we
give a concrete example (a tool we actually used, on Linux), we explain the general
logic behind it so you can translate it to whatever you have on hand.

Relaxed tone, straight to the point. Let's go.

## About this repo

This collects reusable guides and pipelines for RAW/event-media processing — this is
the first one, more may get added over time. The working implementation used to
develop and validate this guide lives in [`tools/`](tools/).

## The general idea (this is what matters, the rest is detail)

RAW is heavy and slow to work with directly, no matter what software you use. So the
pipeline logic is:

1. **Generate a lightweight intermediate codec** for everything, so you can
   browse/review/assemble quickly without re-reading the RAW constantly. It doesn't
   need to be any particular format — what matters is that it's fast to open and
   decent to look at.
2. **Group whatever is "the same shot"** (bursts, repeated takes, video clips from
   the same moment). Evaluating 300 photos one by one is a slog, and a lot of them
   are going to be nearly identical anyway — better to think of them as groups.
3. **Score automatically** (sharpness, exposure, whatever you come up with) to build
   a preselection, but **with mandatory human review before anything is considered
   final**. An automatic algorithm doesn't understand "this photo is weird but it's
   the best one from that moment," and it doesn't reliably tell a rotated photo from
   a genuinely ruined one. Event lighting (dark, colored stage lights) breaks any
   "this looks nice" heuristic.
4. **Do the real processing (color correction, noise reduction, whatever's needed)
   only on what survived the selection** — never on the full batch. It's expensive
   in time to do it on everything, and there's no point spending that time on
   material you're not going to use. And always starting from the original files,
   not from the lightweight proxies from step 1.
5. **Assemble the final cut**: motion effects on single photos, real use of clips/
   bursts that have their own motion, transitions, music, and a separate final
   quality pass before delivery (see below for why it's separate).

## Tools — by category, not by specific name

It doesn't matter what operating system you're on, you need something in each of
these categories. We give examples — pick whichever one you have or feel most
comfortable with:

- **A RAW developer with a batch/command-line mode.** You need to be able to throw
  a RAW at it and get back a processed image without opening a window, so you can
  automate 300 of them at once. Examples: **darktable** (`darktable-cli`, free,
  Win/Mac/Linux), **RawTherapee** (`rawtherapee-cli`, free, cross-platform), Adobe
  Camera Raw via Photoshop/Lightroom scripts (paid, but works fine if you already
  have it installed), Capture One with its tethering tools. What matters: it
  supports batch export and, ideally, applying a reproducible style/preset.
- **A video tool with scripting capability.** **ffmpeg** is the obvious answer here
  for any operating system — it's free, runs on Win/Mac/Linux, and basically
  everything described below (zoom effects, transitions, mixing audio, changing
  codec) is done with it. If you'd rather have a GUI, DaVinci Resolve also has a
  scripting engine (Python/Lua) and is free in its base edition, but it's much
  heavier for simple automated tasks.
- **Something that reads EXIF metadata.** `exiftool` is free, cross-platform, and
  the de facto standard for this — you need it to read sub-second-precision
  timestamps (key for grouping bursts) and to copy metadata between files.
- **A scripting language with basic image libraries.** Python with
  `numpy`/`scipy`/`Pillow` was enough for all the scoring (sharpness, exposure) —
  no heavy machine learning needed. Node.js with `sharp`, or any other language
  you're more comfortable with, works just as well if you know how to build the
  logic.
- **(Optional but recommended) A GPU to speed up video encoding.** If you have a
  decent GPU (NVIDIA, AMD, Apple Silicon), use it for the fast-iteration stages — it
  makes the difference between waiting seconds and waiting minutes every time you
  try a change. Not required, just convenient.

**You don't need to pay for anything** to put this pipeline together —
darktable/RawTherapee + ffmpeg + exiftool + Python cover 100% of it, on any
operating system.

## Phases of the process, with expected timings

For scale: we ran this on 289 photos of ~23MB each (RAW from a mid-range APS-C
camera), on a modest machine (8 cores, 15GB RAM, an old 2GB GPU). Adjust for your
own hardware, but it's useful as an order-of-magnitude reference.

### Phase 0 — Before touching anything: inventory your sources

Two cheap checks that save hours if you skip assuming instead:

- **Is there more than one card/folder from the camera?** Cameras tend to create a
  new folder (numbering like `100CANON`, `101CANON`) once the previous one hits a
  certain file-count limit, or when you reinsert the card. It's very common to
  remember only one and process a whole event thinking it's complete, only to
  discover a second batch later with completely different file numbering
  (sometimes the numbering even resets to zero). Check the full card/camera before
  considering ingestion closed.
- **Is the material photos only, or did the camera also record video?** A lot of
  DSLRs/mirrorless cameras (especially with modified firmware like Magic Lantern)
  also produce RAW video clips during the same event. If your plan is "I process
  photos and build the video from those separately," a video clip mixed into the
  folder breaks that assumption — identify it early and decide up front whether it
  goes into the same final cut or gets processed separately.

**If the event gets uploaded in more than one batch** (happened in our case: we
remembered a second camera folder after already having processed the first one in
full), a few things to keep in mind:

- Most pipelines of this style assume "one RAW folder at a time" — the burst
  grouper and the scorer tend to **overwrite** their results file instead of
  merging into it. Before re-running the pipeline on batch 2, make a copy of batch
  1's analysis results (the burst JSON, the shortlist, etc.) if you're still going
  to need them — costs nothing and saves you from losing the record of what was
  discarded and why.
- Check that filenames don't collide between batches before assuming they can be
  mixed into the same output folders without clobbering each other.
- The good news: if your Phase 5 (final processing) exports to a format with its
  own filename per photo (not a single cumulative file), batches can be processed
  separately without touching the previous one, and only get merged in Phase 6
  (video assembly) — you don't need to reprocess anything already approved just
  because more material from the same event showed up.

### Phase 1 — Lightweight proxies (minutes)

Convert each RAW to a compressed-but-good-quality format (AVIF, high-quality JPEG,
whatever you prefer) so you can browse quickly. With 289 photos, in parallel, this
took **~2-3 minutes** and reduced 6.6GB to 1.5GB. Copy the full EXIF over to the
proxy (with `exiftool -TagsFromFile`) so you don't lose metadata along the way.

**Style consideration**: you don't need full resolution here, or maximum quality —
this is for *looking*, not for the final result. Visible-but-decent compression
(for example, quality ~80 on a 100 scale) shrinks the size a lot without it showing
during review.

### Phase 2 — Group by burst (seconds)

Sort by timestamp (sub-second precision if the camera records it) and group
consecutive shots taken close together in time. The threshold depends on the
camera's burst speed — at ~4 shots per second, a cutoff of ~0.5-0.6s between shots
does a good job of separating "same burst" from "new photo." It's cheap to run, so
do it as many times as needed until the threshold makes sense against the real
data (don't assume a number, look at the deltas).

### Phase 3 — Score and build a suggested selection (minutes, depending on photo count)

For each group, some sharpness metric (the variance of a Laplacian-type filter
works well and is cheap) and some exposure metric (fraction of blown/black pixels).
With that: pick a representative per group, and split out large groups (a long
burst, say 5+ shots) to treat differently — in the final video they can become a
"mini clip with its own motion" instead of a single static photo.

Generate a contact sheet (thumbnails with a visible ID) for a human to look at.
**This is not optional.** The automatic score is a filter for the obviously bad,
not a curator.

### Phase 4 — Human review (however long it takes — this is the part that doesn't rush)

This is where the person sets the pace, not the machine. Things worth checking:

- **Are there rotated photos?** If the camera didn't save the orientation tag in
  EXIF (happens more often than you'd expect), there's no reliably automatic way
  to know — maybe a face/horizon-detection library gives you a hint, but for events
  with odd lighting and people in odd poses, it's faster to have a human look at a
  chronological grid and give you a list of exceptions ("these go rotated 90° this
  way") than to build automatic detection. Note: almost always **a whole session
  is rotated the same way** (the photographer holds the camera the same way every
  time), so confirming 2-3 cases lets you generalize to the rest of that batch.
- **What's excess?** "Atmosphere" shots without much value (crowd from behind,
  lights, nothing recognizable) — decide whether they all go in, none of them, or
  a scattered few to vary the pacing.
- **What's misclassified?** Sometimes the algorithm discards something that's
  actually good, or approves something that's obviously unusable at a glance
  (false positive/negative). Also build a video/contact-sheet of **what got
  discarded** so you can rescue things — it's much easier to spot an error looking
  at the full set than blindly trusting a threshold.

**Time-saving trick**: when you ask for feedback on an already-assembled video
("cut the thing at second 12"), burn a visible identifier (text like "group X,
second Y") into each clip in the video. Mapping "second of the video" → "which
photo/group is that" by hand, every time there's feedback, is an unnecessary drag.

### Phase 5 — Final processing of what was approved (minutes to an hour, depending on how much denoise)

Only now do you run color correction and serious noise reduction, and **only on
what you approved**, starting from the original files. A good-quality noise
reducer (non-local-means type) is quite a bit slower than a basic one, but since
you're running this on a fraction of the total (not the 300 photos, the ~70-100
that survived), it's worth paying the time cost for better quality.

**Don't forget about noise — for events it's almost always necessary, not
optional.** A live event (concert, party, a wedding at night) almost always forces
high ISO — 3200, 6400, 12800 aren't unusual in low light — and that brings visible
sensor grain/noise, especially in the shadows. Check the real ISO of your photos
(it's in the EXIF) before assuming "it won't be needed": the first shots of a
session (before the photographer adjusts) tend to be the worst on this front.
Apply denoise **whenever** the ISO is high, not just "if it's noticeable at a
glance" on the small proxy — it shows a lot more on the full-size photo.

**Even better: don't use the same denoise strength for the whole batch — scale it
by each photo's real ISO.** A multi-hour event almost never has a single ISO — the
photographer raises/lowers it with the light of the moment. If you apply the
strength your noisiest photo needs to *all* of them, you're eating away extra
detail on the ones that need it least; if you apply the strength of the cleanest
one to all of them, the high-ISO ones are left with visible noise. The ISO source
doesn't have to be the RAW itself — if you already copied the full EXIF to the
lightweight proxy in Phase 1 (as recommended there), you can read the ISO from
there without touching the RAW again. Define 3-4 tiers (for example: low/medium/
high/very high ISO, each roughly double the previous one — matches how cameras
tend to step through ISO) and test each tier's strength on 1-2 representative
photos before applying it to the rest, same approach as with the color preset. The
noise pattern depends on the specific body+sensor, so what you calibrate for one
camera isn't a hard reference for another — redo the calibration if the gear
changes.

**Photos that survived the selection but came out very dark** (correct focus, but
insufficient exposure) can be fixed, with a clear ceiling: the only thing you can
recover is what's already captured in the signal, there's no new information to
pull out of nothing. A standard "shadow lift" (gamma, CLAHE-type histogram
equalization) raises the shadows proportionally, but on a photo that's *almost
entirely* dark, that mostly brings pure sensor noise into the light, not real
information — there's no signal there to recover. A technique that performs
better for this specific case ("black-anchored levels stretch"): leave the input
black point anchored at 0% (the image's real black doesn't move), and **lower the
input white point** to wherever the brightness ceiling of your dark photo actually
is (for example, if the brightest part of the image sits at ~25-30% of the scale,
put it there instead of at 100%) — that stretches the narrow band of real
information that does exist to fill the whole output range, without touching the
true black or amplifying the noise floor of the shadows that were already pure
black. It's basically the inverse of a shadow lift: instead of lifting the shadows
further, you lift what already had some brightness and leave the deep shadows
nearly untouched (there's nothing to lift there but noise). Optionally add local
contrast (CLAHE) and an extra denoise pass plus light sharpening (unsharp) to
compensate for the noise this stretch does reveal — the full combination (levels
with anchored black + CLAHE + denoise + light sharpening) beats any single
technique on its own. As with everything else in this phase: try it on 1-2 photos,
look at the result, then generalize.

**Terminology note** (so it doesn't get confusing when asking for/giving manual
brightness adjustments): a **real stop of light is a factor of ×2 or ×0.5**, not
just some percentage. "Lower it 20%" is multiplying by 0.8, which is
*approximately 1/3 of a stop* (`log2(0.8) ≈ −0.32 EV`) — quite a bit more subtle
than "lower it a stop" (which would be ×0.5, half the light). If you're going to
iterate on brightness adjustments by hand with someone else, it's worth clarifying
which of the two units is being used.

**Color style consideration**: don't guess at color preset numbers in the
abstract — look at a real photo from your batch before applying it to everything.
An adjustment that looks "cool" on a well-lit photo can ruin an already-dark event
photo (losing the subject in the shadows, for example). Iterate the preset on 1-2
representative photos first.

### Phase 6 — Assemble the video (seconds to minutes, in "iterate fast" mode)

**First, a check that's easy to skip and hurts later**: look at your photos' native
aspect ratio (for example 3:2 on many DSLRs/mirrorless, or 4:3 on others) against
the delivery aspect ratio you're planning (for example 16:9 widescreen). If they're
different and your framing filter does "scale to cover the frame + crop the
overflow" (the most common pattern, something like
`scale=...:force_original_aspect_ratio=increase,crop=...` in ffmpeg or the
equivalent in any editor), **that crop eats into the top and bottom (or the sides)
of every photo**, without warning — there's no error, the video comes out and looks
"fine" at a glance, it's just missing a chunk of each shot. If your source doesn't
match your delivery format, consciously choose between: cropping (you lose
content, more "cinematic"), or keeping the video in the source's native aspect
ratio / adding letterbox-style bars (you don't lose anything, but it's not the
usual 16:9). Don't leave it as a default without thinking about it — it's a style
decision, not a minor technical detail.

**If you also have RAW video clips from the camera** (Magic Lantern or otherwise),
good news: you don't always need a specialized tool to decode them — modern
`ffmpeg` already ships demuxers for several RAW video formats (try `ffmpeg
-demuxers | grep -i <format>` before going out to install/compile something). What
is worth checking with `ffprobe` before cutting them into the edit: the clip's real
resolution and aspect ratio (don't assume it matches your photos — if it doesn't,
the same cropping problem from above applies), frame rate, and whether it has a
camera-microphone audio track you might want to keep or drop.

**Watch out: `ffmpeg` recognizing the container doesn't mean it can decode the
video inside it.** This happened to us with Magic Lantern MLV: the demuxer reads
the file structure perfectly (resolution, fps, metadata, even the PCM audio
extracts fine with `-map 0:1 -c:a copy`), but throws "no decoder found" for the
video — because the video is usually compressed losslessly (LJ92, a lossless-JPEG
variant) and `ffmpeg` doesn't ship a decoder for that in this particular
container. If something similar happens to you: first confirm the video is
actually compressed (compare the frame size against the expected uncompressed size
— bytes per frame vs. width×height×bits/pixel/8; if the file is noticeably
smaller, it's compressed). Look for whether the format has a standalone,
lightweight decoding library (for LJ92 we found `liblj92`, a single `.c`/`.h` with
no dependencies, part of the MLV App project) — you don't need to build the whole
GUI application, it's enough to write a minimal C program that calls that library
per frame and dumps the raw pixels to stdout. From there, `ffmpeg` does know how to
handle the rest (basic Bayer demosaicing with `-f rawvideo -pix_fmt
bayer_rggb16le -s WIDTHxHEIGHT`, color, encode) — the only missing link was that
specific decompression step. It's important to set up a direct pipe (decompressed
frame → `ffmpeg`'s stdin) instead of writing each raw frame to disk: a few seconds
of RAW video can generate gigabytes of uncompressed frames if you dump them all to
individual files.

Single photos with a subtle motion effect (slow Ken-Burns-style zoom), large burst
groups as mini-clips playing their frames in sequence (gives a sense of real
motion, better than forcing them into a single static photo), transitions between
segments, music.

**Film damage texture (optional, but gives life to a cut that mixes photos and
video)**: if you're going for a more "handheld"/analog feel instead of a clean,
static cut, there's a handful of classic effects that work well on top of still
photos with Ken Burns applied — precisely because they mask how static the source
image is:
- **Grain** — a layer of fine noise (generated or from a real scanned texture)
  blended on top with an "overlay" or "screen" blend mode at low opacity. Breaks up
  digital flatness.
- **Halation/light leaks** — a warm glow that bleeds from the edges or from the
  brightest blown-out areas of the image, simulating light reflections leaking
  into physical film.
- **Tape burns / strong light leaks** — more aggressive than subtle halation,
  useful as a transition between high-energy segments instead of a traditional cut
  or fade.
- **Scratches/dust** — an overlaid texture of scratches and specks (looped so the
  repetition doesn't show), simulates the physical wear of a film reel.
- **Gate weave / subtle jitter** — a minimal, random frame-to-frame shift and
  rotation, mimicking the mechanical instability of an old projector. This is what
  helps the most in making a still photo with zoom feel "alive" instead of a tidy
  PowerPoint slide.
- **Chromatic aberration pulses** — a subtle color-channel shift, more noticeable
  during fast motion moments (whip-pans, transitions).

All of this can be built with standard video overlays/blends (ffmpeg
`blend`/`overlay`, or the compositor in any editor) on top of a generated or
stock texture — no specific plugin needed, though dedicated film-look tools
(Dehancer and similar) give a more polished result out of the box if you already
have them. Same as with color: try the effect on 1-2 representative clips before
applying it to the 70+ clips of the final cut — very strong grain or very
aggressive jitter gets tiring fast if it's sustained through the whole video.

#### How it ended up implemented (pure ffmpeg, no plugins)

Implemented in `lib_preset.build_film_chain()` + `06_film_damage.py`, with
intensity presets in `presets/film/` (subtle / medium / strong). What we learned
building it:

**Apply it at the end, on the already-assembled video, not photo by photo.**
Three reasons, all practical: it doesn't invalidate the segment cache (here, 96
FFV1 files = 3.7 GB, you can iterate on intensity without re-rendering anything);
grain and dirt evolve continuously across the whole piece instead of resetting on
every cut (if applied per segment, the pattern repeats and it shows); and the gate
weave also moves the transitions, which is what makes it read as a single strip of
film instead of N clips each shaking on their own. It hangs off the delivery
encode in the same `ffmpeg` run, so it doesn't add an extra lossy generation.

**Gate weave**: enlarge the frame and crop inside it a window that moves
(`scale` → `rotate` → `crop` with `x`/`y` dependent on `t`). Drift it as the
**sum of two non-harmonic sine waves**, not frame-to-frame noise: pure noise
reads as a shaky camera, and a single sine wave reads as predictable mechanical
sway. The crop margin has to cover the drift **plus** what the rotation eats away
at the corners (≈ `sin(angle) × dimension/2`), or you'll get black wedges at the
edges. Automatically verifiable: pass a pure white frame through the chain and
check the global minimum stays at 255.

**Grain**: a noise layer generated separately at half resolution and scaled up
(comes out chunky, like real grain, instead of single-pixel noise), blended in
"overlay" mode against a neutral gray 128 — which modulates contrast without
shifting exposure. Generate the noise on **a single plane and replicate it** to
all three (`noise=c0s=...` → `extractplanes` → `format=gbrp`): `noise` doesn't
support `gray`, so ffmpeg converts to `gbrp` behind the scenes and an `alls=` ends
up injecting **independent noise per channel**, which isn't film grain but digital
chroma noise, and on top of that leaves each plane with a different mean, tinting
the whole video.

**Halation**: isolate highlights (`lutrgb` with a threshold) → blur → tint warm
(`colorchannelmixer`) → `blend=screen`. The blur runs at 1/4 resolution: the
result is the same diffuse blob and comes out ~16x cheaper.

**Scratches/dust**: procedurally generated texture (numpy) as streaks + specks
over black, `blend=screen` with `-stream_loop -1`. To keep the loop from showing,
events that start near the end wrap their frames modulo N. Generating it is more
controllable than pulling in a stock texture: density comes straight from the
preset.

**Light leaks**: `blend` doesn't accept time expressions in `opacity`, so the
pulse lives inside the layer itself — it's generated black (screen with black does
nothing) and only "lights up" during flash moments, using `geq` and a sine raised
to a high exponent (short, spaced-out flashes, not a constant pulse). Generate the
gradient at ~192 px wide and scale it up: `geq` is a per-pixel, per-frame
interpreter, and a smooth gradient scales without it showing.

> ⚠️ **The whole chain has to run in RGB (`gbrp`), and it has to be re-pinned after
> every `scale`.** `screen`/`overlay` blend modes are defined over color channels;
> on YUV planes they break the color (`screen(128,128)` on chroma gives 192). And a
> single `format=gbrp` at the start **isn't enough**: `scale` negotiates its own
> output format freely, and if what follows accepts YUV (`crop`, `blend`, and
> `gblur` all accept anything), it picks `yuv420p` and everything downstream runs
> in YUV **with no error or warning at all** — the symptom is an overall magenta
> shift. That same `scale` also leaves the SAR at something like `323/322` when it
> changes the pixel aspect ratio, and the video comes out stretched. Put
> `format=gbrp` **and** `setsar=1` after every `scale`.

The two bugs above (the magenta shift and the per-channel noise) don't throw an
error, don't show up in a clean log, and only show up if you look at the pixels.
That's why it's worth leaving an automatic check in place — here it's
`06_film_damage.py --selftest`, which runs a flat gray frame through every preset
and verifies it comes out gray (with the lights off there's no halation or leak,
so any separation between channels is broken plumbing), that no intermediate
`scale` returns YUV, and that the SAR stays 1:1.

**Pacing consideration**: a video with 5 static photos in a row feels slow even if
each one is brief. Interleaving "something with its own motion" between static
photos helps perceived pacing a lot more than shortening durations does.

For this phase use your encoding tool's **fast** setting (GPU acceleration if you
have it, aggressive lossy compression) — this is all for iterating on
order/pacing/selection, it's not the delivery.

### Phase 7 — Final quality pass (the slowest one, minutes to quite a bit more)

This is the only phase where **processing time isn't the priority, quality is**.
And there's an important style detail here: **if your final cut is assembled by
chaining many clips together with transitions, don't re-encode lossily at every
intermediate step**. Every generation of lossy compression (even "high quality")
degrades a bit, and if you have several stages (individual clip → batch grouping
→ final file), that loss accumulates. The fix: use a **truly lossless** codec (not
an approximation like "very light lossy compression") for all the intermediate
steps, and save the one real lossy compression pass for the final delivery export.

For the delivery export itself: a modern codec (AV1, H.265) gives you better
quality per byte than classic H.264. If the video is going to be watched on a
phone, **think about the decoder**: not every phone has hardware AV1 decoding yet,
so avoid features that make the decoder's life harder (film grain synthesis, for
example — it exists in AV1, saves bitrate but demands more at playback). A
slightly simpler file beats one that stutters on half of your friends' phones.

**On file size**: if you need a predictable size (to send over WhatsApp, for
example), aim for a **target bitrate** instead of leaving quality "loose" — with
loose quality (CRF-type) the final size is a surprise until processing finishes.

## Generic things that break (and how to avoid them, whatever your tool)

- **Parallel RAW tools sharing state.** If your batch RAW developer uses a shared
  database/config (fairly common), running several instances in parallel can
  corrupt that state and make all of them fail. If your tool lets you point each
  instance at its own temporary config/cache directory, do that. And test with a
  few parallel instances before launching all 300 at once — a smoke test with 5-10
  photos saves you from discovering the problem after the whole batch has already
  failed.
- **Over-parallelizing isn't faster.** If your RAW tool already uses multiple
  threads internally per instance, running as many parallel instances as you have
  CPU cores oversubscribes it and **performs worse**, not better. Try half your
  cores as a starting point.
- **Many clips + transitions = memory spike.** If you build transitions by
  chaining *all* the clips together in a single processing step, your tool may
  need to hold a lot of decoded streams in memory at once — with ~70 clips this
  ate close to 9GB of RAM on a 15GB machine, hitting swap. The generic fix: merge
  in small batches (8-10 clips), then merge the batches together. The memory peak
  stays bounded no matter how many clips the final video has.
- **If you merge clips in batches recursively, watch out for repeated filenames
  across levels.** If every merge "round" uses the same temp directory and the
  same names, one round can clobber another round's output with no visible error
  — the final file just comes out wrong (for us this meant a duration much
  shorter than expected, with no error message at all). Every recursion level
  needs its own namespace/temp directory.
- **If your processing has several stages with a heavy intermediate file in
  between (for example: developing the RAW into a big uncompressed TIFF, then
  applying denoise/color afterward), don't do "stage 1 for the whole batch, then
  stage 2 for the whole batch" even if each function deletes its own intermediate
  when done.** If the deletion happens at the end of stage 2, and stage 1 runs to
  completion first, the intermediate will pile up for **the entire batch** before
  a single one gets deleted — the peak disk usage is N intermediate files, not a
  few. The generic fix: process each item end to end (stage 1 → stage 2 → delete
  intermediate) before starting the next one. That way the disk peak stays bounded
  to the number of parallel workers, not the batch size. This matters more the
  heavier the intermediate is (an uncompressed 16-bit TIFF can weigh a lot more
  than the compressed final result) and the bigger the batch is — with a handful
  of test photos the problem doesn't even show up, which is exactly why it's easy
  to miss until you run the full real batch.
- **Disk fills up faster than expected.** Between proxies, full-resolution
  processed versions (which can weigh ~100MB per photo at 16-bit), and lossless
  intermediates (which weigh a lot, tens of MB per second of video), space goes
  fast. Things that help: delete intermediate files as soon as the next step is
  done using them (don't wait until "later"); check free space *before* launching
  a large batch, not after; and if a process fails mid-write (for example due to
  running out of space), it can leave an output file that *exists* but is
  corrupt/truncated — a check like "does it already exist, so it's already done?"
  isn't enough, you need to verify the process actually finished cleanly AND
  delete any partial file if it failed.
  - **Before launching a heavy step (especially full-resolution export), do the
    math:** number of photos going through that step × expected average size,
    against actual free space. Don't trust "there's some free space" — with heavy
    RAW, a batch of a few hundred photos exported at full resolution can demand
    tens of GB at once.
  - **If the working disk is also the system disk** (not one dedicated to media),
    before going out and deleting your own project files, check what general
    system "fat" has piled up — there's usually more than it looks like, and it's
    safer to touch because it's all regenerable or redundant: browser cache
    (`~/.cache/<browser>`), chat/messaging app caches that store received media
    (look for `cache`/`media_cache` folders inside their data directory), old
    crash core dumps, the system package manager's cache (`apt clean` on
    Debian/Ubuntu, equivalents on other distros), accumulated systemd journal
    logs (`journalctl --vacuum-size=<size>` — vacuuming by *time* doesn't help if
    all your logs are recent, you need to go by size there), old kernels that are
    neither the running one nor the newest one, unused Docker images
    (`docker system df` shows you how much is reclaimable before you decide).
    Every one of these categories is safe to delete without a second thought
    because it regenerates itself or can be re-downloaded — the only category
    worth asking about first is anything that's *content* belonging to someone
    else, or an already-finished result you might want to keep (previous
    renders, personal downloads), not system cache.
- **Photo rotation without reliable metadata isn't solved with more algorithm,
  it's solved with less friction for the human.** Don't waste time building
  sophisticated automatic detection for this — a chronological contact sheet and
  a list of exceptions given over chat is faster in practice.

## Architecture: separating "capture" from "process"

If this is going to happen regularly (multiple photographers, multiple events),
it's worth thinking of the pipeline as **two roles on two separate machines**, not
one:

- **Capture** (wherever the photographer is, at the event or just back from it):
  only needs to pull the card and upload the RAW somewhere. Doesn't need a GPU or
  heavy compute — any modest laptop is enough for this. The "somewhere" can be as
  simple as whatever file-transfer service you already use (cloud, messaging with
  large-file support, whatever) — at this stage the RAW is just payload, nothing
  special needed.
- **Processing** (a fixed machine, with a real GPU): runs the whole heavy pipeline
  (proxies, denoise, final export) unattended, as soon as new material arrives.
  This is the one worth equipping properly.

The advantage of this specific pipeline for that setup: **it already separates
light from heavy by design** (small proxies for review/approval vs. heavy
processing only of what's approved — see Phases 1 and 5 above). That means the
back-and-forth of approval ("cut these, keep these") can happen by sending only
the lightweight files (contact sheets, review videos with filenames burned in)
over any normal messaging channel, without ever moving the heavy RAW more than
once (the initial upload).

**On picking a GPU for the processing machine**: think about general compute
(OpenCL/CUDA/Vulkan), not just the video encoder. The encoder (NVENC and similar)
is already fairly even across nearby generations of cards — what actually changes
with a newer/more-VRAM card is how well GPU denoise filters run (RAW developers
like darktable also benefit a lot from this) and heavy editing tools like DaVinci
Resolve. An old card can flat-out fail on relatively new GPU filters (happened to
us: a Vulkan denoise filter wouldn't compile on a 2015 GPU due to a shader
compiler limit, no fix possible by adjusting parameters) — don't assume "it has a
GPU, so it'll work," test it on the actual card before planning your workflow
around it.

**Counter-intuitive, and we confirmed it by migrating machines mid-project: GPU
doesn't always win, even when it "does work."** With a much newer GPU (Ampere vs.
the 2015 Maxwell one above), the Vulkan denoise filter went from "won't compile"
to "compiles, but..." — it still had a much smaller search-window ceiling than the
CPU version (due to a compute-shader shared-memory-per-workgroup limit, not total
VRAM — more VRAM doesn't fix it), and in our usage pattern (a fresh `ffmpeg`
process per photo, not one long batch inside the same process) the fixed cost of
initializing the Vulkan device on every invocation ended up outweighing the
savings enough that **the CPU-only version was faster** than the GPU one for a
single photo. And on top of that, that same new CPU (with no GPU involved) already
turned out to be ~6x faster than the old one for this same filter, so a lot of the
expected gain from "let's get a better GPU" actually came from the new CPU that
happened to arrive in the same upgrade. Moral: measure both paths with your real
usage pattern (how many invocations, what size each) before assuming which one
wins — the answer isn't the same for "one long batch in a single process" as it is
for "many short-lived processes."

**On remote editing on the processing machine**: if you're also going to use that
machine for interactive editing at a distance (not just unattended batch work) —
tools like DaVinci Resolve depend on low visual latency for real-time
scrubbing/grading to feel good. A generic remote desktop (standard VNC/RDP) tends
to fall short there; something purpose-built for it (GPU-accelerated streaming
like Moonlight/Sunshine, or NoMachine) is worth it over just whatever works first.
For everything that's console-based (running this pipeline, running an agent/AI
that helps with the process, scripts in general) that problem doesn't exist —
plain SSH is enough, the only thing to watch there is remote-access security
itself (key-based auth, don't expose the raw port to the internet without at
least that).

## Speed levers for the final encode (use only if you're in a hurry)

Before thinking about compiling a custom `ffmpeg` for your CPU (not worth it — the
heavy loops in `libx264`/`libsvtav1`/`dav1d` are already hand-tuned assembly with
runtime dispatch per instruction set; a custom build with `-march=native` only
speeds up the "glue" code around it, typically a couple percent gain, and you lose
easy security updates via the package manager) — there are real levers with more
impact, to use **only if you're pressed for time**; if you're not, prioritize
quality/compatibility as we've been doing:

- **Hardware encoder for H264/HEVC if your GPU has one** (`h264_nvenc`/
  `hevc_nvenc` on NVIDIA, equivalents on AMD/Intel) — much faster than software
  (`libx264`), somewhat lower quality but perfectly usable for a quick delivery.
  Note: hardware AV1 only shows up on fairly new GPUs (NVIDIA 40-series onward) —
  on a slightly older GPU (Ampere, 30-series, for example) AV1 is still
  software-only no matter what, no shortcut there besides lowering the preset.
- **Software encoder preset** (`-preset` in `libx264`/`libsvtav1`) — raising it
  (faster, less compression-efficient) shrinks the time quite a bit at the cost of
  a somewhat larger file for the same visual quality. It's the simplest lever to
  touch.
- **`nlmeans` and most `ffmpeg` filters already parallelize on their own** (slice
  threading) using all available cores — there's no hidden experimental flag
  there, the hardware is already being used by default.

## Preset system (if you want this to be reusable for other events)

Split into independent categories that can be combined:

- **Look/color** — contrast, saturation, shadow/highlight tint, vignette, grain. A
  named set of numeric parameters (e.g. `"concert"`, `"wedding"`, `"birthday"`) is
  easier to adjust and reproduce than rules hardcoded into the code.
- **Pace** — how long each single photo lasts, each frame of a mini-clip,
  each transition. A "fast" preset (quick cuts) and a "slow" one (contemplative)
  cover most cases.
- **Motion** — which effect applies to each type of segment (zoom in, zoom out,
  static, some more aggressive effect like a whip-pan for high-energy
  transitions).

Store them as simple data files (JSON, YAML, whatever you prefer) separate from
the code that applies them — so adding a new preset means writing a file, not
touching logic.

## Summary of the whole path

```
1. RAW → lightweight proxies
    ↓
2. Group by burst/repeated shot
    ↓
3. Score + suggested selection + contact sheet  ←──┐
    ↓                                               │ iterate until
4. Human review (rotation, what's excess, false +/-) ┤ the selection
    ↓                                               │ closes
5. Real processing (color + noise) only on approved ─┘
    ↓
6. Assemble video in fast mode — iterate on pacing/order/selection
    ↓
7. Final quality pass: lossless intermediate + a well-thought-out compressed delivery
```

None of this is magic, it's just organizing the work well: cheap and fast for
exploring, expensive and slow only where it actually matters (what gets
delivered), and human review placed at the exact points where an algorithm is
blind — not before, not everywhere, right there.

## License

MIT — see [LICENSE](LICENSE).
