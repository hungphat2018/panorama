// The AI sides of the live path (plans/261013-0718-ai-surround-live-path, phase 2): the player end. A separate worker
// process (spikes/ai-surround/live-worker.py) makes the sides ahead of the playhead and hands them over through the side
// store (perf1-ai-sides-store.h); here they are sampled every film frame, and the player never waits for them.
//
//   perf1 --ai-sides STORE [--ai-horizon-s 8] [--ai-gpu-canvases 8] [--ai-upload-kb 256] [--ai-no-seam]
//
// Per refresh, inside the picture path (perf1-picture-render.c calls ai_sides_refresh before passes 2 and 3):
//   pump      (the master's refreshes) the playhead record into the store; the next canvas the coming shots need copied
//             from the store's heap to the GPU, a few rows per refresh; the store re-opened or dropped as the worker comes
//             and goes (a crash only stops new canvases: what is on the GPU stays)
//   showing   the first refresh that draws a shot (after a cut, a seek or the film start) picks the best canvas already
//             on the GPU for it and keeps it for the whole shot: tiers change only at cuts; nothing ready means glow
//   fill      pass 2's AI fragment (perf1-finish-surround-ai.c): each leftover pixel's source position (pass 1's own map,
//             the camera slide included), the frame's camera transform to the canvas, the canvas under the picture, its
//             gain, the seam pull from this frame's picture edge; the glow fills what the canvas does not cover
// Writes OUT/ai-sides.json at the end (uploads, showings, film time per tier, costs).
#pragma once

#include "perf1-picture.h"
#include "perf1-picture-prepare.h"

// One of its options at argv[*i]: 1 taken, 0 not its, -1 malformed (the convention of picture_arg).
int ai_sides_arg(int argc, char **argv, int *i);
void ai_sides_usage(void);
bool ai_sides_on(void);

// After the monitor's picture-path objects exist: the fill hook on its surround (a real GPU only).
void ai_sides_init_monitor(struct monitor *m, struct fp_surround *surround);
// One refresh, before passes 2 and 3: pump, showing, the fill's inputs for this monitor. front: the prepared front frame.
void ai_sides_refresh(const struct pic_refresh *r, const struct pic_shot *shot, const struct pic_pass1_job *job,
                      const struct pic_prepared *front);
void ai_sides_destroy(void);   // before the device goes
void ai_sides_write_log(void);
