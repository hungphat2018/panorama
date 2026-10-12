// Reads a text subtitle track (and the fonts attached to the file) with a separate AVFormatContext, up front: subtitles are
// small, so every packet is demuxed and decoded to ASS dialogue lines before the film starts, and libass gets them all at
// once. The film's own decoder (perf1-decode.c) is never involved. Works on the film itself (an ASS, SRT or similar
// stream inside the container) and on an external .ass/.srt/.vtt file (FFmpeg demuxes those as one-stream files).
//
// Times are on the film's rebased timeline (the first video frame is 0): the video stream's start time is subtracted.
// Bitmap subtitles (PGS, DVD) are not read; the mpv per-file fallback plays those.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "perf1-finish-passes.h"

struct subs_event {
    char *text;                 // an ASS dialogue line as libavcodec makes it (ReadOrder,Layer,Style,...,Text)
    size_t n;
    long long start_ms, duration_ms;
};

struct subs_font {
    char *name;
    uint8_t *data;
    size_t n;
};

struct subs_track {
    char codec[24];             // "ass", "subrip", ...
    char source[260];           // what was read, for messages
    char *header;               // the ASS script header (styles) the decoder made or the container holds
    size_t header_n;
    struct subs_event *ev;
    size_t n_ev, skipped;       // skipped: packets with no usable text or duration
    struct subs_font *font;
    size_t n_font;
    double seconds;             // time spent reading
};

// path: a film or a subtitle file. stream_hint: the stream index to use (-1 = the default-flagged text subtitle stream,
// else the first one). offset_from_video: shift times so the first video frame is 0 (the film), false for an external
// file whose times are already film times. False with a message when there is no readable text track.
bool subs_load(const char *path, int stream_hint, bool offset_from_video, struct subs_track *out, char *err, size_t err_size);
void subs_free(struct subs_track *t);

// Hands the whole track to a libass renderer: the fonts, the header, every event.
bool subs_apply(const struct subs_track *t, struct fp_subs *s);
