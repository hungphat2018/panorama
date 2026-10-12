// dovi-probe: reads the Dolby Vision metadata FFmpeg attaches to decoded frames and prints it, to answer: are the level 5
// active-area offsets and scene_refresh_flag really delivered as frame side data, and does the picture agree with them?
//
// For every decoded frame (or only keyframes with --keyframes) of the best video stream it prints one line:
//   frame  pts_ticks  key  scene_refresh  dm_metadata_id  n_ext_blocks  L1(min,avg,max PQ)  L5(left,right,top,bottom)  levels
// and, with --luma, the measured active picture of the frame: the first and last row and column whose mean luma is above
// the black level plus a margin (limited-range 10-bit code values; works for yuv420p10le and yuv420p), so the L5 rectangle
// can be compared with the picture.  Software decoding only (the metadata is attached by the HEVC parser, not by the GPU).
//
// usage: dovi-probe FILE [--max N] [--keyframes] [--luma]
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/dovi_meta.h>
#include <libavutil/pixdesc.h>

// Active picture by row/column means of the luma plane, 8 or 10/16-bit samples. Black level and margin in 8-bit units.
static void measure_active(const AVFrame *f, int *x0, int *x1, int *y0, int *y1)
{
    const AVPixFmtDescriptor *d = av_pix_fmt_desc_get(f->format);
    int depth = d ? d->comp[0].depth : 8;
    int shift = depth - 8;
    bool full = f->color_range == AVCOL_RANGE_JPEG;
    double thr = ((full ? 0.0 : 16.0) + 6.0) * (double) (1 << shift);   // mean-luma threshold: black + 6 (8-bit units)
    int w = f->width, h = f->height;
    double *row = calloc((size_t) h, sizeof(double)), *col = calloc((size_t) w, sizeof(double));
    for (int y = 0; y < h; y++) {
        const uint8_t *p = f->data[0] + (size_t) y * f->linesize[0];
        for (int x = 0; x < w; x++) {
            double v = depth > 8 ? (double) ((const uint16_t *) p)[x] : (double) p[x];
            row[y] += v;
            col[x] += v;
        }
    }
    *y0 = h; *y1 = -1; *x0 = w; *x1 = -1;
    for (int y = 0; y < h; y++)
        if (row[y] / w > thr) { if (y < *y0) *y0 = y; *y1 = y; }
    for (int x = 0; x < w; x++)
        if (col[x] / h > thr) { if (x < *x0) *x0 = x; *x1 = x; }
    free(row);
    free(col);
}

