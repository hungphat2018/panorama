// FFmpeg demux + decode (software or D3D11VA) feeding one pl_queue per monitor. Derived from
// perf0-decode.c (adapted from libplacebo demos/plplay.c, CC0 / public domain).
//
// The file is decoded once. Every decoded frame is pushed to every queue as its own AVFrame reference, so with D3D11VA
// all monitors sample the same decoder texture (one device). A frame's timestamp is appended to the engine's frame
// index only after every queue has the frame, so the engine never picks a frame a queue does not hold.
//
// Seeking follows the engine's supply handshake (perf1-engine.h, struct supply_sync). The render thread owns the GPU
// and so resets the queues; this thread must not hold a stale frame while that happens:
//   1. the engine raises want_gen with the film time to seek to;
//   2. this thread drops the frame it holds, flushes the codec, and answers parked_gen = want_gen;
//   3. the engine empties the frame index, resets every queue, and answers reset_gen = want_gen;
//   4. this thread seeks the demuxer to the keyframe at or before the time, decodes forward without queueing, and
//      queues the frame that contains the time (the last one at or before it) and the next one;
//   5. it announces ready_gen = want_gen with the first frame's timestamp and the time.
// A newer request at any step restarts the sequence for the newest generation. Pushes use short timeouts so a
// blocked push notices a request within a few milliseconds.
#define COBJMACROS
#include "perf1.h"

#include <math.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/cpu.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_d3d11va.h>
#include <libavutil/pixdesc.h>
#include <libplacebo/utils/libav.h>

#include <d3d11.h>
#include <d3d10.h>

#define HW_EXTRA_FRAMES 8 // decoder surfaces kept beyond the DPB: every queue holds a few frames
#define PUSH_TIMEOUT_NS 5000000ULL

struct decoder {
    const struct cfg *cfg;
    struct gpu_ctx *g;
    struct engine *engine;
    pl_queue queues[MAX_MONITORS];
    int nqueues;

    AVFormatContext *fmt;
    AVCodecContext *codec;
    const AVStream *stream;
    AVBufferRef *hw_device;

    HANDLE thread;
    atomic_bool exit_thread;
    atomic_bool at_eof;
    atomic_uint decoded, mapped, dropped, pushed, seeks, nonmonotonic;

    char codec_name[64], pix_fmt_name[64], hw_fmt_name[64];
};

// ---- D3D11VA import: decoder array texture -> pl_tex planes -------------------------------

// Bit layout of a hardware frame's software format (P010 is 10 bits stored in the top of 16:
// sample depth 16, colour depth 10, shift 6).
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
        fprintf(stderr, "perf1: D3D11VA sw_format %s not handled\n", av_get_pix_fmt_name(hwfc->sw_format));
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
            fprintf(stderr, "perf1: pl_d3d11_wrap failed for plane %d (bind flags 0x%x)\n", p, td.BindFlags);
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
        fprintf(stderr, "perf1: failed mapping AVFrame\n");
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

