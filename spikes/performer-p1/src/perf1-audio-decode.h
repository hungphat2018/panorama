// The sound's own demuxer and decoder: it opens the file a second time (the video decoder is not touched), reads one audio
// stream on its own thread and queues the decoded frames with their film time. Film time is the video decoder's timeline:
// the file's timestamp minus the timestamp of the first video frame, so film time 0 is the first picture.
//
// Seeks follow the same generation counter as the video supply (perf1-engine.h): audio_src_seek(gen, t) empties the queue
// at once; the thread then seeks the demuxer to a little before t, decodes forward and queues every frame that ends after
// t - PREROLL (a whole second before the target, so the consumer can cut at the exact sample). Frames of an older
// generation are never returned.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>

struct audio_src;

struct audio_src_info {
    int stream;                 // index in the file
    int track;                  // ordinal among the file's audio streams
    int n_tracks;
    int rate, channels;
    char codec[32];
    char layout[64];
    double video_first;         // file timeline seconds of the first video frame (film time 0); 0 without video
};

// One decoded frame. The consumer owns `frame` (av_frame_free) once it has popped it.
struct audio_chunk {
    AVFrame *frame;
    double t0;                  // film time of its first sample
    double dur;                 // its length in seconds
};

// track < 0: FFmpeg's best audio stream; otherwise the track-th audio stream (0 based). NULL with a message in err.
struct audio_src *audio_src_open(const char *file, int track, char *err, size_t err_size);
const struct audio_src_info *audio_src_info(const struct audio_src *s);
bool audio_src_start(struct audio_src *s);          // starts the thread; it decodes from the start of the file (generation 0)
void audio_src_seek(struct audio_src *s, int gen, double film_target);
// 1: *out holds a frame; 0: nothing queued for this generation yet; -1: this generation has no more frames.
int audio_src_pop(struct audio_src *s, int gen, struct audio_chunk *out);
double audio_src_queued_s(struct audio_src *s);     // seconds of sound waiting in the queue
void audio_src_close(struct audio_src **s);         // stops the thread, frees everything
