// Where the sound goes: a device's shared-mode buffer (WASAPI) or a stand-in that consumes frames on the same period
// schedule from the host clock (--audio-null: no device, so the sync logic can be tested anywhere). Both are pull devices:
// the audio thread waits for `wake` (once per period), asks how much is queued, writes float frames and reads the
// device position with the host time of the reading.
//
// Frame numbering. After halt() the position restarts at 0 and the first frame written is frame 0; start() is called
// with the buffer already holding silence. position() counts frames the device has played since then (a device that
// starves goes on counting, so position > frames written means an underrun).
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

struct audio_sink {
    const char *kind;               // "wasapi" | "null"
    char name[160];
    int rate, channels;             // the device's mix format (float32, interleaved)
    uint64_t chmask;                // speaker mask, 0 if unknown
    uint32_t capacity;              // frames the device buffer holds
    uint32_t period;                // frames per wake-up
    double stream_latency_s;        // what the device reports (WASAPI GetStreamLatency), 0 for the stand-in
    HANDLE wake;                    // signalled once per period while started

    bool (*start)(struct audio_sink *s);                        // the buffer already holds silence (see lock/unlock)
    bool (*halt)(struct audio_sink *s);                         // stop, throw away everything queued, position back to 0
    uint32_t (*padding)(struct audio_sink *s);                  // frames queued and not yet played
    float *(*lock)(struct audio_sink *s, uint32_t frames);      // room for `frames` (at most capacity - padding)
    bool (*unlock)(struct audio_sink *s, uint32_t frames);      // the frames were written
    bool (*position)(struct audio_sink *s, uint64_t *frames, double *host_s);
    void (*on_wake)(struct audio_sink *s);                      // call after every wake-up
    void (*close)(struct audio_sink *s);
    void *impl;
};

// volume 0..1 is applied to the stream only (ISimpleAudioVolume, not persisted). NULL with a message in err.
struct audio_sink *audio_sink_wasapi_open(double volume, char *err, size_t err_size);
// A device of `rate` Hz and `channels` whose clock runs drift_ppm fast against the host clock, and whose driver holds frames
// for latency_ms after taking them from the buffer (the position reports what has sounded, as a shared-mode stream does).
struct audio_sink *audio_sink_null_open(int rate, int channels, double drift_ppm, double latency_ms);
void audio_sink_destroy(struct audio_sink **s);
