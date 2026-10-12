// FFmpeg demux + decode (software, D3D11VA, Vulkan Video) feeding a pl_queue.
// Adapted from libplacebo demos/plplay.c (CC0 / public domain). The D3D11VA import
// (wrap the decoder's array texture planes with pl_d3d11_wrap) is not part of
// libplacebo's libav helpers and was written for this spike.
#define COBJMACROS
#include "perf0.h"

#include <math.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/cpu.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_d3d11va.h>
#include <libavutil/hwcontext_vulkan.h>
#include <libavutil/pixdesc.h>
#include <libplacebo/utils/libav.h>

#define HW_EXTRA_FRAMES 6 // decoder surfaces kept beyond the DPB: the queue holds a few frames

struct decoder {
    const struct cfg *cfg;
    struct gpu_ctx *g;
    pl_log log;
    pl_queue queue;

    AVFormatContext *fmt;
    AVCodecContext *codec;
    const AVStream *stream;
    AVBufferRef *hw_device;
    enum AVPixelFormat hw_pix_fmt;

    HANDLE thread;
    atomic_bool exit_thread;

    atomic_uint decoded, mapped, dropped;
    double t_open_ms, t_codec_ms, t_first_decoded_ms;
    double decode_busy_ms, thread_cpu_s;

    char codec_name[64], pix_fmt_name[64], hw_fmt_name[64];
};

// ---- D3D11VA import: decoder array texture -> pl_tex planes -------------------------------

// libplacebo's libav helpers only have these two as internal statics in newer releases (7.360 lacks
// them), so the same few lines live here: bit layout of a hardware frame's software format (P010 is
// 10 bits stored in the top of 16: sample depth 16, colour depth 10, shift 6).
static void hwframe_bit_encoding(struct pl_bit_encoding *out, enum AVPixelFormat sw_fmt)
{
    const AVPixFmtDescriptor *desc = av_pix_fmt_desc_get(sw_fmt);
    struct pl_bit_encoding bits = {0};
    if (!desc)
        return;
    for (int c = 0; c < FFMIN(desc->nb_components, 3); c++) {
        const AVComponentDescriptor *comp = &desc->comp[c];
        struct pl_bit_encoding cb = {
            .sample_depth = comp->depth + FFABS(comp->shift),
            .color_depth  = comp->depth,
            .bit_shift    = FFMAX(comp->shift, 0),
        };
        if (bits.sample_depth && !pl_bit_encoding_equal(&bits, &cb))
            return; // components differ: leave the frame's default encoding
        bits = cb;
    }
    *out = bits;
}

// The wrapped texture's own component depth is the real sample depth.
static void fix_hwframe_sample_depth(struct pl_frame *out)
{
    out->repr.bits.sample_depth = out->planes[0].texture->params.format->component_depth[0];
}

static bool map_d3d11va(pl_gpu gpu, struct pl_frame *out, const AVFrame *frame)
{
    const AVHWFramesContext *hwfc = (const AVHWFramesContext *) frame->hw_frames_ctx->data;
    ID3D11Texture2D *tex = (ID3D11Texture2D *) frame->data[0];
    int slice = (int) (intptr_t) frame->data[1];

    DXGI_FORMAT fmt_luma, fmt_chroma;
    switch (hwfc->sw_format) {
    case AV_PIX_FMT_NV12:
        fmt_luma = DXGI_FORMAT_R8_UNORM;
        fmt_chroma = DXGI_FORMAT_R8G8_UNORM;
        break;
    case AV_PIX_FMT_P010:
    case AV_PIX_FMT_P016:
        fmt_luma = DXGI_FORMAT_R16_UNORM;
        fmt_chroma = DXGI_FORMAT_R16G16_UNORM;
        break;
    default:
        fprintf(stderr, "perf0: D3D11VA sw_format %s not handled\n", av_get_pix_fmt_name(hwfc->sw_format));
        return false;
    }

    D3D11_TEXTURE2D_DESC td;
    ID3D11Texture2D_GetDesc(tex, &td);

    struct pl_avframe_priv *priv = calloc(1, sizeof(*priv));
    if (!priv)
        return false;
    pl_frame_from_avframe(out, frame);
    priv->avframe = av_frame_clone(frame);
    out->user_data = priv;
    hwframe_bit_encoding(&out->repr.bits, hwfc->sw_format);

    for (int p = 0; p < out->num_planes; p++) {
        bool chroma = p > 0;
        out->planes[p].texture = pl_d3d11_wrap(gpu, pl_d3d11_wrap_params(
            .tex = (ID3D11Resource *) tex,
            .array_slice = slice,
            .fmt = chroma ? fmt_chroma : fmt_luma,
            .w = chroma ? (int) (td.Width + 1) / 2 : (int) td.Width,
            .h = chroma ? (int) (td.Height + 1) / 2 : (int) td.Height,
        ));
        if (!out->planes[p].texture) {
            fprintf(stderr, "perf0: pl_d3d11_wrap failed for plane %d (bind flags 0x%x)\n", p, td.BindFlags);
            pl_unmap_avframe(gpu, out);
            return false;
        }
    }

    fix_hwframe_sample_depth(out);
    return true;
}

