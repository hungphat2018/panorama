// FFmpeg demux + decode feeding one pl_queue per monitor. Derived from perf1-decode.c (spikes/performer-p1) and
// perf0-decode.c (adapted from libplacebo demos/plplay.c, CC0 / public domain), with the D3D11VA import taken out.
//
// The file is decoded once. Every decoded frame is pushed to every queue as its own AVFrame reference. Software
// decoding writes straight into libplacebo's host-mapped GPU buffers (pl_get_buffer2), so uploading a frame to the
// Vulkan device costs no extra copy. libplacebo's Vulkan pl_gpu is thread-safe, which FFmpeg's frame-threaded decoder
// needs: its worker threads call get_buffer2.
#include "perf1vk.h"

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/cpu.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vulkan.h>
#include <libavutil/pixdesc.h>
#include <libplacebo/utils/libav.h>

#define HW_EXTRA_FRAMES 12 // decoder surfaces kept beyond the DPB: every queue holds a few frames

struct decoder {
    const struct cfg *cfg;
    pl_gpu gpu;
    AVBufferRef *hw_device;     // libplacebo's VkDevice as FFmpeg sees it (DECODE_VULKAN); the gpu context owns it
    pl_queue queues[MAX_MONITORS];
    int nqueues;

    AVFormatContext *fmt;
    AVCodecContext *codec;
    const AVStream *stream;

    HANDLE thread;
    atomic_bool exit_thread;
    atomic_uint decoded, mapped, dropped;

    char codec_name[64], pix_fmt_name[64], hw_fmt_name[64];
};

// ---- pl_queue callbacks ---------------------------------------------------------------

static bool map_frame(pl_gpu gpu, pl_tex *tex, const struct pl_source_frame *src,
                      struct pl_frame *out_frame)
{
    AVFrame *frame = src->frame_data;
    struct decoder *d = frame->opaque;

    bool ok = pl_map_avframe_ex(gpu, out_frame, pl_avframe_params(
        .frame   = frame,
        .tex     = tex,
        .map_dovi = true,
    ));

    av_frame_free(&frame); // `out_frame` holds its own reference
    if (!ok) {
        fprintf(stderr, "perf1vk: failed mapping AVFrame\n");
        return false;
    }

    atomic_fetch_add(&d->mapped, 1);
    pl_frame_copy_stream_props(out_frame, d->stream);
    return true;
}

static void unmap_frame(pl_gpu gpu, struct pl_frame *frame, const struct pl_source_frame *src)
{
    pl_unmap_avframe(gpu, frame);
}

static void discard_frame(const struct pl_source_frame *src)
{
    AVFrame *frame = src->frame_data;
    struct decoder *d = frame->opaque;
    atomic_fetch_add(&d->dropped, 1);
    av_frame_free(&frame);
}

// ---- hardware decoding set-up ----------------------------------------------------------

static enum AVPixelFormat get_format_hw(AVCodecContext *ctx, const enum AVPixelFormat *fmts)
{
    struct decoder *d = ctx->opaque;
    for (const enum AVPixelFormat *p = fmts; *p != AV_PIX_FMT_NONE; p++) {
        if (*p != AV_PIX_FMT_VULKAN)
            continue;

        AVBufferRef *frames = NULL;
        int r = avcodec_get_hw_frames_parameters(ctx, ctx->hw_device_ctx, *p, &frames);
        if (r < 0) {
            fprintf(stderr, "perf1vk: avcodec_get_hw_frames_parameters: %s\n", av_err2str(r));
            return AV_PIX_FMT_NONE;
        }
        r = av_hwframe_ctx_init(frames);
        if (r < 0) {
            fprintf(stderr, "perf1vk: av_hwframe_ctx_init: %s\n", av_err2str(r));
            av_buffer_unref(&frames);
            return AV_PIX_FMT_NONE;
        }

        AVHWFramesContext *fc = (AVHWFramesContext *) frames->data;
        snprintf(d->hw_fmt_name, sizeof(d->hw_fmt_name), "%s/%s",
                 av_get_pix_fmt_name(*p), av_get_pix_fmt_name(fc->sw_format));
        av_buffer_unref(&ctx->hw_frames_ctx);
        ctx->hw_frames_ctx = frames;
        return *p;
    }
    fprintf(stderr, "perf1vk: decoder does not offer Vulkan frames for this stream\n");
    return AV_PIX_FMT_NONE;
}

