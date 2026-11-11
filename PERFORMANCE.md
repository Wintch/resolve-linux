# Performance guide: Resolve on a mid-range Linux workstation

What actually moves the needle for DaVinci Resolve Studio on Linux, measured on one
reference machine rather than taken from generic tuning advice. Every number below was
measured on that machine or is cited from a source.

**Reference machine**: AMD Ryzen 5 5600X (6C/12T), 32 GB RAM, RTX 3060 Ti (8 GB VRAM),
Debian 13, DaVinci Resolve Studio 21. Two drives: a DRAM-less SATA SSD (Kingston A400
class) and a DRAM-less QLC NVMe (Kingston NV1/NV2 class).

## 1. Storage: the one big, clear win

Storage speed for Media Storage, render output, Optimized Media and the render cache is
the highest-impact setting on a machine like this. Measured with Blackmagic's bundled
`TestIO` disk benchmark (Direct I/O, which bypasses the OS page cache):

| Drive | Filesystem | Sustained WRITE | READ | READ-WRITE |
|---|---|---|---|---|
| SATA SSD, DRAM-less | ext4 | **46 MB/s** | 221 MB/s | 75 MB/s |
| NVMe, DRAM-less QLC | NTFS-3G (dual-boot partition) | **~1,660–1,740 MB/s** | 1,600–3,580 MB/s | 2,077–2,292 MB/s |
| NVMe, same drive | native ext4, after `fstrim` | **1,649 MB/s** | 2,138–2,150 MB/s | — |

**~35x faster sustained write on the NVMe, even through NTFS-3G/FUSE.** The SATA drive's
46 MB/s is the documented behaviour of DRAM-less SATA SSDs once their small SLC cache is
exhausted. A 4K DNxHR HQ export needs roughly 194 MB/s sustained (about 700 GB/hour),
so **a DRAM-less SATA SSD cannot sustain a real 4K DNxHR export**: a long render on it is
write-starved, not just short on space.

**What to do:**

1. Register a fast volume in Resolve: Preferences → Media Storage → Add. **There is no
   scripting-API method to register a Media Storage volume**; the API can only list the
   registered ones (`MediaStorage.GetMountedVolumeList()`) and browse paths inside them.
   This is a one-time GUI step.
