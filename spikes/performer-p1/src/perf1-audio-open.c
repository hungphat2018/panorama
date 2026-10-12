// Opening the file for the sound: which audio stream, which video start is film time 0, the decoder. See perf1-audio-decode.h.
#include "perf1-audio-decode-priv.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

// ---- open ------------------------------------------------------------------------------------------------------

static void set_err(char *err, size_t n, const char *fmt, const char *a, const char *b)
{
    if (err && n)
        snprintf(err, n, fmt, a, b);
}

// Film time 0: the first video frame. The video stream's own start time is what the video decoder's first frame carries.
static double video_first_time(AVFormatContext *fmt)
{
    int v = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (v >= 0) {
        const AVStream *vs = fmt->streams[v];
        if (vs->start_time != AV_NOPTS_VALUE)
            return (double) vs->start_time * av_q2d(vs->time_base);
    }
    return fmt->start_time != AV_NOPTS_VALUE ? (double) fmt->start_time / AV_TIME_BASE : 0.0;
}

static int pick_stream(AVFormatContext *fmt, int track, int *n_tracks)
{
    int n = 0, found = -1;
    for (unsigned i = 0; i < fmt->nb_streams; i++)
        if (fmt->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO) {
            if (n == track)
                found = (int) i;
            n++;
        }
    *n_tracks = n;
    if (track < 0)
        return av_find_best_stream(fmt, AVMEDIA_TYPE_AUDIO, -1, -1, NULL, 0);
    return found;
}

struct audio_src *audio_src_open(const char *file, int track, char *err, size_t err_size)
{
    struct audio_src *s = calloc(1, sizeof(*s));
    if (!s)
        return NULL;
    InitializeCriticalSection(&s->cs);
    InitializeConditionVariable(&s->cv);
    s->q_gen = 0;               // generation 0: from the start of the file, until the first seek
    s->want_gen = 0;
    if (avformat_open_input(&s->fmt, file, NULL, NULL) != 0) {
        set_err(err, err_size, "cannot open '%s'%s", file, "");
        goto fail;
    }
    if (avformat_find_stream_info(s->fmt, NULL) < 0) {
        set_err(err, err_size, "no stream information in '%s'%s", file, "");
        goto fail;
    }
    int n_tracks = 0;
    int idx = pick_stream(s->fmt, track, &n_tracks);
    s->info.n_tracks = n_tracks;
    if (idx < 0) {
        char want[16];
        snprintf(want, sizeof(want), "%d", track);
        set_err(err, err_size, n_tracks ? "audio track %s does not exist%s" : "the file has no audio stream%s",
                track < 0 ? "" : want, "");
        goto fail;
    }
    s->st = s->fmt->streams[idx];
    s->tb = av_q2d(s->st->time_base);
    const AVCodec *dec = avcodec_find_decoder(s->st->codecpar->codec_id);
    if (!dec) {
        set_err(err, err_size, "no decoder for the audio stream (%s)%s", avcodec_get_name(s->st->codecpar->codec_id), "");
        goto fail;
    }
    s->codec = avcodec_alloc_context3(dec);
    if (!s->codec || avcodec_parameters_to_context(s->codec, s->st->codecpar) < 0) {
        set_err(err, err_size, "cannot set up the audio decoder%s%s", "", "");
        goto fail;
    }
    s->codec->pkt_timebase = s->st->time_base;
    if (avcodec_open2(s->codec, dec, NULL) < 0) {
        set_err(err, err_size, "cannot open the audio decoder (%s)%s", dec->name, "");
        goto fail;
    }
    s->info.stream = idx;
    s->info.track = 0;
    for (int i = 0; i < idx; i++)
        if (s->fmt->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO)
            s->info.track++;
    s->info.rate = s->codec->sample_rate;
    s->info.channels = s->codec->ch_layout.nb_channels;
    snprintf(s->info.codec, sizeof(s->info.codec), "%s", dec->name);
    av_channel_layout_describe(&s->codec->ch_layout, s->info.layout, sizeof(s->info.layout));
    s->info.video_first = video_first_time(s->fmt);
    return s;
fail:
    audio_src_close(&s);
    return NULL;
}

const struct audio_src_info *audio_src_info(const struct audio_src *s)
{
    return &s->info;
}