// ---- pl_queue callbacks ---------------------------------------------------------------

static bool map_frame(pl_gpu gpu, pl_tex *tex, const struct pl_source_frame *src,
                      struct pl_frame *out_frame)
{
    AVFrame *frame = src->frame_data;
    struct decoder *d = frame->opaque;
    bool ok;

    if (frame->format == AV_PIX_FMT_D3D11) {
        ok = map_d3d11va(gpu, out_frame, frame);
    } else {
        ok = pl_map_avframe_ex(gpu, out_frame, pl_avframe_params(
            .frame   = frame,
            .tex     = tex,
            .map_dovi = true,
        ));
    }

    av_frame_free(&frame); // `out_frame` holds its own reference
    if (!ok) {
        fprintf(stderr, "perf0: failed mapping AVFrame\n");
        return false;
    }

    atomic_fetch_add(&d->mapped, 1);
    pl_frame_copy_stream_props(out_frame, d->stream);
    if (d->cfg->sd_passthrough) {
        // libplacebo converts BT.601 primaries to the BT.709 display and gamut-maps the leftovers, which desaturates
        // saturated SD colours; mpv's target-prim=auto passes them through. Untagged SD counts as BT.601 too.
        struct pl_color_space *cs = &out_frame->color;
        int h = (int) fabsf(out_frame->crop.y1 - out_frame->crop.y0), w = (int) fabsf(out_frame->crop.x1 - out_frame->crop.x0);
        if (cs->primaries == PL_COLOR_PRIM_BT_601_525 || cs->primaries == PL_COLOR_PRIM_BT_601_625 ||
            (cs->primaries == PL_COLOR_PRIM_UNKNOWN && w < 1280 && h <= 576))
            cs->primaries = PL_COLOR_PRIM_BT_709;
    }
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

static AVBufferRef *make_d3d11va_device(ID3D11Device *dev)
{
    AVBufferRef *ref = av_hwdevice_ctx_alloc(AV_HWDEVICE_TYPE_D3D11VA);
    if (!ref)
        return NULL;
    AVHWDeviceContext *dc = (AVHWDeviceContext *) ref->data;
    AVD3D11VADeviceContext *dh = dc->hwctx;
    dh->device = dev;
    ID3D11Device_AddRef(dev); // the hwcontext releases it when it is freed
    if (av_hwdevice_ctx_init(ref) < 0) {
        av_buffer_unref(&ref);
        return NULL;
    }
    return ref;
}

static enum AVPixelFormat get_format_hw(AVCodecContext *ctx, const enum AVPixelFormat *fmts)
{
    struct decoder *d = ctx->opaque;
    for (const enum AVPixelFormat *p = fmts; *p != AV_PIX_FMT_NONE; p++) {
        if (*p != d->hw_pix_fmt)
            continue;

        AVBufferRef *frames = NULL;
        int r = avcodec_get_hw_frames_parameters(ctx, ctx->hw_device_ctx, *p, &frames);
        if (r < 0) {
            fprintf(stderr, "perf0: avcodec_get_hw_frames_parameters: %s\n", av_err2str(r));
            return AV_PIX_FMT_NONE;
        }
        if (*p == AV_PIX_FMT_D3D11) {
            // The decoder's array texture must also be sampleable, so planes can be wrapped as shader views.
            AVHWFramesContext *fc = (AVHWFramesContext *) frames->data;
            ((AVD3D11VAFramesContext *) fc->hwctx)->BindFlags |= D3D11_BIND_SHADER_RESOURCE;
        }
        r = av_hwframe_ctx_init(frames);
        if (r < 0) {
            fprintf(stderr, "perf0: av_hwframe_ctx_init: %s\n", av_err2str(r));
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
    if (d->cfg->sw_fallback) {
        // FFmpeg's default pick is the software format at the end of the list (the codec is already open with
        // hardware settings, so frame threading is off and the fallback decodes on one thread).
        enum AVPixelFormat sw = avcodec_default_get_format(ctx, fmts);
        fprintf(stderr, "perf0: decoder does not offer %s for this stream, decoding in software (%s)\n",
                av_get_pix_fmt_name(d->hw_pix_fmt), av_get_pix_fmt_name(sw));
        snprintf(d->hw_fmt_name, sizeof(d->hw_fmt_name), "fallback/%s", av_get_pix_fmt_name(sw));
        return sw;
    }
    fprintf(stderr, "perf0: decoder does not offer %s for this stream\n", av_get_pix_fmt_name(d->hw_pix_fmt));
    return AV_PIX_FMT_NONE;
}

// ---- open ------------------------------------------------------------------------------

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

    if (avformat_open_input(&d->fmt, filename, NULL, NULL) != 0) {
        fprintf(stderr, "perf0: avformat_open_input failed for '%s'\n", filename);
        return false;
    }
    if (avformat_find_stream_info(d->fmt, NULL) < 0) {
        fprintf(stderr, "perf0: avformat_find_stream_info failed\n");
        return false;
    }
    int idx = av_find_best_stream(d->fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (idx < 0) {
        fprintf(stderr, "perf0: no video stream\n");
        return false;
    }
    d->stream = d->fmt->streams[idx];
    const AVPixFmtDescriptor *desc = av_pix_fmt_desc_get(d->stream->codecpar->format);
    snprintf(d->pix_fmt_name, sizeof(d->pix_fmt_name), "%s", desc ? desc->name : "?");
    return true;
}

// For hardware decoding pick the decoder that carries the matching hwaccel: for AV1 the default
// software decoder is libdav1d, which has none, while FFmpeg's native "av1" decoder does.
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

static bool init_codec(struct decoder *d)
{
    const struct cfg *cfg = d->cfg;
    enum AVHWDeviceType want = cfg->decode == DECODE_D3D11VA ? AV_HWDEVICE_TYPE_D3D11VA
                             : cfg->decode == DECODE_VULKAN  ? AV_HWDEVICE_TYPE_VULKAN
                                                             : AV_HWDEVICE_TYPE_NONE;
    const AVCodec *codec = find_decoder(d->stream->codecpar->codec_id, want);
    if (!codec) {
        fprintf(stderr, "perf0: no decoder for codec id %d\n", d->stream->codecpar->codec_id);
        return false;
    }
    snprintf(d->codec_name, sizeof(d->codec_name), "%s", codec->name);

    d->codec = avcodec_alloc_context3(codec);
    if (!d->codec || avcodec_parameters_to_context(d->codec, d->stream->codecpar) < 0) {
        fprintf(stderr, "perf0: failed allocating the codec context\n");
        return false;
    }

    if (cfg->decode != DECODE_SW) {
        enum AVHWDeviceType type = cfg->decode == DECODE_D3D11VA ? AV_HWDEVICE_TYPE_D3D11VA
                                                                  : AV_HWDEVICE_TYPE_VULKAN;
        d->hw_pix_fmt = cfg->decode == DECODE_D3D11VA ? AV_PIX_FMT_D3D11 : AV_PIX_FMT_VULKAN;

        bool supported = false;
        const AVCodecHWConfig *hwcfg;
        for (int i = 0; (hwcfg = avcodec_get_hw_config(codec, i)); i++) {
            if (hwcfg->device_type == type && hwcfg->pix_fmt == d->hw_pix_fmt &&
                (hwcfg->methods & AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX))
            {
                supported = true;
                break;
            }
        }
        if (!supported) {
            fprintf(stderr, "perf0: codec %s has no %s hw config in this FFmpeg\n",
                    codec->name, av_hwdevice_get_type_name(type));
            return false;
        }

        if (cfg->decode == DECODE_D3D11VA) {
            d->hw_device = make_d3d11va_device(d->g->d3d11->device);
        } else {
            d->hw_device = av_buffer_ref(d->g->ff_vk_device);
        }
        if (!d->hw_device) {
            fprintf(stderr, "perf0: failed creating the %s hw device\n", av_hwdevice_get_type_name(type));
            return false;
        }
        d->codec->hw_device_ctx = av_buffer_ref(d->hw_device);
        d->codec->get_format = get_format_hw;
        d->codec->extra_hw_frames = HW_EXTRA_FRAMES;
        d->codec->opaque = d;
        d->codec->thread_count = 1; // frame threading does not apply to hwaccel decoding
    } else {
        d->codec->thread_count = FFMIN(av_cpu_count() + 1, 16);
        d->codec->get_buffer2 = pl_get_buffer2;
        d->codec->opaque = &d->g->gpu;
    }
    d->codec->export_side_data |= AV_CODEC_EXPORT_DATA_FILM_GRAIN;

    if (avcodec_open2(d->codec, codec, NULL) < 0) {
        fprintf(stderr, "perf0: avcodec_open2 failed\n");
        return false;
    }
    return true;
}

struct decoder *decoder_open(const struct cfg *cfg, struct gpu_ctx *g, pl_log log, pl_queue queue)
{
    struct decoder *d = calloc(1, sizeof(*d));
    if (!d)
        return NULL;
    d->cfg = cfg;
    d->g = g;
    d->log = log;
    d->queue = queue;

    if (!open_file(d, cfg->file))
        goto error;
    d->t_open_ms = process_age_ms();
    if (!init_codec(d))
        goto error;
    d->t_codec_ms = process_age_ms();
    return d;

error:
    decoder_destroy(&d);
    return NULL;
}

// ---- decode loop -------------------------------------------------------------------------

static void push_frame(struct decoder *d, AVFrame *frame, double pts, double duration)
{
    frame->opaque = d;
    pl_queue_push_block(d->queue, UINT64_MAX, &(struct pl_source_frame) {
        .pts        = pts,
        .duration   = duration,
        .map        = map_frame,
        .unmap      = unmap_frame,
        .discard    = discard_frame,
        .frame_data = frame,

        // lets libplacebo split an interlaced frame into two fields for its deinterlacer (see plplay.c)
        .first_field = d->cfg->deinterlace ? pl_field_from_avframe(frame) : PL_FIELD_NONE,
    });
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
    double first_pts = 0.0, base_pts = 0.0, last_pts = 0.0;
    uint64_t num_frames = 0;
    bool flushing = false;
    int bad_streak = 0; // consecutive AVERROR_INVALIDDATA skipped with --skip-bad-packets

    while (!atomic_load(&d->exit_thread)) {
        if (!flushing) {
            ret = av_read_frame(d->fmt, packet);
            if (ret == 0) {
                if (packet->stream_index != d->stream->index) {
                    av_packet_unref(packet);
                    continue;
                }
                double t0 = now_ms();
                ret = avcodec_send_packet(d->codec, packet);
                d->decode_busy_ms += now_ms() - t0;
                av_packet_unref(packet);
            } else if (ret == AVERROR_EOF) {
                ret = avcodec_send_packet(d->codec, NULL); // drain
                flushing = true;
            } else {
                fprintf(stderr, "perf0: av_read_frame: %s\n", av_err2str(ret));
                if (ret == AVERROR_INVALIDDATA && d->cfg->skip_bad_packets && ++bad_streak < 200)
                    continue;
                goto done;
            }
            if (ret < 0 && ret != AVERROR_EOF) {
                fprintf(stderr, "perf0: avcodec_send_packet: %s\n", av_err2str(ret));
                if (ret == AVERROR_INVALIDDATA && d->cfg->skip_bad_packets && ++bad_streak < 200)
                    continue;
                goto done;
            }
        }

        for (;;) {
            double t0 = now_ms();
            ret = avcodec_receive_frame(d->codec, frame);
            d->decode_busy_ms += now_ms() - t0;
            if (ret != 0)
                break;

            int64_t ts = frame->pts != AV_NOPTS_VALUE ? frame->pts : frame->best_effort_timestamp;
            if (ts == AV_NOPTS_VALUE && d->cfg->synth_pts)
                last_pts = (double) num_frames * frame_duration; // raw elementary stream: no timestamps at all
            else
                last_pts = ts * av_q2d(d->stream->time_base);
            if (num_frames++ == 0) {
                first_pts = last_pts;
                d->t_first_decoded_ms = process_age_ms();
            }
            atomic_fetch_add(&d->decoded, 1);
            bad_streak = 0;
            push_frame(d, frame, last_pts - first_pts + base_pts, frame_duration);
            frame = av_frame_alloc();
            if (!frame)
                goto done;
        }

        if (ret == AVERROR(EAGAIN))
            continue;
        if (ret == AVERROR_EOF) {
            if (!d->cfg->loop || num_frames <= 1)
                goto done;
            // loop: seek to the start and keep timestamps increasing
            if (av_seek_frame(d->fmt, d->stream->index, 0, AVSEEK_FLAG_BACKWARD) < 0)
                goto done;
            avcodec_flush_buffers(d->codec);
            base_pts += last_pts - first_pts + frame_duration;
            num_frames = 0;
            flushing = false;
            continue;
        }
        fprintf(stderr, "perf0: avcodec_receive_frame: %s\n", av_err2str(ret));
        if (ret == AVERROR_INVALIDDATA && d->cfg->skip_bad_packets && ++bad_streak < 200)
            continue;
        goto done;
    }

done:
    pl_queue_push(d->queue, NULL); // EOF: lets the queue drain
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
    pl_queue_push(d->queue, NULL); // wake a blocked push_block
    WaitForSingleObject(d->thread, INFINITE);
    FILETIME created, exited, kernel, user;
    if (GetThreadTimes(d->thread, &created, &exited, &kernel, &user)) {
        ULARGE_INTEGER k = { .LowPart = kernel.dwLowDateTime, .HighPart = kernel.dwHighDateTime };
        ULARGE_INTEGER u = { .LowPart = user.dwLowDateTime, .HighPart = user.dwHighDateTime };
        d->thread_cpu_s = (double) (k.QuadPart + u.QuadPart) / 1e7;
    }
    CloseHandle(d->thread);
    d->thread = NULL;
}

// Decode as fast as possible with no rendering: headroom of the decoder alone.
bool decoder_bench(struct decoder *d, double seconds, uint32_t *frames_out, double *elapsed_s_out)
{
    AVPacket *packet = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    uint32_t frames = 0;
    double t_start = now_ms();
    bool ok = packet && frame;

    while (ok && now_ms() - t_start < seconds * 1e3) {
        int ret = av_read_frame(d->fmt, packet);
        if (ret == AVERROR_EOF) {
            if (av_seek_frame(d->fmt, d->stream->index, 0, AVSEEK_FLAG_BACKWARD) < 0)
                break;
            avcodec_flush_buffers(d->codec);
            continue;
        }
        if (ret < 0) {
            ok = false;
            break;
        }
        if (packet->stream_index == d->stream->index) {
            ret = avcodec_send_packet(d->codec, packet);
            if (ret < 0) {
                fprintf(stderr, "perf0: bench avcodec_send_packet: %s\n", av_err2str(ret));
                ok = false;
            }
            while (ok && avcodec_receive_frame(d->codec, frame) == 0) {
                frames++;
                av_frame_unref(frame);
            }
        }
        av_packet_unref(packet);
    }

    // Hardware decode is asynchronous: wait for the GPU to finish what was submitted.
    if (d->cfg->decode == DECODE_D3D11VA && d->g->d3d11) {
        ID3D11Device *dev = d->g->d3d11->device;
        ID3D11DeviceContext *ctx = NULL;
        ID3D11Query *q = NULL;
        D3D11_QUERY_DESC qd = { .Query = D3D11_QUERY_EVENT };
        ID3D11Device_GetImmediateContext(dev, &ctx);
        if (ctx && SUCCEEDED(ID3D11Device_CreateQuery(dev, &qd, &q))) {
            ID3D11DeviceContext_End(ctx, (ID3D11Asynchronous *) q);
            ID3D11DeviceContext_Flush(ctx);
            BOOL done = FALSE;
            while (ID3D11DeviceContext_GetData(ctx, (ID3D11Asynchronous *) q, &done, sizeof(done), 0) == S_FALSE ||
                   !done)
                Sleep(0);
            ID3D11Query_Release(q);
        }
        if (ctx)
            ID3D11DeviceContext_Release(ctx);
    } else if (d->cfg->decode == DECODE_VULKAN && d->g->vk) {
        PFN_vkDeviceWaitIdle wait_idle = (PFN_vkDeviceWaitIdle)
            d->g->vk->get_proc_addr(d->g->vk->instance, "vkDeviceWaitIdle");
        if (wait_idle)
            wait_idle(d->g->vk->device);
    }

    *frames_out = frames;
    *elapsed_s_out = (now_ms() - t_start) / 1e3;
    av_packet_free(&packet);
    av_frame_free(&frame);
    return ok;
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
    out->t_open_ms = d->t_open_ms;
    out->t_codec_ms = d->t_codec_ms;
    out->t_first_decoded_ms = d->t_first_decoded_ms;
    out->decoded = atomic_load(&d->decoded);
    out->mapped = atomic_load(&d->mapped);
    out->dropped = atomic_load(&d->dropped);
    out->decode_busy_s = d->decode_busy_ms / 1e3;
    out->decode_thread_cpu_s = d->thread_cpu_s;
}

void decoder_destroy(struct decoder **pd)
{
    struct decoder *d = *pd;
    if (!d)
        return;
    decoder_stop(d);
    avcodec_free_context(&d->codec); // before the GPU goes away: releases hw frames
    av_buffer_unref(&d->hw_device);
    avformat_close_input(&d->fmt);
    free(d);
    *pd = NULL;
}
