# Performance guide: getting the most out of this rig's actual hardware

Asked for directly (2026-08-25): "vamos a ver si conviene darle mas ram tambien...
busca toda mejora de performance que podemos hacer con el hardware que hay. Cache en
ram? Proxy? Necesitamos la guia." This is that guide — concrete to `iashur`'s real,
measured hardware, not generic Linux performance advice. Every number here was either
measured directly this session or is cited from a source; nothing is guessed. See
README.md for the full session-by-session history this draws on (CUDA/X11 fix, RAM
upgrade, render/effect benchmarks) — this file is the actionable summary, not a new
investigation log.

**Rig**: AMD Ryzen 5 5600X (6C/12T), 31GB RAM, RTX 3060 Ti (8GB VRAM, driver 595.71.05),
Debian 13, DaVinci Resolve Studio 21.0.4.

## 1. Storage — the one big, clear win here

**This is the highest-impact finding in this guide.** Measured directly with
Blackmagic's own bundled `TestIO` disk benchmark (Direct I/O, bypasses OS cache — see
README.md's "Render-profile benchmark + bundled Blackmagic tools map" for the full
methodology and the cache-inflated numbers to ignore):

| Path | Drive | Sustained WRITE | READ | READ-WRITE |
|---|---|---|---|---|
| `/home/iam/Videos` (**current** Resolve Media Storage) | `/dev/sda`, Kingston SA400 240GB SATA SSD, DRAM-less | **46 MB/s** | 221 MB/s | 75 MB/s |
| `/mnt/videos` (NTFS-3G over the dual-boot NVMe) | `/dev/nvme0n1`, Kingston NV1 NVMe, DRAM-less | **~1,660–1,740 MB/s** | 1,600–3,580 MB/s | 2,077–2,292 MB/s |

**~35-37x faster sustained write on the NVMe path, even through NTFS-3G/FUSE
overhead.** The SATA drive's ~46MB/s isn't a bug or a fluke — it's this exact Kingston
A400 model's well-documented real-world behavior once its small DRAM-less SLC cache is
exhausted (corroborated by independent reviews, not just this rig).

**Verified this wasn't a caching artifact, per the user's own good catch**: the NVMe
numbers above came from `TestIO`'s own `useDirectIO=1` flag, but the `/mnt/videos` mount
itself is `fuseblk` (ntfs-3g) *without* the FUSE `direct_io` mount option — meaning
`O_DIRECT` requests from `TestIO` could, in principle, still be served from the kernel
page cache rather than real hardware, silently. Worth checking rather than assuming.
`sudo mount -o remount,direct_io /mnt/videos` was tried; `mount`'s own output showed no
change (FUSE filesystems generally don't support `-o remount` to add options live — a
real unmount/remount would be needed to actually test that path), but a fresh `TestIO`
run straight after landed in the same broad range as before (WRITE 1,397 MB/s here vs.
1,660–1,740 MB/s earlier; READ/READ-reverse/RANDOM READ all in the 2.5–3.6GB/s band both
times) — well below what page-cache/RAM-speed reads would show (typically 5-15+ GB/s for
a sequential in-memory copy) and squarely in plausible real-NVMe-through-FUSE territory.
**Read as: genuinely disk-backed, not RAM-cache-inflated, even though `direct_io`
specifically was never confirmed active** — the 35x SATA-vs-NVMe gap in the table above
holds up as real. Against this repo's
own "DNxHR HQ 4K ≈ 700GB/hour" estimate (needs ~194MB/s sustained to keep up), **the
current SATA-backed Media Storage path cannot sustain a real 4K DNxHR export** — a long
render is a real write-starvation risk, not just a "plan the disk space" concern.

