// Text subtitle reading, see perf1-subs-load.h.
#include "perf1-subs-load.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>

static bool fail(char *err, size_t n, const char *fmt, ...) __attribute__((format(gnu_printf, 3, 4)));
static bool fail(char *err, size_t n, const char *fmt, ...)
{
    if (err && n) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(err, n, fmt, ap);
        va_end(ap);
    }
    return false;
}

static bool is_text_codec(enum AVCodecID id)
{
    switch (id) {
    case AV_CODEC_ID_ASS:
    case AV_CODEC_ID_SSA:
    case AV_CODEC_ID_SUBRIP:
    case AV_CODEC_ID_SRT:
    case AV_CODEC_ID_TEXT:
    case AV_CODEC_ID_MOV_TEXT:
    case AV_CODEC_ID_WEBVTT:
        return true;
    default:
        return false;
    }
}

// The stream to read: the hint if it is a text subtitle stream, else the default-flagged one, else the first.
static int pick_stream(const AVFormatContext *fc, int hint)
{
    int first = -1, flagged = -1;
    for (unsigned i = 0; i < fc->nb_streams; i++) {
        const AVStream *st = fc->streams[i];
        if (st->codecpar->codec_type != AVMEDIA_TYPE_SUBTITLE || !is_text_codec(st->codecpar->codec_id))
            continue;
        if ((int) i == hint)
            return hint;
        if (first < 0)
            first = (int) i;
        if (flagged < 0 && (st->disposition & AV_DISPOSITION_DEFAULT))
            flagged = (int) i;
    }
    return hint >= 0 ? -1 : flagged >= 0 ? flagged : first;
}

static void read_fonts(const AVFormatContext *fc, struct subs_track *t)
{
    for (unsigned i = 0; i < fc->nb_streams; i++) {
        const AVStream *st = fc->streams[i];
        if (st->codecpar->codec_type != AVMEDIA_TYPE_ATTACHMENT || st->codecpar->extradata_size <= 0)
            continue;
        const AVDictionaryEntry *mime = av_dict_get(st->metadata, "mimetype", NULL, 0);
        const AVDictionaryEntry *name = av_dict_get(st->metadata, "filename", NULL, 0);
        if (!mime || !name || (!strstr(mime->value, "font") && !strstr(mime->value, "opentype") && !strstr(mime->value, "truetype")))
            continue;
        struct subs_font *f = realloc(t->font, (t->n_font + 1) * sizeof(*f));
        if (!f)
            return;
        t->font = f;
        f += t->n_font;
        f->name = strdup(name->value);
        f->data = malloc((size_t) st->codecpar->extradata_size);
        f->n = (size_t) st->codecpar->extradata_size;
        if (!f->name || !f->data) {
            free(f->name);
            free(f->data);
            return;
        }
        memcpy(f->data, st->codecpar->extradata, f->n);
        t->n_font++;
    }
}

static bool add_event(struct subs_track *t, const char *text, long long start_ms, long long duration_ms)
{
    if ((t->n_ev & 1023) == 0) {
        struct subs_event *e = realloc(t->ev, (t->n_ev + 1024) * sizeof(*e));
        if (!e)
            return false;
        t->ev = e;
    }
    struct subs_event *e = &t->ev[t->n_ev];
    e->n = strlen(text);
    e->text = malloc(e->n + 1);
    if (!e->text)
        return false;
    memcpy(e->text, text, e->n + 1);
    e->start_ms = start_ms;
    e->duration_ms = duration_ms;
    t->n_ev++;
    return true;
}

// Decodes one packet into ASS lines and adds them with the packet's time and duration.
static void take_packet(struct subs_track *t, AVCodecContext *dec, const AVPacket *pkt, double tb, double shift_s)
{
    AVSubtitle sub;
    int got = 0;
    if (pkt->pts == AV_NOPTS_VALUE || pkt->duration <= 0 || avcodec_decode_subtitle2(dec, &sub, &got, (AVPacket *) pkt) < 0 ||
        !got) {
        t->skipped++;
        return;
    }
    long long start_ms = (long long) llround((pkt->pts * tb - shift_s) * 1000.0);
    long long dur_ms = (long long) llround(pkt->duration * tb * 1000.0);
    bool any = false;
    for (unsigned i = 0; i < sub.num_rects; i++)
        if (sub.rects[i]->type == SUBTITLE_ASS && sub.rects[i]->ass && sub.rects[i]->ass[0]) {
            any = add_event(t, sub.rects[i]->ass, start_ms, dur_ms) || any;
        }
    if (!any)
        t->skipped++;
    avsubtitle_free(&sub);
}