int main(int argc, char **argv)
{
    const char *file = NULL;
    int max_frames = 1 << 30;
    bool only_key = false, luma = false;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--max") && i + 1 < argc) max_frames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--keyframes")) only_key = true;
        else if (!strcmp(argv[i], "--luma")) luma = true;
        else if (!file) file = argv[i];
        else { file = NULL; break; }
    }
    if (!file) {
        fprintf(stderr, "usage: dovi-probe FILE [--max N] [--keyframes] [--luma]\n");
        return 2;
    }
    av_log_set_level(AV_LOG_ERROR);
    AVFormatContext *fmt = NULL;
    if (avformat_open_input(&fmt, file, NULL, NULL) != 0 || avformat_find_stream_info(fmt, NULL) < 0) {
        fprintf(stderr, "dovi-probe: cannot open %s\n", file);
        return 1;
    }
    int idx = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (idx < 0) { fprintf(stderr, "dovi-probe: no video stream\n"); return 1; }
    const AVStream *st = fmt->streams[idx];
    const AVCodec *codec = avcodec_find_decoder(st->codecpar->codec_id);
    AVCodecContext *ctx = avcodec_alloc_context3(codec);
    avcodec_parameters_to_context(ctx, st->codecpar);
    ctx->thread_count = 4;
    if (only_key)
        ctx->skip_frame = AVDISCARD_NONKEY;
    if (avcodec_open2(ctx, codec, NULL) < 0) { fprintf(stderr, "dovi-probe: avcodec_open2 failed\n"); return 1; }

    const AVPacketSideData *sd = av_packet_side_data_get(st->codecpar->coded_side_data, st->codecpar->nb_coded_side_data,
                                                         AV_PKT_DATA_DOVI_CONF);
    if (sd) {
        const AVDOVIDecoderConfigurationRecord *c = (const AVDOVIDecoderConfigurationRecord *) sd->data;
        printf("# stream DOVI configuration: profile %d level %d rpu %d el %d bl %d compat %d\n", c->dv_profile, c->dv_level,
               c->rpu_present_flag, c->el_present_flag, c->bl_present_flag, c->dv_bl_signal_compatibility_id);
    } else {
        printf("# stream has no DOVI configuration record\n");
    }
    printf("# %dx%d %s time_base %d/%d\n", st->codecpar->width, st->codecpar->height,
           av_get_pix_fmt_name(st->codecpar->format), st->time_base.num, st->time_base.den);

    AVPacket *pkt = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    int n = 0, with_meta = 0, with_l5 = 0, scene_refresh = 0;
    bool draining = false;
    while (n < max_frames) {
        int ret = av_read_frame(fmt, pkt);
        if (ret == 0) {
            if (pkt->stream_index != idx) { av_packet_unref(pkt); continue; }
            avcodec_send_packet(ctx, pkt);
            av_packet_unref(pkt);
        } else {
            avcodec_send_packet(ctx, NULL);
            draining = true;
        }
        while (n < max_frames && avcodec_receive_frame(ctx, frame) == 0) {
            const AVFrameSideData *fsd = av_frame_get_side_data(frame, AV_FRAME_DATA_DOVI_METADATA);
            printf("frame %d pts %" PRId64 " key %d", n, frame->pts, (frame->flags & AV_FRAME_FLAG_KEY) ? 1 : 0);
            if (fsd) {
                const AVDOVIMetadata *md = (const AVDOVIMetadata *) fsd->data;
                const AVDOVIColorMetadata *col = av_dovi_get_color(md);
                with_meta++;
                scene_refresh += col->scene_refresh_flag;
                printf(" scene_refresh %d dm_id %d ext_blocks %d", col->scene_refresh_flag, col->dm_metadata_id,
                       md->num_ext_blocks);
                const AVDOVIDmData *l1 = av_dovi_find_level(md, 1), *l5 = av_dovi_find_level(md, 5);
                if (l1) printf(" L1(min %u avg %u max %u)", l1->l1.min_pq, l1->l1.avg_pq, l1->l1.max_pq);
                if (l5) {
                    with_l5++;
                    printf(" L5(left %u right %u top %u bottom %u)", l5->l5.left_offset, l5->l5.right_offset, l5->l5.top_offset,
                           l5->l5.bottom_offset);
                }
                printf(" levels[");
                for (int i = 0; i < md->num_ext_blocks; i++)
                    printf("%s%d", i ? "," : "", av_dovi_get_ext(md, i)->level);
                printf("]");
            } else {
                printf(" no_dovi_metadata");
            }
            if (luma) {
                int x0, x1, y0, y1;
                measure_active(frame, &x0, &x1, &y0, &y1);
                printf(" picture(x %d..%d y %d..%d of %dx%d)", x0, x1, y0, y1, frame->width, frame->height);
            }
            printf("\n");
            av_frame_unref(frame);
            n++;
        }
        if (draining)
            break;
    }
    printf("# frames %d, with Dolby Vision metadata %d, with level 5 %d, scene_refresh_flag set on %d\n", n, with_meta,
           with_l5, scene_refresh);
    av_frame_free(&frame);
    av_packet_free(&pkt);
    avcodec_free_context(&ctx);
    avformat_close_input(&fmt);
    return 0;
}