**Done**: `/mnt/videos` is now registered as a second Media Storage volume in Resolve
(confirmed live via the scripting API — `MediaStorage.GetMountedVolumeList()` now
returns both `/home/iam/Videos` and `/mnt/videos`). This had to be done by hand in the
GUI (Preferences → Media Storage → Add) — **no scripting-API method exists to register a
new Media Storage volume**, only to read the existing list and browse/add items from
paths already registered (confirmed against the full official scripting API reference,
not just the MCP wrapper — this is a real, permanent gap, not something this session
missed). **Still to actually do**: point render output / Optimized Media / render cache
at `/mnt/videos` instead of `/home/iam/Videos` for real work going forward — registering
the volume was the blocking step, not the whole change.

**Real constraint, don't reformat anything to "fix" this — but see the partition survey
below, this is being actively considered.** The NVMe is a 1.8TB drive fully partitioned
for a **Windows dual-boot** (EFI + Windows boot + three NTFS partitions, no
free/unallocated space found via `lsblk`):

| Partition | Size | Label | Contents (as identified this session) |
|---|---|---|---|
| `nvme0n1p3` | 488GB | "500" | **Steam/Battle.net games library** (Diablo IV, SteamLibrary) — actively used, not a candidate to free |
| `nvme0n1p4` | 586GB | "600" | The drive now mounted at `/mnt/videos` and just registered as Media Storage above — in active use as of this change, not a candidate to free |
| `nvme0n1p5` | 788GB | "800" | **Unidentified** — largest of the three, so the best candidate *by size* if it turns out to be low-value/backup data, but a read-only mount attempt this session failed with a polkit "Not authorized" error rather than actually showing its contents. Needs either the user to confirm what's on it, or to authorize the mount interactively, before treating it as available. |

Reformatting any of these to a native Linux filesystem (ext4, for full O_DIRECT/no-FUSE
performance) means shrinking or fully freeing one of them first — a real, deliberate
decision about the dual-boot setup, not something to do as a side effect of a Resolve
performance tweak, and not attempted this session. Until then, the NTFS-3G mount is
already a large, real win over the SATA drive without touching any partition.

### 1a. Reliability caveat on this same NVMe drive — read before trusting it with anything irreplaceable (2026-08-25)

**This is a reliability flag, not a performance one — it doesn't change the storage-move
recommendation above, but it changes how that new Media Storage path should be used.** The
physical drive behind `/mnt/videos` (and `/mnt/win3`, `/mnt/win5`) reports as `Kingston
SNVS2000G`, firmware `S8442105` — a DRAM-less, QLC-NAND, Host-Memory-Buffer-dependent
budget NVMe drive (Kingston's NV1/NV2 family; the model number and firmware string both
point specifically to NV1, a labeling detail worked out during the research below). Full
research, sourcing, and reasoning: **`reverb-g2/docs/74-nvme-kingston-nv2-reliability.md`**
(a separate repo on this same machine — that doc is where this finding was researched, not
duplicated here).

Condensed version:

- **The user has personally experienced silent data corruption on this exact drive
  before** (no error thrown — the data just came out wrong later), and separately on
  another drive too. His own standing rule: "if it fills up, it can corrupt data."
- **Observed today**: a ~185GB sustained copy dropped from ~350MB/s to ~20MB/s partway
  through — consistent with this drive's small pseudo-SLC write cache running out and
  falling back to native QLC write speed, exactly the kind of sustained-write stress this
  drive handles worst.
- **Research found**: no single authoritative "this model corrupts data" advisory, but a
  real, credible, independent field report of a same-family Kingston drive silently losing
  its entire contents (files gone, `chkdsk` reporting 100% free, zero errors thrown) — the
  same failure shape the user describes — plus two separate, real component-swap
  controversies on this drive family (Kingston shipping different controllers/NAND under
  one unchanged model number) and the general engineering reality that DRAM-less QLC
  drives have the least margin of any common SSD class for garbage collection and
  FTL-metadata safety once they're pushed near full or under sustained heavy writes.

**Practical takeaway for this video-editing workflow specifically:**

1. **Don't let `/mnt/videos` (Media Storage) fill up near capacity.** Keep real headroom,
   not just "there's still some space left."