// The decoder shares libplacebo's ID3D11Device so decoded textures are sampled with no copy. The decoder
// thread and the render thread share the immediate context: the device serialises its own calls.
static AVBufferRef *make_d3d11va_device(ID3D11Device *dev)
{
    ID3D10Multithread *mt = NULL;
    if (SUCCEEDED(ID3D11Device_QueryInterface(dev, &IID_ID3D10Multithread, (void **) &mt))) {
        ID3D10Multithread_SetMultithreadProtected(mt, TRUE);
        ID3D10Multithread_Release(mt);
    }

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
        if (*p != AV_PIX_FMT_D3D11)
            continue;

        AVBufferRef *frames = NULL;
        int r = avcodec_get_hw_frames_parameters(ctx, ctx->hw_device_ctx, *p, &frames);
        if (r < 0) {
            fprintf(stderr, "perf1: avcodec_get_hw_frames_parameters: %s\n", av_err2str(r));
            return AV_PIX_FMT_NONE;
        }
        // The decoder's array texture must also be sampleable, so planes can be wrapped as shader views.
        AVHWFramesContext *fc = (AVHWFramesContext *) frames->data;
        ((AVD3D11VAFramesContext *) fc->hwctx)->BindFlags |= D3D11_BIND_SHADER_RESOURCE;
        r = av_hwframe_ctx_init(frames);
        if (r < 0) {
            fprintf(stderr, "perf1: av_hwframe_ctx_init: %s\n", av_err2str(r));
            av_buffer_unref(&frames);
            return AV_PIX_FMT_NONE;
        }

        fc = (AVHWFramesContext *) frames->data;
        snprintf(d->hw_fmt_name, sizeof(d->hw_fmt_name), "%s/%s",
                 av_get_pix_fmt_name(*p), av_get_pix_fmt_name(fc->sw_format));
        av_buffer_unref(&ctx->hw_frames_ctx);
        ctx->hw_frames_ctx = frames;
        return *p;
    }
    fprintf(stderr, "perf1: decoder does not offer d3d11 frames for this stream\n");
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
        fprintf(stderr, "perf1: avformat_open_input failed for '%s'\n", filename);
        return false;
    }
    if (avformat_find_stream_info(d->fmt, NULL) < 0) {
        fprintf(stderr, "perf1: avformat_find_stream_info failed\n");
        return false;
    }
    int idx = av_find_best_stream(d->fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (idx < 0) {
        fprintf(stderr, "perf1: no video stream\n");
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
                                                             : AV_HWDEVICE_TYPE_NONE;
    const AVCodec *codec = find_decoder(d->stream->codecpar->codec_id, want);
    if (!codec) {
        fprintf(stderr, "perf1: no decoder for codec id %d\n", d->stream->codecpar->codec_id);
        return false;
    }
    snprintf(d->codec_name, sizeof(d->codec_name), "%s", codec->name);

    d->codec = avcodec_alloc_context3(codec);
    if (!d->codec || avcodec_parameters_to_context(d->codec, d->stream->codecpar) < 0) {
        fprintf(stderr, "perf1: failed allocating the codec context\n");
        return false;
    }

    if (cfg->decode == DECODE_D3D11VA) {
        bool supported = false;
        const AVCodecHWConfig *hwcfg;
        for (int i = 0; (hwcfg = avcodec_get_hw_config(codec, i)); i++) {
            if (hwcfg->device_type == AV_HWDEVICE_TYPE_D3D11VA && hwcfg->pix_fmt == AV_PIX_FMT_D3D11 &&
                (hwcfg->methods & AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX))
            {
                supported = true;
                break;
            }
        }
        if (!supported) {
            fprintf(stderr, "perf1: codec %s has no d3d11va hw config in this FFmpeg\n", codec->name);
            return false;
        }

        d->hw_device = make_d3d11va_device(d->g->d3d11->device);
        if (!d->hw_device) {
            fprintf(stderr, "perf1: failed creating the d3d11va hw device\n");
            return false;
        }
        d->codec->hw_device_ctx = av_buffer_ref(d->hw_device);
        d->codec->get_format = get_format_hw;
        d->codec->extra_hw_frames = HW_EXTRA_FRAMES;
        d->codec->opaque = d;
        d->codec->thread_count = 1; // frame threading does not apply to hwaccel decoding
    } else {
        // Headless runs use libplacebo's dummy GPU, which has no host-mapped buffers to decode into, and are tests that
        // must stay light: two decoding threads.
        d->codec->thread_count = cfg->headless_hz ? 2 : FFMIN(av_cpu_count() + 1, 16);
        if (!cfg->headless_hz) {
            d->codec->get_buffer2 = pl_get_buffer2;
            d->codec->opaque = &d->g->gpu;
        }
    }
    d->codec->export_side_data |= AV_CODEC_EXPORT_DATA_FILM_GRAIN;

    if (avcodec_open2(d->codec, codec, NULL) < 0) {
        fprintf(stderr, "perf1: avcodec_open2 failed\n");
        return false;
    }
    return true;
}

struct decoder *decoder_open(const struct cfg *cfg, struct gpu_ctx *g, pl_queue *queues, int nqueues, struct engine *engine)
{
    if (nqueues < 1 || nqueues > MAX_MONITORS)
        return NULL;
    struct decoder *d = calloc(1, sizeof(*d));
    if (!d)
        return NULL;
    d->cfg = cfg;
    d->g = g;
    d->engine = engine;
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

// State private to the decode thread.
struct dstate {
    int gen;                    // generation being served (0 = the film's start)
    bool have_first;
    double file_first_pts;      // seconds: the rebased timeline starts at the first frame of the file
    double frame_duration;
    double last_pts;            // last timestamp seen in this generation (rebased); frames must increase
    bool seeking;               // decoding forward to seek_to without queueing
    double seek_to;             // rebased seconds
    AVFrame *held;              // seeking: the newest frame at or before seek_to, not queued yet
    double held_pts;
    int pushed;                 // frames queued since the generation began
    double first_pushed_pts;
    bool announced;             // ready_gen was written for this generation
    bool eof;                   // the demuxer and the codec are drained; waiting for a seek or the end
};

static bool should_abort(struct decoder *d, const struct dstate *st)
{
    return atomic_load(&d->exit_thread) ||
           atomic_load_explicit(&d->engine->sync.want_gen, memory_order_acquire) != st->gen;
}

static void push_eof(struct decoder *d)
{
    for (int i = 0; i < d->nqueues; i++)
        pl_queue_push(d->queues[i], NULL);
}

// Queues the frame in every monitor's queue (its own AVFrame reference each) and records its timestamp. Takes the
// frame; on a false return (a newer request arrived while a queue was full) the frame has been released.
static bool push_frame(struct decoder *d, struct dstate *st, AVFrame *frame, double pts)
{
    AVFrame *own[MAX_MONITORS] = {0};
    for (int i = 0; i < d->nqueues; i++)
        own[i] = i == d->nqueues - 1 ? frame : av_frame_clone(frame);
    bool ok = true;
    for (int i = 0; i < d->nqueues && ok; i++) {
        AVFrame *f = own[i];
        if (!f)
            continue; // out of memory for a clone: that queue misses the frame (the log shows a mismatch)
        f->opaque = d;
        struct pl_source_frame src = {
            .pts        = pts,
            .duration   = st->frame_duration,
            .map        = map_frame,
            .unmap      = unmap_frame,
            .discard    = discard_frame,
            .frame_data = f,
        };
        for (;;) {
            if (pl_queue_push_block(d->queues[i], PUSH_TIMEOUT_NS, &src)) {
                own[i] = NULL; // the queue owns it now
                break;
            }
            if (should_abort(d, st)) {
                ok = false;
                break;
            }
        }
    }
    if (!ok) {
        for (int i = 0; i < d->nqueues; i++)
            if (own[i])
                av_frame_free(&own[i]);
        return false;
    }
    findex_append(&d->engine->findex, st->gen, pts); // after every queue has it
    atomic_fetch_add(&d->pushed, 1);
    if (!st->pushed++)
        st->first_pushed_pts = pts;
    st->last_pts = pts;
    return true;
}

// Tells the engine the first frames of the generation are queued.
static void announce_ready(struct decoder *d, struct dstate *st, double first_pts)
{
    if (st->announced || st->gen == 0)
        return;
    st->announced = true;
    struct supply_sync *s = &d->engine->sync;
    atomic_store_explicit(&s->ready_pts, first_pts, memory_order_relaxed);
    atomic_store_explicit(&s->ready_time, now_s(), memory_order_relaxed);
    atomic_store_explicit(&s->ready_gen, st->gen, memory_order_release);
}

static void release_held(struct dstate *st)
{
    if (st->held)
        av_frame_free(&st->held);
}

// A decoded frame, in presentation order. Consumes the frame.
static void handle_frame(struct decoder *d, struct dstate *st, AVFrame *frame)
{
    int64_t ts = frame->pts != AV_NOPTS_VALUE ? frame->pts : frame->best_effort_timestamp;
    double secs = (double) ts * av_q2d(d->stream->time_base);
    if (!st->have_first) {
        st->file_first_pts = secs;
        st->have_first = true;
    }
    double pts = secs - st->file_first_pts;
    atomic_fetch_add(&d->decoded, 1);
    if (!(pts > st->last_pts)) {
        atomic_fetch_add(&d->nonmonotonic, 1); // presentation order must increase: refuse the frame
        av_frame_free(&frame);
        return;
    }
    st->last_pts = pts;
    if (!st->seeking) {
        if (push_frame(d, st, frame, pts) && st->pushed >= 2)
            announce_ready(d, st, st->first_pushed_pts);
        return;
    }
    if (pts <= st->seek_to) {
        // a candidate for "the frame that contains the sought time": the newest one at or before it
        release_held(st);
        st->held = frame;
        st->held_pts = pts;
        return;
    }
    // first frame after the sought time: queue the one that contains it (or this one if nothing came before)
    st->seeking = false;
    st->last_pts = -1e300;
    if (st->held) {
        AVFrame *h = st->held;
        st->held = NULL;
        if (!push_frame(d, st, h, st->held_pts)) {
            av_frame_free(&frame);
            return;
        }
    }
    if (push_frame(d, st, frame, pts) && st->pushed >= 2)
        announce_ready(d, st, st->first_pushed_pts);
}

// The demuxer is exhausted and the codec drained. A seek that never found a frame past its time still has the last
// frame before it: queue that, announce, and tell the queues the stream ended.
static void finish_stream(struct decoder *d, struct dstate *st)
{
    if (st->seeking) {
        st->seeking = false;
        st->last_pts = -1e300;
        if (st->held) {
            AVFrame *h = st->held;
            st->held = NULL;
            push_frame(d, st, h, st->held_pts);
        }
    }
    if (st->gen != 0 && !st->announced)
        announce_ready(d, st, st->pushed ? st->first_pushed_pts : NAN); // NaN: the seek produced no frame
    push_eof(d);
    st->eof = true;
    atomic_store(&d->at_eof, true);
}

// A new generation was requested. Returns true with a seek in progress, false if the request was overtaken or the
// program is ending.
static bool begin_seek(struct decoder *d, struct dstate *st, int gen)
{
    struct supply_sync *s = &d->engine->sync;
    st->gen = gen;
    release_held(st);
    avcodec_flush_buffers(d->codec);
    st->eof = false;
    atomic_store(&d->at_eof, false);
    st->announced = false;
    st->pushed = 0;
    st->last_pts = -1e300;
    atomic_store_explicit(&s->parked_gen, gen, memory_order_release);
    for (;;) { // the engine resets the queues and the frame index, then releases us
        if (atomic_load(&d->exit_thread))
            return false;
        if (atomic_load_explicit(&s->want_gen, memory_order_acquire) != gen)
            return false;
        if (atomic_load_explicit(&s->reset_gen, memory_order_acquire) == gen)
            break;
        Sleep(1);
    }
    double to = atomic_load_explicit(&s->seek_target, memory_order_relaxed);
    int64_t ts = (int64_t) llround((to + st->file_first_pts) / av_q2d(d->stream->time_base));
    atomic_fetch_add(&d->seeks, 1);
    if (av_seek_frame(d->fmt, d->stream->index, ts, AVSEEK_FLAG_BACKWARD) < 0) {
        fprintf(stderr, "perf1: seek to %.3f s failed\n", to);
        finish_stream(d, st); // announces NaN: the engine goes back to paused
        return false;
    }
    avcodec_flush_buffers(d->codec);
    st->seeking = true;
    st->seek_to = to;
    return true;
}

static DWORD WINAPI decode_thread(LPVOID arg)
{
    struct decoder *d = arg;
    int ret;
    AVPacket *packet = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    struct dstate st = { .last_pts = -1e300 };
    if (!frame || !packet)
        goto done;
    st.frame_duration = d->stream->avg_frame_rate.num && d->stream->avg_frame_rate.den
                      ? av_q2d(av_inv_q(d->stream->avg_frame_rate)) : 1.0 / 24.0;

    while (!atomic_load(&d->exit_thread)) {
        int want = atomic_load_explicit(&d->engine->sync.want_gen, memory_order_acquire);
        if (want != st.gen) {
            begin_seek(d, &st, want);
            continue;
        }
        if (st.eof) {
            Sleep(2); // parked at the end of the file until a seek or the end of the run
            continue;
        }

        bool draining = false;
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
            draining = true;
        } else {
            fprintf(stderr, "perf1: av_read_frame: %s\n", av_err2str(ret));
            finish_stream(d, &st);
            continue;
        }
        if (ret < 0 && ret != AVERROR_EOF && ret != AVERROR(EAGAIN)) {
            fprintf(stderr, "perf1: avcodec_send_packet: %s\n", av_err2str(ret));
            finish_stream(d, &st);
            continue;
        }

        for (;;) {
            ret = avcodec_receive_frame(d->codec, frame);
            if (ret != 0)
                break;
            handle_frame(d, &st, frame);
            frame = av_frame_alloc();
            if (!frame)
                goto done;
            if (should_abort(d, &st))
                break;
        }
        if (should_abort(d, &st))
            continue; // a seek (or the end) was requested: the loop top deals with it
        if (draining) {
            finish_stream(d, &st);
        } else if (ret != AVERROR(EAGAIN) && ret != 0 && ret != AVERROR_EOF) {
            fprintf(stderr, "perf1: avcodec_receive_frame: %s\n", av_err2str(ret));
            finish_stream(d, &st);
        }
    }

done:
    release_held(&st);
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
    out->pushed = atomic_load(&d->pushed);
    out->seeks = atomic_load(&d->seeks);
    out->nonmonotonic = atomic_load(&d->nonmonotonic);
    out->eof = atomic_load(&d->at_eof);
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
