// frame-times: the performer's frame timestamps for a file, with no window, no GPU and no libplacebo.
//
// It repeats, line for line, what spikes/performer-p1/src/perf1-decode.c does to a decoded frame on the straight play
// path (no seek): the frame's time is frame->pts, or best_effort_timestamp when pts is missing, times the stream's
// time base as a double; the first frame the decoder returns defines time 0; a frame whose rebased time does not exceed
// the previous one is refused (counted as "nonmonotonic"). Software decoding uses the same open/stream selection
// (avformat_open_input, avformat_find_stream_info, av_find_best_stream) and the same decoder choice as the performer's
// headless mode. With --hw d3d11va the decoder is given its own D3D11VA device (the performer shares libplacebo's
// device; the frame times do not depend on that), so the hardware path can be compared too.
//
// Output (stdout, tab separated, one line per accepted frame, preceded by "#" header lines):
//   index  ts_ticks  secs_abs  secs_rebased        (doubles printed with %.17g, so they round-trip exactly)
// The worker's frame-table rule is checked against this output by verify-frame-table.py.
//
// Compile through spikes/film-analysis/native-checks/compile-native-checks.ps1 (MSYS2 UCRT64, the performer's FFmpeg).
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/hwcontext.h>
#include <libavutil/pixdesc.h>

static enum AVPixelFormat get_format_hw(AVCodecContext *ctx, const enum AVPixelFormat *fmts)
{
    (void) ctx;
    for (const enum AVPixelFormat *p = fmts; *p != AV_PIX_FMT_NONE; p++)
        if (*p == AV_PIX_FMT_D3D11)
            return *p;
    fprintf(stderr, "frame-times: the decoder does not offer D3D11 frames for this stream\n");
    return AV_PIX_FMT_NONE;
}

// For hardware decoding the performer picks the decoder that carries the matching hwaccel (find_decoder in perf1-decode.c).
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
        for (int i = 0; (hwcfg = avcodec_get_hw_config(c, i)); i++)
            if (hwcfg->device_type == type && (hwcfg->methods & AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX))
                return c;
    }
    return NULL;
}

struct state {
    bool have_first;
    double file_first_secs;
    double last_pts;
    long accepted, decoded, nonmonotonic;
};

static void handle_frame(struct state *st, const AVStream *stream, AVFrame *frame)
{
    int64_t ts = frame->pts != AV_NOPTS_VALUE ? frame->pts : frame->best_effort_timestamp;
    double secs = (double) ts * av_q2d(stream->time_base);
    if (!st->have_first) {
        st->file_first_secs = secs;
        st->have_first = true;
    }
    double pts = secs - st->file_first_secs;
    st->decoded++;
    if (!(pts > st->last_pts)) {
        st->nonmonotonic++;
        return;
    }
    st->last_pts = pts;
    printf("%ld\t%" PRId64 "\t%.17g\t%.17g\n", st->accepted, ts, secs, pts);
    st->accepted++;
}

