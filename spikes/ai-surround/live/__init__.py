"""The AI surround live path, worker side: a separate process reads the film ahead of the playhead, plans the sides and
makes them, and hands them to the player through the side store. The player (spikes/performer-p1, perf1 --ai-sides)
samples them every film frame and never waits. Plan: plans/261013-0718-ai-surround-live-path/ (phase 2).

ROLES (each behind a contract below; swapping one role's tool touches no other role)
  analyse   frame table, shots, each frame's camera transform and gain      analyse_v0.py
  paint     T1 mosaic of the shot's own real pixels; T2 painted plate          mosaic_t1.py, painter.py (+ the
            (the resident painter service, phase 3)                           painter service, painter-service.py)
  schedule  follow the playhead, earliest deadline first, drop stale work      live-worker.py
  render    the player: one world canvas per showing, the camera transform,   src/perf1-ai-sides.c,
            seam pull and gain, glow where nothing is ready                    src/perf1-finish-surround-ai.c

CONTRACTS (binary layout: side-store.layout.json, schema panorama.side-store/2)
  Side plan (worker -> player). Per frame of the film's frame table: its shot, its camera transform (source px ->
    world px of the shot's canvas, projective: camera_model.py), a linear-light gain per side (for now one value for all
    four: the picture's luma over its median, analyse_v0) and a step state per side (HOLD, STILL, SLIDE, GAIN, PATCH, GEN: step_planner.py; the
    player ignores it). Per shot: its frame range and cue-sheet start, its canvas id (the set it belongs to), its key
    frame (whose source px are the world px) and the crop the analysis took as the picture.
  Patch page (worker -> player, phase 4: structure.py). Per shot, in a heap slot: a grid map into the set's world and
    patches, each from a film frame on (persistent: into the showing's accumulate texture) or for a frame range
    (overlay). The player composites overlay over accumulate over the canvas, only for a shot whose page was on the GPU
    when it began.
  Canvas (worker -> player). An RGBA image of a set's world, at world px x0 + i * scale (texel i), with a tier
    (1 mosaic, 2 plate; 3 structure and 4 refined later), the film's own encoded RGB with coverage in alpha, colour
    already fitted to the picture. Versions of one canvas id grow; the player shows the best version that was on the
    GPU when the showing began: a showing starts at a cut, a seek or the film start, so tiers change only at cuts.
    Canvases live in fixed-size heap slots of the store; the worker keeps the ones near the playhead there (from its
    disk cache, side_cache.py) and never reuses the slot the player is copying.
  Playhead record (player -> worker). Film time on the master monitor, speed, a seek counter (epoch), present slack
    and late refreshes (the worker holds GPU work back when they show pressure), the side area the room needs
    (need_margin_px: how far beyond the crop leftover pixels reach, source px), and what is on screen.

TIERS (degrade ladder; a missing tier falls to the one below and the player never stalls)
  T0 glow (the player's own surround)  T1 mosaic  T2 plate  T3 structure patches (phase 4)  T4 refined zones (phase 5)

Units: film time in seconds on the rebased timeline (first frame = 0; the content model's and the player's frame
times, bit for bit); positions in pixels of the stored video frame (spikes/film-analysis/schema/common.schema.json).
"""
CONTRACT_VERSION = "panorama.side-store/2"