2. **Verify footage after every transfer onto or off of this drive** — a matching file size
   is not enough; every documented corruption case above passed that check. Use
   `rsync -av --checksum` or a `sha256sum` pass before trusting footage moved onto this
   drive for editing, especially before the source copy is deleted anywhere else.
3. **Don't treat this drive as the sole, non-backed-up copy of any irreplaceable footage.**
   It's a legitimate fast scratch/working-media volume given the performance numbers in
   section 1 above, but given this specific unit's own incident history, original camera
   footage that only exists here is a real risk, not a hypothetical one.

### 1b. Native ext4 partition on the same NVMe — real numbers, and a real TRIM lesson (2026-08-26)

The user freed `nvme0n1p3` (the second Steam library from section 1's table, 488GB) and
had it reformatted native ext4 (`pipelines/disk-benchmark/setup_test_partition.sh`,
mounted at `/mnt/resolve_test`) specifically to get a clean apples-to-apples comparison
against the root filesystem (also ext4) without NTFS-3G/FUSE in the picture, and to run
real IOPS numbers via `fio` (`pipelines/disk-benchmark/io_profile_benchmark.py`) — this
repo's `TestIO` numbers throughout are throughput/FPS, not true IOPS at small block
sizes/queue depths.

**Real gotcha hit and root-caused, worth keeping as a general lesson for this drive (or
any SSD) after a reformat**: the very first `TestIO` write test at 2GB (200MB × 10
frames) measured **324 MB/s** — a strange outlier against a 1GB test (1,658 MB/s) and an
8GB test (1,615 MB/s) run right around it, which ruled out a simple "runs out of SLC
cache past N gigabytes" theory (the 8GB test would have hit that first, and didn't).
**Root cause, confirmed not just guessed**: `mkfs.ext4` doesn't necessarily TRIM/discard
the underlying flash blocks it's given — the 2GB test happened to land on blocks that
still physically held the old Steam library's data, forcing the SSD controller into a
real erase-before-write cycle (slow) instead of a fast write to already-erased space. Ran
`sudo fstrim -v /mnt/resolve_test` (479.5GiB trimmed) and re-ran the exact same 2GB test:
**1,649 MB/s** — matches the other two runs. **Practical lesson: always `fstrim` a
freshly formatted partition before trusting any write-performance number on it**,
especially one carved out of a drive that previously held real data — this isn't
specific to this Kingston drive, it's a general SSD/TRIM characteristic, just one that
would have produced a confusing, wrong "this partition is slow" conclusion here if not
chased down. **This is a one-time write-path anomaly, unrelated to the section 1a
reliability caveat** (silent corruption risk) — don't conflate the two; TRIM explains
this specific number, it doesn't change the drive's separate, real reliability history.

**Full storage comparison, all three paths, same rig:**

| Path | Filesystem | Sustained WRITE (Direct I/O) | READ |
|---|---|---|---|
| `/home/iam/Videos` | ext4, SATA (Kingston A400) | 46 MB/s | 221 MB/s |
| `/mnt/videos` | NTFS-3G, NVMe (Kingston NV1) | ~1,660–1,740 MB/s | 1,600–3,580 MB/s |
| `/mnt/resolve_test` | **native ext4**, same NVMe, post-TRIM | **1,649 MB/s** | 2,138–2,150 MB/s |

**Filesystem barely matters here — NTFS-3G's overhead is real but small next to the
SATA-vs-NVMe gap.** ext4 read throughput is modestly higher across the board (~2.1-2.2
vs ~1.6-3.6GB/s, noisy on both), and write is roughly a wash once the NTFS-3G mount's
own numbers are compared post-hoc — the SATA-vs-NVMe hardware difference (35x+) dwarfs
the filesystem-overhead difference (nowhere near that scale). This mildly weakens the
case for actually shrinking the Windows dual-boot to get a native partition just for
performance — `/mnt/videos` (NTFS-3G, no partition surgery needed) already captures
nearly all of the real, available win.

**`fio` IOPS/latency, two synthetic profiles on the ext4 partition (post-TRIM)** — see
that script's docstring for exact parameters:

| Profile | Pattern | Read IOPS | Read BW | Read latency (avg) | Write IOPS | Write BW |
|---|---|---|---|---|---|---|
| `editing` | sequential, 4MB blocks, 70/30 mixed R/W, queue depth 4 | 280 | 1,122 MB/s | 8.3ms | 127 | 510 MB/s |
| `gaming` | random read, 4KB blocks, queue depth 32 × 4 jobs | **198,069** | 774 MB/s | **0.65ms** | — | — |

**This NVMe handles small random reads (the gaming-shaped profile) very well** — high
IOPS, sub-millisecond latency, the shape a DRAM-less NVMe with decent controller
parallelism is generally good at. The editing-shaped profile's lower raw bandwidth here
(1,122 MB/s read) vs. the pure-sequential `TestIO` numbers above (2,138-2,150 MB/s) isn't
a contradiction — it's a *mixed* 70/30 read+write workload on one file with a shallower
queue depth (4, not 32), a different and more realistic shape than a single-direction
pure-sequential pass.

**Third profile added, and run against both NVMe paths for a direct comparison — this is
what actually answers the CinemaDNG/many-small-files question raised earlier in this
session**: `manyfiles` (500 files × 4MB, 256K reads, queue depth 16 × 4 jobs) simulates
opening/reading many discrete files — the real shape of a CinemaDNG image sequence or
game-asset loading, as opposed to `gaming`'s random offsets *within* one large file. Ran
on both `/mnt/resolve_test` (ext4) and `/mnt/videos` (NTFS-3G), same 2G size both times:

| Profile | Metric | ext4 (`/mnt/resolve_test`) | NTFS-3G (`/mnt/videos`) |
|---|---|---|---|
| `editing` | read | 1,122 MB/s, 280 IOPS, 8.3ms avg | **1,812 MB/s**, 453 IOPS, 5.8ms avg |
| `editing` | write | 510 MB/s, 127 IOPS, 13.0ms avg | **824 MB/s**, 206 IOPS, 6.5ms avg |
| `gaming` | read | **774 MB/s, 198,069 IOPS**, 0.65ms avg | 698 MB/s, 178,740 IOPS, 0.72ms avg, 1.06ms p99 |
| `manyfiles` | read | 1,681 MB/s, 6,724 IOPS, 9.5ms avg, 11.1ms p99 | **3,338 MB/s, 13,350 IOPS**, 4.8ms avg, 7.1ms p99 |

**Genuinely surprising result, reported as measured rather than smoothed over**: NTFS-3G
beat native ext4 on the *same physical NVMe* for `editing` and `manyfiles` — by a lot on
`manyfiles` (~2x the IOPS/bandwidth). Only `gaming` (pure random 4K reads) came out
roughly even, with ext4 slightly ahead. This runs against the naive expectation that a
FUSE filesystem should always lose to a native one. Two re-runs (1G and 2G sizes) on the
NTFS-3G side gave consistent numbers, so this isn't a one-off fluke.

**Update, resolved later the same session — see 1d below**: the leading suspicion here
was that `ntfs-3g`'s `fuseblk` mount doesn't genuinely honor `O_DIRECT` and these numbers
were secretly cache-inflated. **A decisive follow-up test ruled that out**: a 40GB
sequential write+read (>RAM, can't be cache) against `/mnt/videos` sustained
1,152 MB/s write / 1,313 MB/s read — real, disk-backed throughput. So NTFS-3G's speed
advantage on `editing`/`manyfiles` above is **genuinely real, not a caching artifact** —
what actually explains the gap is still open (ext4/XFS block-allocation behavior,
`big_writes`, or something else), just not "it wasn't really measuring disk."

**Practically, this doesn't change the recommendation**: whichever of the two NVMe paths
is marginally faster, both stay 15-70x ahead of the SATA drive across every profile
tested, which is the actual decision that matters (see section 1's table). It does mean
**don't assume the extra step of getting a native partition on this NVMe buys real
speed over just using `/mnt/videos` as-is** — on this evidence, it might not, and could
even be slightly slower for some access patterns.

**Still not tested**: the `gaming`/`manyfiles` profiles against a real game partition
(not this freed-up one), or a genuinely large (tens-of-GB) sustained write to see
whether this drive's SLC cache has a real cliff at some larger volume than tested here —
the 8GB test above didn't find one, but didn't rule one out at, say, 30-50GB either.

### 1c. Same partition, reformatted XFS — three-way filesystem comparison (2026-08-26)

Same `nvme0n1p3`, reformatted from ext4 to XFS
(`pipelines/disk-benchmark/reformat_test_partition.sh`) purely to compare filesystems on
identical hardware. **`mkfs.xfs` discards/trims the device by default** ("Discarding
blocks...Done" in its own output) — unlike `mkfs.ext4` in section 1b, no separate
`fstrim` was needed to avoid the untrimmed-block write-speed artifact (the script runs
one anyway, as a safety net, but it wasn't the fix this time). `TestIO` write throughput
matched ext4's post-TRIM numbers closely (1,642-1,718 MB/s vs. ext4's 1,649-1,658 MB/s)
— no untrimmed-block surprise on the XFS pass, consistent with that root cause.

**Full three-way `fio` comparison, same 2G size, same partition/hardware, only the
filesystem changed:**

| Profile | Metric | ext4 | XFS | NTFS-3G (`/mnt/videos`) |
|---|---|---|---|---|
| `editing` | read | 1,122 MB/s, 280 IOPS, 8.3ms avg | 1,298 MB/s, 324 IOPS, 7.1ms avg, 12.1ms p99 | **1,812 MB/s**, 453 IOPS, 5.8ms avg, 6.7ms p99 |
| `editing` | write | 510 MB/s, 127 IOPS, 13.0ms avg | 590 MB/s, 147 IOPS, 11.4ms avg, **26.6ms p99** | **824 MB/s**, 206 IOPS, 6.5ms avg, 7.3ms p99 |
| `gaming` | read | 774 MB/s, 198,069 IOPS, 0.65ms avg | **860 MB/s, 220,058 IOPS**, 0.58ms avg, 0.95ms p99 | 698 MB/s, 178,740 IOPS, 0.72ms avg, 1.06ms p99 |
| `manyfiles` | read | 1,681 MB/s, 6,724 IOPS, 9.5ms avg, 11.1ms p99 | 1,661 MB/s, 6,643 IOPS, 9.6ms avg, 10.9ms p99 | **3,338 MB/s, 13,350 IOPS**, 4.8ms avg, 7.1ms p99 |

**Three real takeaways from this pass:**

1. **XFS edges out ext4 on the `gaming` (random 4K read) profile** — modestly higher
   IOPS/bandwidth, lower average and p99 latency. Small but consistent with XFS's
   general reputation for better small-file/metadata-heavy random I/O than ext4.
2. **XFS's write-latency tail on the `editing` profile is real and worth flagging**:
   26.6ms p99 write latency vs. ext4's untracked-but-likely-lower equivalent (p99 wasn't
   captured on the ext4 `editing` run, only added to the script afterward) — a ~2x gap
   over XFS's own 11.4ms *average*. Worth knowing if XFS is ever considered for a
   heavy-write render/cache target: occasional slower write stalls are plausible, even
   if throughput looks fine on average. Not yet root-caused (could be XFS's allocation
   group behavior on a mixed-RW workload at shallow queue depth) — flagging the
   symptom, not claiming the cause.
3. **`manyfiles` result holds across BOTH native filesystems**: ext4 and XFS land within
   2% of each other (1,681 vs. 1,661 MB/s) — two independent native filesystems agreeing
   closely, both genuinely beaten by NTFS-3G's 3,338 MB/s on this same access pattern.
   At the time this was written the leading theory was that NTFS-3G's number was a
   measurement artifact (`O_DIRECT` not honored) rather than real — **section 1d below
   ruled that out with a decisive >RAM-sized test**. So this is a real result: on this
   rig, for many-small-files access specifically, the NTFS-3G mount is genuinely faster
   than either native Linux filesystem tried. Not root-caused further (why NTFS-3G's
   driver/mount options would beat ext4/XFS here specifically), but no longer in doubt
   that it's real.

**Bottom line, storage filesystem choice**: for this rig's actual editing workload, none
of these differences come close to the SATA-vs-NVMe gap that actually matters (section
1). If choosing between ext4 and XFS specifically for a future native partition on this
drive: XFS's small edge on random reads is nice, but its write-latency tail is a real
consideration for a render-cache target — **ext4 remains the simpler, safer default
recommendation** absent a specific reason to prefer XFS. **Practically, this strengthens
rather than weakens section 1's actual recommendation**: `/mnt/videos` (NTFS-3G, already
in place, no partition surgery) isn't just "good enough" next to a native partition —
for the many-small-files access pattern specifically, it measured genuinely faster than
either native filesystem tried. No reason found this session to convert real disk space
away from the Windows dual-boot just to gain a native filesystem.

### 1d. Decisive test: is NTFS-3G's speed real, or cache? (2026-08-26)

Section 1c left one real open question: is `ntfs-3g`'s apparent speed advantage genuine,
or a caching artifact from `O_DIRECT` silently not being honored by the FUSE layer? A
same-process/same-tool test can't rule this out no matter how many times it's repeated,
since a bigger test that still fits in a cache just looks equally "fast." **The one test
that actually settles it: a dataset larger than total system RAM (31GB) — that cannot
be fully cache-inflated regardless of what layer is or isn't honoring `O_DIRECT`.**

Ran a plain 40GB sequential write, then a 40GB sequential read (fresh `fio` runs,
`--direct=1`, 4MB blocks) against `/mnt/videos`:

| Pass | Size | Time | Sustained throughput |
|---|---|---|---|
| WRITE | 40GB | 35.6s | **1,151.9 MB/s** |
| READ | 40GB | 31.2s | **1,313.2 MB/s** |

**Both held sustained, real throughput well above what could possibly be RAM-cache
(40GB can't fit in 31GB of RAM, full stop).** This resolves section 1c's open question:
**NTFS-3G's speed on this rig is genuinely real, not a measurement artifact.** It also
gives a cleaner, larger-scale real-world number than the smaller 1-8GB tests in section
1: ~1.15-1.3GB/s sustained at 40GB, in the same broad range as those smaller tests
(1,660-1,740 MB/s) — a little lower, plausibly the drive's real SLC-cache-tapering
starting to show at this larger volume, but nowhere near the ~20MB/s post-cliff
collapse the section 1a reliability doc describes for a much larger (185GB) transfer.
**Still open**: exactly where between 40GB and 185GB the real cliff sits, if there is
a single sharp one rather than a gradual taper — not tested this session, and not
urgent given no real workflow here approaches that volume in one sustained write.

## 2. RAM: is 31GB enough, or worth going further?

**Conditional, not urgent.** General Resolve benchmarking shows 32GB→64GB buys only
~2.5% overall performance, and GPU-driven color/AI-tool work shows essentially no
difference between the two ([codeitbro.com system-requirements
writeup](https://www.codeitbro.com/blog/davinci-resolve-system-requirements)). **The one
real exception is Fusion**: Puget Systems' testing found going from 16GB to 64GB is a
**31% performance difference specifically in Fusion comp-heavy work**. This rig's actual
usage so far (Film Grain, Noise Reduction, Analog Damage — all Color-page OFX, no real
Fusion compositing) doesn't hit that case. **Verdict: 31GB is adequate for the
cut/color/grading workflow validated so far. Only worth pushing further if Fusion
compositing becomes a real, regular part of the workflow** — revisit then, not
preemptively.

## 3. RAM-based cache (tmpfs) for Resolve's own cache

Resolve's cache/optimized-media location is a **per-project, GUI-only setting**
(Project Settings → General Options) — no scripting-API `SetSetting` key for it was
found in the bundled MCP's full API surface, so this can't be automated/scripted, only
set by hand per project.

A `tmpfs` (RAM disk) for this is plausible in principle — `/mnt/vrtmp` (20GB tmpfs,
belongs to the unrelated `reverb-g2` project, **do not touch or reuse it**) already
proves tmpfs works fine as a pattern on this box. **Not tried for Resolve yet.** Real
trade-off before doing it: cache content is lost on crash/reboot/power loss (fine for
disposable render/preview cache, not fine if anything ever gets pointed at it that
shouldn't be — keep it strictly to cache, never project files), and it eats into the
same 31GB this rig only recently got to the official minimum with, competing with
Resolve itself and zram. Given section 1's storage fix is a much bigger, already-measured
win with no such trade-off, **try the NVMe storage move first and re-evaluate whether a
tmpfs cache is even still worth the RAM trade-off afterward.**

## 4. Proxy / Optimized Media workflow

Not deeply tested this session (flagged as a real follow-up, not verified with numbers
the way storage was). Given this rig's actual bottlenecks — weak SATA sustained write,
8GB VRAM under the official AI-tools/background-render minimums, and CPU carrying
decode load even on a simple timeline (5% GPU / 127-156% CPU measured earlier for a
single 1080p clip, no effects) — Optimized Media at a lower resolution should help
playback/scrub smoothness on heavier timelines without help from a GPU/storage upgrade.
**Generate it to `/mnt/videos` (the NVMe mount), not `/home/iam/Videos`** — same
storage-speed logic as section 1, and keeps proxy churn off the weak SATA drive
entirely.

## 5. VRAM / GPU tuning

Not deeply verified this pass — flagged as a real follow-up given the confirmed 8GB
VRAM shortfall vs. official minimums (16GB for AI tools, 12GB for background render).
Known general levers, **not yet confirmed against this specific rig**: Timeline/Playback
proxy-resolution settings, disabling live AI-preview overlay caching, and Optimized
Media at reduced resolution all reduce VRAM pressure in principle. Worth a dedicated
pass once a real VRAM-pressure symptom actually shows up (e.g. an AI Extras tool
failing or degrading) rather than tuning preemptively against a spec-sheet number alone.

## 6. zram / swap

Checked, **no misconfiguration found, nothing recommended to change**:
- `zram0`: 15.6GB (50% of RAM, `lz4`, priority 100) — Debian's standard `zramswap`
  default (`/etc/default/zramswap`, `PERCENT=50`).
- LVM swap: 11.3GB, priority -2 (used only after zram fills).
- `swappiness=60` — Debian default, unchanged.
- Both at 0 used when checked (idle, not under load at the time) — doesn't confirm or
  deny the earlier session's "swap climbing under the lightest possible playback
  timeline" finding from before the RAM upgrade; that was flagged as worth re-checking
  under a heavier timeline, still genuinely open, unrelated to zram/swap *configuration*
  being fine.

## 7. CPU / PCIe

CPU governor confirmed `performance` at check time (this rig toggles it via
`pipelines/resolve-power/resolve_power.py` around actual Resolve sessions — see
README.md). GPU confirmed running at full PCIe Gen3 x16 link, no downclocking. NVMe PCIe
link speed **not verified** — `lspci -vv` needs root, not available this pass.

## Bottom line

**Do the storage move (section 1) first** — it's the largest, clearest, already-measured
win, and it's a Resolve settings change, not a system reconfiguration. Everything else
here is either already fine as configured (zram/swap), conditional on a workflow this
rig doesn't currently have (RAM/Fusion), or a real follow-up that needs either root
access or hands-on GUI verification to turn into a confirmed number instead of a
plausible lever (proxy workflow, VRAM tuning, PCIe link speed).
