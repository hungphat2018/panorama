// The performer's sound (public face). Audio follows the film clock, never the other way round: the film clock is the master
// monitor's refresh count (perf1-engine.h); the sound is placed on it and held on it.
//
//   perf1-audio.c         open / close, the thread, the engine's notes (breaks, epoch starts)
//   perf1-audio-play.c    one wake-up of the thread: device clock, error against the film, correction, placing, filling
//   perf1-audio-log.c     audio.csv and the summary line
//   perf1-audio-decode.c  own demuxer + decoder of the chosen stream      perf1-audio-pipe.c  convert, speed, film time of every block
//   perf1-audio-sync.c    device clock, write log, correction loop          perf1-audio-sink.h  WASAPI / stand-in sink
//
// Sync model. An epoch starts on master refresh J0 (film time `origin`). The audio is placed so that the sample for
// `origin` sounds when J0's light reaches the aim row: silence is written until the device frame that sounds then. From
// there the error "film time sounding now minus film clock now" is measured every device period and held near zero by
// small changes of the resampling ratio (a PI loop, at most 0.5 %); only beyond 40 ms is the sound moved (silence
// inserted or sound skipped, logged). The film clock includes the playback speed lock (+0.1 % at 23.976 fps on 96 or
// 72 Hz): the sound plays at the same speed, pitch included. The re-aim at cuts (a picture frame moves by one master refresh) is not
// followed: the sound stays on the unshifted clock, so the picture stays within one master refresh of it.
#pragma once

#include <stdbool.h>

#include "perf1.h"

// Opens the chosen audio stream of cfg->file and the output (the default WASAPI device, or --audio-null's stand-in), and
// starts the thread. NULL (with the reason printed) when there is no sound: --no-audio, no audio stream, no device.
struct audio *audio_open(const struct cfg *cfg);
// engine_host.note: breaks and epoch starts, from the render thread. Never blocks.
void audio_engine_note(void *user, const struct engine_note *n);
// Stops the thread and the device, writes audio.csv into dir, prints the summary line. Safe to call twice.
void audio_close(struct audio **a, const char *dir);
