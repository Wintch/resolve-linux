# detailapp

Knowledge base: how to get specific apps and games running well — Linux first, but
Windows/Mac notes count too when relevant. Specs, best settings, known issues, fixes.

Scope still being worked out (2026-08-24) — started as a place to move non-VR content
out of the `reverb-g2` project (an unrelated HP Reverb G2 headset project on the same
machine) once it became clear that project's docs were accumulating general-purpose
Linux app/game compatibility notes that had nothing to do with VR. Not yet decided:
final structure, whether/how to publish, exact boundary with existing resources like
ProtonDB/WineHQ AppDB/PCGamingWiki (which already own general compatibility ratings —
see each app's page for what's linked out to vs. documented here directly). The angle
this repo is actually meant to add: **measured, per-app performance-settings
recommendations tied to specific hardware**, and (for games) **GPU power-limit-vs-
performance curves per title** — not "does it run", but "these settings hit X fps on
this specific GPU". Neither of those exists elsewhere at the per-title level as of this
writing (checked).

## Layout

```
apps/<app-name>.md   one file per app/game
```

## Apps documented so far

- [DaVinci Resolve](apps/davinci-resolve.md) — Linux install via makeresolvedeb, Studio
  vs. Free, NVIDIA/CUDA requirements, AI-driven control via davinci-resolve-mcp.