int main(int argc, char **argv)
{
    const char *file = NULL;
    bool hw = false;
    int threads = 2; // the performer's headless value
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--hw") && i + 1 < argc) {
            if (strcmp(argv[++i], "d3d11va")) {
                fprintf(stderr, "frame-times: --hw takes d3d11va\n");
                return 2;
            }
            hw = true;
        } else if (!strcmp(argv[i], "--threads") && i + 1 < argc) {
            threads = atoi(argv[++i]);
        } else if (!file) {
            file = argv[i];
        } else {
            file = NULL;
            break;
        }
    }
    if (!file) {
        fprintf(stderr, "usage: frame-times FILE [--hw d3d11va] [--threads N]\n");
        return 2;
    }
    av_log_set_level(AV_LOG_WARNING);

    AVFormatContext *fmt = NULL;
    if (avformat_open_input(&fmt, file, NULL, NULL) != 0) {
        fprintf(stderr, "frame-times: avformat_open_input failed for '%s'\n", file);
        return 1;
    }
    if (avformat_find_stream_info(fmt, NULL) < 0) {
        fprintf(stderr, "frame-times: avformat_find_stream_info failed\n");
        return 1;
    }
    int idx = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (idx < 0) {
        fprintf(stderr, "frame-times: no video stream\n");
        return 1;
    }
    const AVStream *stream = fmt->streams[idx];

    enum AVHWDeviceType want = hw ? AV_HWDEVICE_TYPE_D3D11VA : AV_HWDEVICE_TYPE_NONE;
    const AVCodec *codec = find_decoder(stream->codecpar->codec_id, want);
    if (!codec) {
        fprintf(stderr, "frame-times: no decoder for codec id %d\n", stream->codecpar->codec_id);
        return 1;
    }
    AVCodecContext *ctx = avcodec_alloc_context3(codec);
    if (!ctx || avcodec_parameters_to_context(ctx, stream->codecpar) < 0) {
        fprintf(stderr, "frame-times: failed allocating the codec context\n");
        return 1;
    }
    AVBufferRef *hw_device = NULL;
    if (hw) {
        if (av_hwdevice_ctx_create(&hw_device, AV_HWDEVICE_TYPE_D3D11VA, NULL, NULL, 0) < 0) {
            fprintf(stderr, "frame-times: could not create a D3D11VA device\n");
            return 1;
        }
        ctx->hw_device_ctx = av_buffer_ref(hw_device);
        ctx->get_format = get_format_hw;
        ctx->extra_hw_frames = 8;
        ctx->thread_count = 1;
    } else {
        ctx->thread_count = threads;
    }
    ctx->export_side_data |= AV_CODEC_EXPORT_DATA_FILM_GRAIN;
    if (avcodec_open2(ctx, codec, NULL) < 0) {
        fprintf(stderr, "frame-times: avcodec_open2 failed\n");
        return 1;
    }

    printf("# file\t%s\n", file);
    printf("# decoder\t%s\t%s\n", codec->name, hw ? "d3d11va" : "software");
    printf("# time_base\t%d/%d\n", stream->time_base.num, stream->time_base.den);
    printf("# libavcodec\t%d.%d.%d\n", LIBAVCODEC_VERSION_MAJOR, LIBAVCODEC_VERSION_MINOR, LIBAVCODEC_VERSION_MICRO);
    printf("# columns\tindex\tts_ticks\tsecs_abs\tsecs_rebased\n");

    struct state st = { .last_pts = -1e300 };
    AVPacket *packet = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    bool draining = false;
    for (;;) {
        int ret = av_read_frame(fmt, packet);
        if (ret == 0) {
            if (packet->stream_index != stream->index) {
                av_packet_unref(packet);
                continue;
            }
            ret = avcodec_send_packet(ctx, packet);
            av_packet_unref(packet);
        } else if (ret == AVERROR_EOF) {
            ret = avcodec_send_packet(ctx, NULL);
            draining = true;
        } else {
            fprintf(stderr, "frame-times: av_read_frame: %s\n", av_err2str(ret));
            break;
        }
        if (ret < 0 && ret != AVERROR_EOF && ret != AVERROR(EAGAIN)) {
            fprintf(stderr, "frame-times: avcodec_send_packet: %s\n", av_err2str(ret));
            break;
        }
        while ((ret = avcodec_receive_frame(ctx, frame)) == 0) {
            handle_frame(&st, stream, frame);
            av_frame_unref(frame);
        }
        if (draining)
            break;
        if (ret != AVERROR(EAGAIN) && ret != 0 && ret != AVERROR_EOF) {
            fprintf(stderr, "frame-times: avcodec_receive_frame: %s\n", av_err2str(ret));
            break;
        }
    }
    printf("# decoded\t%ld\n# accepted\t%ld\n# nonmonotonic\t%ld\n", st.decoded, st.accepted, st.nonmonotonic);

    av_frame_free(&frame);
    av_packet_free(&packet);
    avcodec_free_context(&ctx);
    av_buffer_unref(&hw_device);
    avformat_close_input(&fmt);
    return 0;
}