// For hardware decoding pick the decoder that carries the matching hwaccel: for AV1 the default software decoder is
// libdav1d, which has none, while FFmpeg's native "av1" decoder does.
static const AVCodec *find_decoder(enum AVCodecID id, enum AVHWDeviceType type)
{
    if (type == AV_HWDEVICE_TYPE_NONE)
        return avcodec_find_decoder(id);

    void *it = NULL;
    const AVCodec *c;
    while ((c = av_codec_iterate(&it))) {
        if (c->id != id || !av_codec_is_decoder(c))
            continue;
        const AVCodecHWConfig *hwcfg;
        for (int i = 0; (hwcfg = avcodec_get_hw_config(c, i)); i++) {
            if (hwcfg->device_type == type && (hwcfg->methods & AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX))
                return c;
        }
    }
    return NULL;
}

// ---- open ------------------------------------------------------------------------------

// FFmpeg's own log lines go to the same event log as libplacebo's, with a timestamp, so a hitch can be matched with
// a decoder message.
static void ffmpeg_log_cb(void *avcl, int level, const char *fmt, va_list vl)
{
    if (level > av_log_get_level())
        return;
    char line[200];
    vsnprintf(line, sizeof(line), fmt, vl);
    size_t n = strlen(line);
    while (n && (line[n - 1] == '\n' || line[n - 1] == '\r'))
        line[--n] = '\0';
    if (n)
        gpu_note("ffmpeg: %s", line);
}

