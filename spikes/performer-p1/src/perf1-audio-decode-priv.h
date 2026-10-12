// Private state of the sound's demuxer + decoder, shared by perf1-audio-open.c and perf1-audio-decode.c.
#pragma once

#include <stdatomic.h>
#include <stdlib.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "perf1-audio-decode.h"

#define QUEUE_FRAMES 512        // ring size
#define QUEUE_MAX_S 3.0         // the thread waits while this much sound is queued
#define PREROLL_S 1.0           // decoded and queued from this long before a seek target

struct qitem {
    AVFrame *frame;
    double t0, dur;
};

struct audio_src {
    AVFormatContext *fmt;
    AVCodecContext *codec;
    AVStream *st;
    struct audio_src_info info;
    double tb;                  // seconds per tick of the stream

    HANDLE thread;
    atomic_bool exit_thread;

    CRITICAL_SECTION cs;        // guards everything below
    CONDITION_VARIABLE cv;      // the thread waits on it for room in the queue and for a request
    int want_gen;
    double want_target;
    int q_gen;                  // generation the queue belongs to
    bool q_eof;                 // the thread has queued everything of q_gen
    struct qitem q[QUEUE_FRAMES];
    int q_head, q_count;
    double q_seconds;
};
