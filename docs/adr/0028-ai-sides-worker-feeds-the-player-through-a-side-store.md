# 0028. AI sides: a worker feeds the player through a side store

- **Status:** Proposed. Phase 2's smoke on the screens passed (2026-10-13); acceptance waits for the user's OK on what the viewer sees (ADR 0017).
- **Date:** 2026-10-13
- **Refines:** [0008](0008-picture-sizing-no-crop-surround-optional-crop-preset.md), [0014](0014-architecture-plan-ahead-into-a-cue-sheet-perform-live.md), [0024](0024-film-analysis-worker-and-content-model-shape.md)

## Context

The AI surround fills the leftover area with sides made by AI, at film speed on this laptop ([live path plan](../../plans/261013-0718-ai-surround-live-path/plan.md)). The chosen architecture is a compositor ([synthesis](../../plans/reports/research-261013-1049-ai-surround-real-time-architecture-synthesis.md)):
- most side frames are rendered from structure: plates, the camera's motion and real pixels;
- generation runs only where the picture changed something.

Phase 2 builds the skeleton the later phases add tiers to. It holds the choices that are hard to change: how the AI work reaches the player, in what coordinates, and when what is on screen may change. They were built and checked on the real player and episode ([report](../../plans/reports/implementation-261013-2012-live-skeleton-phase2.md), [code review](../../plans/reports/code-reviewer-261013-2012-live-skeleton-phase2.md)).

## Decision

### What you see (protected by [ADR 0017](0017-agents-may-change-how-not-what-you-see.md))

- Playback starts at once. The leftover area shows the glow until an AI picture is ready, and the player never waits for one.
- A shot's sides are chosen when the shot starts (a cut, a seek, the film start) and kept for the whole shot. A better tier shows from the next cut on, never in the middle of a shot.
- The AI picture sits under the real picture: the real picture is never covered or changed. Where the AI picture does not reach, the glow fills the rest.
- The side area follows the room: the player works out how far the leftover area reaches beyond the picture on each side (on Setup A's default room, about 0.43 of the picture's width on the left, 0.78 on the right, and a strip below it on the external), and the AI fills that.
- For now, the sides are stored at half the film's resolution. This is judged by eye in phase 6.

### How it works

- **Two processes.** A worker (Python, below-normal priority) reads the film ahead of the playhead and makes the sides. The player samples them every film frame. They meet in one memory-mapped file, the side store.
  - Its binary layout is a versioned contract: [`side-store.layout.json`](../../spikes/ai-surround/live/side-store.layout.json), schema `panorama.side-store/1` (2 since phase 4: see the amendment below).
  - The Python writer builds its records from that file; the player's C reader pins every offset with static asserts. A self-test proves both agree.
- **Consistency without locks.** Every record that can change carries a sequence counter, odd while it is written. A reader keeps a copy only if the counter was even and unchanged. Canvas pixels are checked again after every chunk the player copies.
- **The side plan.** Every frame of the film's frame table maps its source pixels to its shot's world (v1: a translation from optical flow). It also carries a gain and a step state per side. Each shot names its canvas and the crop taken as the picture. Times and pixels follow the content model's conventions ([ADR 0027](0027-content-model-schema-v0-timeline-and-cache-key.md)).
- **Canvases.** A canvas is an RGBA image of a shot's world with a tier (1: the shot's own real pixels; 2: a painted plate; 3 and 4 in later phases) and a version.
  - Canvases live in fixed-size heap slots of the store, and the worker keeps those needed soon there. The player pulls what the next seconds need onto the GPU, a few rows per refresh.
  - A crash of the worker stops only new canvases. A restart empties the store in place with a new id, and the player drops what it took from it.
- **The player record.** The player writes back:
  - the playhead and the seek counter;
  - present slack and missed refreshes (the worker holds GPU work back on a heavy burst);
  - the side area the room needs;
  - what is on screen.
- **The fill.** It runs inside the player's surround pass, before the glow. Each leftover pixel takes its source position from the picture pass's own map (camera slide included), goes through the frame's transform to the canvas, and gets a gain and a seam pull from the picture's edge.
- **GPU sharing** ([E1](../../plans/reports/spike-261013-1950-e1-player-under-ai-load.md)):
  - the player reserves its own VRAM;
  - the painter fits its VRAM budget without streaming weights while the film plays. Capping ComfyUI's painter (`--reserve-vram 4`) doubled E1's wrong frames, so phase 3 replaces it;
  - kernel length is not limited;
  - clock locking belongs to the M1 track.
- **Swappable roles.** Analyse, paint, refine and render meet only through this contract. Phases 3-5 replace the painter, add the structure path and the zone refiner behind it. Zone patches that change at a drawing may need a new region in a minor version.

### Amendment, 2026-10-14 (phase 4: structure path, schema `panorama.side-store/2`)

The contract above becomes major version 2:
- **Projective frame maps.** A frame's transform is a homography `h[8]` instead of a 2x3 map. The camera model ([`camera_model.py`](../../spikes/ai-surround/live/camera_model.py)) measures pans through 3D sets, zooms and rolls. The fill samples through it with a perspective divide.
- **Patch pages.** A shot's patch page is held in a heap slot: a grid map into the set's world, a patch table, and packed RGBA8 texels.
  - Persistent patches go into a per-showing accumulate texture from their film frame on.
  - Overlay patches show for a frame range.
  - The fill composites overlay over accumulate over the canvas.
  - Phase 4 fills pages with exit strips: the picture's last view of what the camera leaves behind. Phase 5's refined zones use the same pages.

What you see changes in one way, and that needs the user's OK under ADR 0017. Within a shot, the sides may now change at a new drawing (as the camera moves on, the real pixels it leaves behind replace the canvas there). They still never change between drawings, and the tier still changes only at a cut: a shot shows its patches only if its page was on the GPU when the shot began.

## Consequences

- The cue sheet and the worker must take the same crop as the picture. The worker reads the same cue sheet, and the player shows glow on a mismatch.
- The first shot after play or a seek shows the glow unless its canvas is already on the GPU, which is rare after a seek: tiers change only at cuts.
- The cache is keyed by the room's side widths, rounded up to 32 source px: a windowed and a full-screen player share it. Another room or mosaic version means the film is analysed again, since a mosaic needs the decoded frames.
- The worker's painter target grows with the room's real side area (about 1.7x the picture's area on Setup A, against 1.0x in the speed tests); phase 3 sizes its levers on that.
- Not settled here: the rolling blend together with the AI sides (untested); tiles for very long pans (v1 coarsens the canvas instead); the clock lock (M1).