static bool open_file(struct decoder *d, const char *filename)
{
    static const int av_log_level[] = {
        [PL_LOG_NONE]  = AV_LOG_QUIET,
        [PL_LOG_FATAL] = AV_LOG_PANIC,
        [PL_LOG_ERR]   = AV_LOG_ERROR,
        [PL_LOG_WARN]  = AV_LOG_WARNING,
        [PL_LOG_INFO]  = AV_LOG_WARNING,
        [PL_LOG_DEBUG] = AV_LOG_VERBOSE,
        [PL_LOG_TRACE] = AV_LOG_DEBUG,
    };
    av_log_set_level(av_log_level[d->cfg->log_level]);
    av_log_set_callback(ffmpeg_log_cb);

    if (avformat_open_input(&d->fmt, filename, NULL, NULL) != 0) {
        fprintf(stderr, "perf1vk: avformat_open_input failed for '%s'\n", filename);
        return false;
    }
    if (avformat_find_stream_info(d->fmt, NULL) < 0) {
        fprintf(stderr, "perf1vk: avformat_find_stream_info failed\n");
        return false;
    }
    int idx = av_find_best_stream(d->fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (idx < 0) {
        fprintf(stderr, "perf1vk: no video stream\n");
        return false;
    }
    d->stream = d->fmt->streams[idx];
    const AVPixFmtDescriptor *desc = av_pix_fmt_desc_get(d->stream->codecpar->format);
    snprintf(d->pix_fmt_name, sizeof(d->pix_fmt_name), "%s", desc ? desc->name : "?");
    return true;
}

static bool init_codec(struct decoder *d)
{
    const struct cfg *cfg = d->cfg;
    enum AVHWDeviceType want = cfg->decode == DECODE_VULKAN ? AV_HWDEVICE_TYPE_VULKAN : AV_HWDEVICE_TYPE_NONE;
    const AVCodec *codec = find_decoder(d->stream->codecpar->codec_id, want);
    if (!codec) {
        fprintf(stderr, "perf1vk: no decoder for codec id %d\n", d->stream->codecpar->codec_id);
        return false;
    }
    snprintf(d->codec_name, sizeof(d->codec_name), "%s", codec->name);

    d->codec = avcodec_alloc_context3(codec);
    if (!d->codec || avcodec_parameters_to_context(d->codec, d->stream->codecpar) < 0) {
        fprintf(stderr, "perf1vk: failed allocating the codec context\n");
        return false;
    }

    if (cfg->decode == DECODE_VULKAN) {
        bool supported = false;
        const AVCodecHWConfig *hwcfg;
        for (int i = 0; (hwcfg = avcodec_get_hw_config(codec, i)); i++) {
            if (hwcfg->device_type == AV_HWDEVICE_TYPE_VULKAN && hwcfg->pix_fmt == AV_PIX_FMT_VULKAN &&
                (hwcfg->methods & AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX))
            {
                supported = true;
                break;
            }
        }
        if (!supported) {
            fprintf(stderr, "perf1vk: codec %s has no Vulkan hw config in this FFmpeg\n", codec->name);
            return false;
        }
        d->codec->hw_device_ctx = av_buffer_ref(d->hw_device); // the codec context owns this reference
        d->codec->get_format = get_format_hw;
        d->codec->extra_hw_frames = HW_EXTRA_FRAMES;
        d->codec->opaque = d;
        d->codec->thread_count = 1; // frame threading does not apply to hwaccel decoding
    } else {
        d->codec->thread_count = FFMIN(av_cpu_count() + 1, 16);
        d->codec->get_buffer2 = pl_get_buffer2;
        d->codec->opaque = &d->gpu;
    }
    d->codec->export_side_data |= AV_CODEC_EXPORT_DATA_FILM_GRAIN;

    if (avcodec_open2(d->codec, codec, NULL) < 0) {
        fprintf(stderr, "perf1vk: avcodec_open2 failed\n");
        return false;
    }
    return true;
}

struct decoder *decoder_open(const struct cfg *cfg, pl_gpu gpu, void *hw_device, pl_queue *queues, int nqueues)
{
    if (nqueues < 1 || nqueues > MAX_MONITORS)
        return NULL;
    if (cfg->decode == DECODE_VULKAN && !hw_device) {
        fprintf(stderr, "perf1vk: Vulkan Video decoding needs the FFmpeg view of the Vulkan device\n");
        return NULL;
    }
    struct decoder *d = calloc(1, sizeof(*d));
    if (!d)
        return NULL;
    d->cfg = cfg;
    d->gpu = gpu;
    d->hw_device = hw_device;
    d->nqueues = nqueues;
    for (int i = 0; i < nqueues; i++)
        d->queues[i] = queues[i];

    if (!open_file(d, cfg->file) || !init_codec(d))
        goto error;
    return d;

error:
    decoder_destroy(&d);
    return NULL;
}

// ---- decode loop -------------------------------------------------------------------------

// Gives each queue its own reference to the frame; the last queue takes the original. A queue that
// cannot get a reference misses that film frame (it shows up as a skipped frame in the log).
static void push_frame(struct decoder *d, AVFrame *frame, double pts, double duration)
{
    for (int i = 0; i < d->nqueues; i++) {
        AVFrame *f = i == d->nqueues - 1 ? frame : av_frame_clone(frame);
        if (!f)
            continue;
        f->opaque = d;
        pl_queue_push_block(d->queues[i], UINT64_MAX, &(struct pl_source_frame) {
            .pts        = pts,
            .duration   = duration,
            .map        = map_frame,
            .unmap      = unmap_frame,
            .discard    = discard_frame,
            .frame_data = f,
        });
    }
}

static void push_eof(struct decoder *d)
{
    for (int i = 0; i < d->nqueues; i++)
        pl_queue_push(d->queues[i], NULL);
}

static DWORD WINAPI decode_thread(LPVOID arg)
{
    struct decoder *d = arg;
    int ret;
    AVPacket *packet = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    if (!frame || !packet)
        goto done;

    double frame_duration = d->stream->avg_frame_rate.num && d->stream->avg_frame_rate.den
                          ? av_q2d(av_inv_q(d->stream->avg_frame_rate)) : 1.0 / 24.0;
    double first_pts = 0.0;
    uint64_t num_frames = 0;
    bool flushing = false;

    while (!atomic_load(&d->exit_thread)) {
        if (!flushing) {
            ret = av_read_frame(d->fmt, packet);
            if (ret == 0) {
                if (packet->stream_index != d->stream->index) {
                    av_packet_unref(packet);
                    continue;
                }
                ret = avcodec_send_packet(d->codec, packet);
                av_packet_unref(packet);
            } else if (ret == AVERROR_EOF) {
                ret = avcodec_send_packet(d->codec, NULL); // drain
                flushing = true;
            } else {
                fprintf(stderr, "perf1vk: av_read_frame: %s\n", av_err2str(ret));
                goto done;
            }
            if (ret < 0 && ret != AVERROR_EOF) {
                fprintf(stderr, "perf1vk: avcodec_send_packet: %s\n", av_err2str(ret));
                goto done;
            }
        }

        for (;;) {
            ret = avcodec_receive_frame(d->codec, frame);
            if (ret != 0)
                break;

            int64_t ts = frame->pts != AV_NOPTS_VALUE ? frame->pts : frame->best_effort_timestamp;
            double pts = ts * av_q2d(d->stream->time_base);
            if (num_frames++ == 0)
                first_pts = pts;
            atomic_fetch_add(&d->decoded, 1);
            push_frame(d, frame, pts - first_pts, frame_duration);
            frame = av_frame_alloc();
            if (!frame)
                goto done;
        }

        if (ret == AVERROR(EAGAIN))
            continue;
        if (ret == AVERROR_EOF)
            goto done;
        fprintf(stderr, "perf1vk: avcodec_receive_frame: %s\n", av_err2str(ret));
        goto done;
    }

done:
    push_eof(d); // lets the queues drain
    av_packet_free(&packet);
    av_frame_free(&frame);
    return 0;
}

bool decoder_start(struct decoder *d)
{
    d->thread = CreateThread(NULL, 0, decode_thread, d, 0, NULL);
    return d->thread != NULL;
}

void decoder_stop(struct decoder *d)
{
    if (!d->thread)
        return;
    atomic_store(&d->exit_thread, true);
    push_eof(d); // wakes a blocked push_block
    WaitForSingleObject(d->thread, INFINITE);
    CloseHandle(d->thread);
    d->thread = NULL;
}

// ---- info / teardown ----------------------------------------------------------------------

void decoder_get_info(struct decoder *d, struct decoder_info *out)
{
    memset(out, 0, sizeof(*out));
    snprintf(out->codec, sizeof(out->codec), "%s", d->codec_name);
    snprintf(out->pix_fmt, sizeof(out->pix_fmt), "%s", d->pix_fmt_name);
    snprintf(out->hw_fmt, sizeof(out->hw_fmt), "%s", d->hw_fmt_name);
    out->width = d->stream->codecpar->width;
    out->height = d->stream->codecpar->height;
    if (d->stream->avg_frame_rate.den)
        out->fps = av_q2d(d->stream->avg_frame_rate);
    if (d->fmt->duration != AV_NOPTS_VALUE)
        out->duration_s = d->fmt->duration / 1e6;
    out->decoded = atomic_load(&d->decoded);
    out->mapped = atomic_load(&d->mapped);
    out->dropped = atomic_load(&d->dropped);
}

void decoder_destroy(struct decoder **pd)
{
    struct decoder *d = *pd;
    if (!d)
        return;
    decoder_stop(d);
    avcodec_free_context(&d->codec); // before the GPU goes away: releases frame buffers
    avformat_close_input(&d->fmt);
    free(d);
    *pd = NULL;
}