bool subs_load(const char *path, int stream_hint, bool offset_from_video, struct subs_track *out, char *err, size_t err_size)
{
    memset(out, 0, sizeof(*out));
    snprintf(out->source, sizeof(out->source), "%s", path);
    clock_t c0 = clock();
    AVFormatContext *fc = NULL;
    AVCodecContext *dec = NULL;
    AVPacket *pkt = NULL;
    bool ok = false;
    do {
        if (avformat_open_input(&fc, path, NULL, NULL) < 0) {
            fail(err, err_size, "cannot open %s", path);
            break;
        }
        if (avformat_find_stream_info(fc, NULL) < 0) {
            fail(err, err_size, "cannot read the streams of %s", path);
            break;
        }
        int si = pick_stream(fc, stream_hint);
        if (si < 0) {
            fail(err, err_size, "%s has no text subtitle stream", path);
            break;
        }
        const AVStream *st = fc->streams[si];
        const AVCodec *codec = avcodec_find_decoder(st->codecpar->codec_id);
        dec = codec ? avcodec_alloc_context3(codec) : NULL;
        if (!dec || avcodec_parameters_to_context(dec, st->codecpar) < 0 || avcodec_open2(dec, codec, NULL) < 0) {
            fail(err, err_size, "no decoder for the subtitle stream of %s", path);
            break;
        }
        snprintf(out->codec, sizeof(out->codec), "%s", codec->name);
        if (dec->subtitle_header && dec->subtitle_header_size > 0) {
            out->header = malloc((size_t) dec->subtitle_header_size);
            if (!out->header)
                break;
            memcpy(out->header, dec->subtitle_header, (size_t) dec->subtitle_header_size);
            out->header_n = (size_t) dec->subtitle_header_size;
        }
        read_fonts(fc, out);
        double shift_s = 0.0;
        if (offset_from_video) {
            for (unsigned i = 0; i < fc->nb_streams; i++) {
                const AVStream *v = fc->streams[i];
                if (v->codecpar->codec_type == AVMEDIA_TYPE_VIDEO && !(v->disposition & AV_DISPOSITION_ATTACHED_PIC) &&
                    v->start_time != AV_NOPTS_VALUE) {
                    shift_s = (double) v->start_time * av_q2d(v->time_base);
                    break;
                }
            }
        }
        pkt = av_packet_alloc();
        if (!pkt) {
            fail(err, err_size, "out of memory");
            break;
        }
        double tb = av_q2d(st->time_base);
        while (av_read_frame(fc, pkt) >= 0) {
            if (pkt->stream_index == si)
                take_packet(out, dec, pkt, tb, shift_s);
            av_packet_unref(pkt);
        }
        if (!out->n_ev) {
            fail(err, err_size, "the subtitle stream of %s holds no usable events (%zu packets skipped)", path, out->skipped);
            break;
        }
        ok = true;
    } while (0);
    av_packet_free(&pkt);
    avcodec_free_context(&dec);
    avformat_close_input(&fc);
    out->seconds = (double) (clock() - c0) / CLOCKS_PER_SEC;
    if (!ok)
        subs_free(out);
    return ok;
}

void subs_free(struct subs_track *t)
{
    for (size_t i = 0; i < t->n_ev; i++)
        free(t->ev[i].text);
    free(t->ev);
    for (size_t i = 0; i < t->n_font; i++) {
        free(t->font[i].name);
        free(t->font[i].data);
    }
    free(t->font);
    free(t->header);
    memset(t, 0, sizeof(*t));
}

bool subs_apply(const struct subs_track *t, struct fp_subs *s)
{
    for (size_t i = 0; i < t->n_font; i++)
        fp_subs_add_font(s, t->font[i].name, t->font[i].data, t->font[i].n);
    if (!fp_subs_track_start(s, t->header, t->header_n))
        return false;
    for (size_t i = 0; i < t->n_ev; i++)
        fp_subs_track_chunk(s, t->ev[i].text, t->ev[i].n, t->ev[i].start_ms, t->ev[i].duration_ms);
    return true;
}
