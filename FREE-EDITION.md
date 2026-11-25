# DaVinci Resolve Free on Linux

The rest of this repo is measured on **Studio** (with its USB key). This page is the same ground for the **free edition**: what it cannot do on Linux, how to get footage in, how to get a file out that is not enormous, and how to hand a project to a Studio copy. Each claim says where it comes from: **table** (Blackmagic's Linux codec table, as quoted in [Codecs on Linux](README.md#codecs-on-linux)), **measured** (a run on the reference machine below or on a similar one, with ffmpeg and a test clip) or **not verified** (stated so, not guessed).

Reference for the new numbers: RTX 3060 Ti 8 GB, Ryzen 5 5600X, Debian 13, free Resolve 21.2 (`makeresolvedeb`), the same free build running in a Docker container with the GPU (a browser desktop). It opened its Welcome window and imported converted media there; nothing was timed inside Resolve itself.

## What the free edition does not do on Linux
| Thing | Result | Source |
|---|---|---|
| Decode H.264 or H.265/HEVC | **not at all**: Studio and an NVIDIA GPU only, no software fallback. A phone or game recording is "media offline" | table; seen on a real H.264 file |
| Decode AAC audio | not in any edition: the clip imports silent and the log fills with `Failed to decode the audio samples` | table, measured |
| Variable frame rate, interlaced footage | import but misbehave (drift, combing): fix them first | community experience, `davincirecode10.sh` thread |
| Render to H.264 or H.265 | not offered (those encoders need Studio and CUDA detected) | table, [GPU detection](README.md#gpu-detection-x11-vs-wayland) |
| External scripting API / the MCP servers | the scripting the rest of this repo drives is a Studio feature: expect to work by hand in the free edition | **not verified here** (no free instance was driven through the API) |
| Remote or dongle-free multi-seat licensing | none: the free edition needs no key, the Studio USB key belongs to one machine and is never put in a shared image | licence |

What it does read: **DNxHR/DNxHD and ProRes** video with **PCM** sound (table; DNxHR measured end to end, see below). AC3 audio decodes. AV1 is listed for decode on any edition (**not verified** here).

## Getting footage in
Make a file Resolve reads: DNxHR video, PCM sound, constant frame rate, progressive.
- One file, the whole decision made for you: [`pipelines/prepare-for-resolve`](pipelines/prepare-for-resolve/) with **`--edition free`** (a folder in, a folder of `.mov` out; the default edition is `studio`, which leaves H.264/H.265/AV1 alone).
- By hand, one file: `ffmpeg -hwaccel cuda -i in.mp4 -c:v dnxhd -profile:v dnxhr_sq -pix_fmt yuv422p -c:a pcm_s24le out.mov` (the card decodes the source; the DNxHR encode itself always runs on the CPU, there is no GPU encoder for it).
- Measured: a 1080p60 H.264 source decodes on the card at about four times real time; a folder of three phone clips converted without a problem.
- Resolve only sees media inside a registered **Media Storage** location. Put the converted folder under it (or add its parent in Preferences, Media Storage).

## Getting a file out without enormous files
**DNxHR is a fixed bitrate for each tier**, so its default tier (HQ) is huge. Measured sizes for 10 s of 1080p60 (a synthetic, busy test pattern: ProRes and the H.26x encoders vary with the picture, DNxHR does not):

| Codec | 10 s | per hour |
|---|---|---|
| DNxHR HQ | 550 MB | 198 GB |
| DNxHR SQ | 363 MB | 131 GB |
| ProRes 422 HQ | 195 MB | 70 GB |
| ProRes 422 | 145 MB | 52 GB |
| DNxHR LB | 113 MB | 41 GB |
| ProRes 422 LT | 112 MB | 40 GB |
| ProRes 422 Proxy | 55 MB | 20 GB |
| H.265 NVENC (cq 23), a delivery file | 20 MB | 7 GB |
| H.264 NVENC (cq 21), a delivery file | 30 MB | 11 GB |

Practical route on the free edition: in the Deliver page choose the **smallest intermediate the codec list offers** (DNxHR LB or SQ; ProRes Proxy or LT if the list shows ProRes: that Studio renders ProRes on Linux is confirmed in this repo, that the free edition offers it for *encoding* is **not verified**), then make the file that goes out with ffmpeg on the card and delete the intermediate:

```bash
ffmpeg -i export.mov -c:v hevc_nvenc -preset p5 -cq 23 -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart final.mp4
```
Tell the render the intermediate's size before starting: four hours of DNxHR HQ is most of a terabyte, and a budget SATA SSD will not sustain it ([PERFORMANCE.md](PERFORMANCE.md)).

## Handing a project to the paid copy
- The unit is the **`.drp` project archive**: Project Manager, right click the project, Export Project; Import Project on the other side. It carries the timeline and settings, not the media.
- A project made in the free edition opens in Studio (Studio only adds features). One made in Studio opens in the free edition but drops what only Studio has (noise reduction, Magic Mask and the other Studio-only effects, H.264/H.265 media).
- Media must be reachable at the **same path** on both machines, or relinked; the converted DNxHR files are what both sides can read. If the paid copy will use the original H.264 media, relink to it there (Studio decodes it).
- Two installations sharing live through a network project database is Studio's collaboration feature and is not covered here. A second person takes their own copy of the same `.drp` and the same intermediates.
- **Not verified here:** a round trip of a real project between the two editions; this describes how the formats are documented to behave.

## Proxy first, final pass on Studio
A workable split: whoever uses the free edition edits on **proxies** made at the start (`prepare-for-resolve --edition free --dnxhr-profile dnxhr_lb`, 41 GB per hour of 1080p60; ProRes Proxy at 20 GB if the free edition's Deliver page offers it) and hands over only the `.drp`. The Studio copy relinks the project to the original media, which it decodes, and does the grade, the effects and the delivery render. Keep the file names the same as the originals (the folder mode keeps each stem; only the extension changes) so the relink can match by name. **Not verified:** how Relink Clips treats the changed extension, and Resolve's own Proxy Mode in the free edition; test both once before depending on it.

## Download
[`pipelines/resolve-update/download_resolve.sh --free`](pipelines/resolve-update/download_resolve.sh) resolved the free 21.2 release when tried, then the vendor's site answered **403** to the request for the download link, so the free installer was downloaded by hand from the vendor's page. Re-run `--list` and `--dry-run` before relying on it.

## Related
[README: Codecs on Linux](README.md#codecs-on-linux), [Known limitations](README.md#known-limitations-on-linux), [`prepare-for-resolve`](pipelines/prepare-for-resolve/).