2. Point render output, Optimized Media and the render cache at it. Render output must be
   inside a registered Media Storage volume anyway (see
   [README.md](README.md#known-limitations-on-linux)).

### Filesystem comparison on the same NVMe

The same NVMe partition was reformatted in turn as ext4, XFS and btrfs and compared with
the existing NTFS-3G partition, using `TestIO` for throughput and `fio` for IOPS and
latency. Three `fio` profiles, all `--direct=1`, 2 GB:

- `editing`: sequential, 4 MB blocks, 70/30 mixed read/write, queue depth 4.
- `gaming`: random reads, 4 KB blocks, queue depth 32 × 4 jobs (random offsets inside one file).
- `manyfiles`: 500 files × 4 MB, 256 KB reads, queue depth 16 × 4 jobs (the shape of a
  CinemaDNG image sequence).

| Profile | Metric | ext4 | XFS | NTFS-3G |
|---|---|---|---|---|
| `editing` | read | 1,122 MB/s, 280 IOPS, 8.3 ms avg | 1,298 MB/s, 324 IOPS, 7.1 ms avg | **1,812 MB/s**, 453 IOPS, 5.8 ms avg |
| `editing` | write | 510 MB/s, 127 IOPS, 13.0 ms avg | 590 MB/s, 147 IOPS, 11.4 ms avg, **26.6 ms p99** | **824 MB/s**, 206 IOPS, 6.5 ms avg |
| `gaming` | read | 774 MB/s, 198,069 IOPS, 0.65 ms avg | **860 MB/s, 220,058 IOPS**, 0.58 ms avg | 698 MB/s, 178,740 IOPS, 0.72 ms avg |
| `manyfiles` | read | 1,681 MB/s, 6,724 IOPS, 9.5 ms avg | 1,661 MB/s, 6,643 IOPS, 9.6 ms avg | **3,338 MB/s, 13,350 IOPS**, 4.8 ms avg |

btrfs (default mount, `ssd` and `discard=async` auto-set):

| Test | ext4 / XFS | btrfs |
|---|---|---|
| `TestIO` WRITE, 1–2 GB | ~1,640–1,720 MB/s | **167–222 MB/s** |
| `fio` pure sequential write, iodepth 4 | — | 810 MB/s |
| `fio` `editing` write | 510–590 MB/s | 498 MB/s |
| `fio` `gaming` / `manyfiles` reads | — | in line with ext4/XFS |

**Findings:**

- **The filesystem matters far less than SATA vs NVMe.** All three native filesystems and
  NTFS-3G stay 15–70x ahead of the SATA drive on every profile.
- **NTFS-3G is genuinely fast, not cache-inflated.** It beat both native filesystems on
  `editing` and `manyfiles`. A FUSE mount without `direct_io` might serve `O_DIRECT` from
  the page cache, so a test larger than RAM settled it: a 40 GB sequential `fio` write and
  read (on a 32 GB machine) sustained **1,152 MB/s write and 1,313 MB/s read**. Why
  NTFS-3G wins on these patterns is not root-caused. In practice, an existing NTFS
  dual-boot partition on an NVMe is already a good Media Storage volume; repartitioning
  for a native filesystem buys no measurable speed.
- **XFS** is slightly better than ext4 on random 4K reads, but showed a write-latency tail
  (26.6 ms p99 against an 11.4 ms average) on the mixed profile, which matters for a
  render-cache target.
- **btrfs pays a large, inconsistent cost on sustained sequential writes**; reads are
  unaffected. Its data checksumming would detect silent corruption, which is a real
  argument for it, but `nodatacow` (`chattr +C`) is not a clean fix: `TestIO` write stayed
  at 185 MB/s, `fio` mixed read-write improved (about 1,100 → 1,763 MB/s) and `fio` pure
  sequential write got worse (810 → 419 MB/s). Not the pick for a write-heavy
  render/export/cache path when performance is the priority.
- **Default recommendation for a native partition: ext4.** It is the only native
  filesystem tested that never showed a write cliff.

### Always `fstrim` a freshly formatted partition before benchmarking it

`mkfs.ext4` does not necessarily discard the blocks it is given. On a partition that
previously held data, the first 2 GB `TestIO` write measured **324 MB/s** against
1,658 MB/s (1 GB) and 1,615 MB/s (8 GB) runs around it: the test landed on blocks that
still held old data, forcing erase-before-write. After `sudo fstrim -v <mount>` the same
test gave **1,649 MB/s**. `mkfs.xfs` discards by default ("Discarding blocks...Done"), so
XFS did not show the anomaly. This is a general SSD behaviour, not specific to one model.

### Reliability caveat for DRAM-less QLC NVMe drives

This is a reliability flag, not a performance one: it does not change the recommendation
above, but it changes how the fast volume should be used. Budget DRAM-less QLC drives that
depend on the Host Memory Buffer (Kingston NV1/NV2 class) have the least margin of any
common SSD class for garbage collection and FTL metadata when nearly full or under long
sustained writes. Credible field reports exist of drives in this family silently losing
data, and the family has had controller/NAND swaps under unchanged model numbers. On the
reference drive, a ~185 GB sustained copy dropped from ~350 MB/s to ~20 MB/s partway
through, which is the pseudo-SLC cache running out and falling back to native QLC speed
(40 GB writes still sustained ~1.15 GB/s).

Practical rules for a video workflow on such a drive:

1. **Keep real headroom**; do not let the volume fill up.
2. **Verify footage after every transfer** onto or off the drive with
   `rsync -av --checksum` or a `sha256sum` pass, not a file-size match, before deleting
   the source anywhere else.
3. **Never keep the only copy of irreplaceable footage on it.** Use it as fast working
   and scratch storage.

## 2. RAM: is 32 GB enough?

**Conditional, not urgent.** General Resolve benchmarking shows 32 GB → 64 GB buys only
about 2.5% overall, and GPU-driven colour and AI work shows essentially no difference
([codeitbro.com system-requirements
writeup](https://www.codeitbro.com/blog/davinci-resolve-system-requirements)). **The
exception is Fusion**: Puget Systems measured a 31% difference between 16 GB and 64 GB in
comp-heavy Fusion work. 32 GB is adequate for cut, colour and Color-page OFX (Film Grain,
Noise Reduction, Analog Damage); go further only if Fusion compositing becomes regular
work. 32 GB is also Blackmagic's official minimum; at 16 GB the machine swapped under the
lightest playback timeline.

## 3. RAM-based cache (tmpfs)

Resolve's cache and Optimized Media locations are **per-project, GUI-only settings**
(Project Settings → General Options); no scripting-API `SetSetting` key exists for them.
A `tmpfs` for the render cache is plausible but untested here. Trade-offs: the cache is
lost on crash, reboot or power loss (fine for disposable cache, never for project files),
and it takes RAM away from Resolve itself and from zram. Move the cache to the fast NVMe
first; that is a larger, measured win with no such cost.

## 4. Proxy / Optimized Media

On a machine with weak storage, 8 GB VRAM and CPU-side decode (a single 1080p clip with
no effects measured 5% GPU and 127–156% CPU during playback), Optimized Media at a lower
resolution should smooth playback and scrubbing on heavier timelines. **Generate it on the
fast NVMe volume**, never on a DRAM-less SATA drive. Not benchmarked with numbers.

## 5. VRAM / GPU

8 GB is below the official minimums (16 GB for the AI tools, 12 GB for background
render). General levers, not verified with numbers on the reference machine: playback
proxy resolution, disabling live AI-preview caching, and reduced-resolution Optimized
Media. Other GPU processes sharing the card (containers, inference servers) eat into the
same 8 GB: GPU memory exhaustion makes media import and AI calls fail (see
[SCRIPTING-API-21.md](SCRIPTING-API-21.md)).

## 6. zram / swap

Debian's defaults are fine; nothing to change: `zramswap` at 50% of RAM (`lz4`,
priority 100), disk swap at a lower priority, `swappiness=60`.

## 7. CPU / PCIe

Run the CPU governor at `performance` and the GPU at its full power limit while Resolve
is working; [pipelines/resolve-power/](pipelines/resolve-power/) does this only while a
Resolve process runs. Check that the GPU links at full PCIe width and generation
(`nvidia-smi -q | grep -A3 'PCI'`, or `sudo lspci -vv`).

## Bottom line

**Move Media Storage, render output and caches to the NVMe first.** It is the largest
measured win and a Resolve settings change, not a system reconfiguration. zram/swap need
nothing; more RAM pays only for heavy Fusion; proxies and VRAM tuning are worth a pass
once a real symptom shows up.
