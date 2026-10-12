// Small helpers for the finish test driver, see test-util.h.
#include "test-util.h"

#include <direct.h>
#include <stdarg.h>
#include <windows.h>
#include <zlib.h>

// ---- checks -----------------------------------------------------------------------------------------------------

static int g_failures;

void tu_check(bool ok, const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    printf("  [%s] %s\n", ok ? "pass" : "FAIL", buf);
    if (!ok)
        g_failures++;
}

int tu_failures(void)
{
    return g_failures;
}

// ---- clock and statistics -----------------------------------------------------------------------------------------

double tu_now_ms(void)
{
    static LARGE_INTEGER freq;
    LARGE_INTEGER t;
    if (!freq.QuadPart)
        QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&t);
    return (double) t.QuadPart * 1000.0 / (double) freq.QuadPart;
}

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

void tu_sort(double *v, int n)
{
    qsort(v, (size_t) n, sizeof(double), cmp_double);
}

double tu_percentile(const double *sorted, int n, double p)
{
    if (n <= 0)
        return 0.0;
    double pos = p / 100.0 * (n - 1);
    int lo = (int) floor(pos), hi = (int) ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

// ---- PNG ----------------------------------------------------------------------------------------------------------

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t) (v >> 24);
    p[1] = (uint8_t) (v >> 16);
    p[2] = (uint8_t) (v >> 8);
    p[3] = (uint8_t) v;
}

static bool write_chunk(FILE *f, const char *type, const uint8_t *data, uint32_t n)
{
    uint8_t hdr[8];
    put32(hdr, n);
    memcpy(hdr + 4, type, 4);
    uint32_t crc = (uint32_t) crc32(0, hdr + 4, 4);
    if (n)
        crc = (uint32_t) crc32(crc, data, n);
    uint8_t tail[4];
    put32(tail, crc);
    return fwrite(hdr, 1, 8, f) == 8 && (!n || fwrite(data, 1, n, f) == n) && fwrite(tail, 1, 4, f) == 4;
}

bool tu_png_write_rgba8(const char *path, const uint8_t *rgba, int w, int h)
{
    FILE *f = fopen(path, "wb");
    if (!f)
        return false;
    size_t rowb = (size_t) w * 3;
    uint8_t *raw = malloc((rowb + 1) * (size_t) h);
    if (!raw) {
        fclose(f);
        return false;
    }
    for (int y = 0; y < h; y++) {
        uint8_t *row = raw + (rowb + 1) * (size_t) y;
        row[0] = 0;
        for (int x = 0; x < w; x++)
            memcpy(row + 1 + 3 * x, rgba + 4 * ((size_t) y * w + x), 3);
    }
    uLongf cap = compressBound((uLong) ((rowb + 1) * (size_t) h));
    uint8_t *z = malloc(cap);
    bool ok = z && compress2(z, &cap, raw, (uLong) ((rowb + 1) * (size_t) h), 3) == Z_OK;
    uint8_t ihdr[13];
    put32(ihdr, (uint32_t) w);
    put32(ihdr + 4, (uint32_t) h);
    ihdr[8] = 8;
    ihdr[9] = 2; // RGB
    ihdr[10] = ihdr[11] = ihdr[12] = 0;
    static const uint8_t sig[8] = { 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A };
    ok = ok && fwrite(sig, 1, 8, f) == 8 && write_chunk(f, "IHDR", ihdr, 13) && write_chunk(f, "IDAT", z, (uint32_t) cap) &&
         write_chunk(f, "IEND", NULL, 0);
    free(z);
    free(raw);
    fclose(f);
    return ok;
}

bool tu_png_write_linear(const char *path, const float *rgba, int w, int h, float exposure)
{
    uint8_t *out = malloc((size_t) w * h * 4);
    if (!out)
        return false;
    for (size_t i = 0; i < (size_t) w * h; i++) {
        for (int c = 0; c < 3; c++) {
            float v = rgba[4 * i + c] * exposure;
            v = v < 0 ? 0 : v > 1 ? 1 : v;
            v = v <= 0.0031308f ? v * 12.92f : 1.055f * powf(v, 1.0f / 2.4f) - 0.055f;
            out[4 * i + c] = (uint8_t) (v * 255.0f + 0.5f);
        }
        out[4 * i + 3] = 255;
    }
    bool ok = tu_png_write_rgba8(path, out, w, h);
    free(out);
    return ok;
}

// ---- textures -----------------------------------------------------------------------------------------------------

float *tu_read_f32(const struct fp_env *env, pl_tex tex, int w, int h)
{
    float *buf = malloc((size_t) w * h * 4 * sizeof(float));
    if (!buf)
        return NULL;
    if (!pl_tex_download(env->gpu, pl_tex_transfer_params(.tex = tex, .ptr = buf))) {
        free(buf);
        return NULL;
    }
    return buf;
}

uint8_t *tu_read_u8(const struct fp_env *env, pl_tex tex, int w, int h)
{
    uint8_t *buf = malloc((size_t) w * h * 4);
    if (!buf)
        return NULL;
    if (!pl_tex_download(env->gpu, pl_tex_transfer_params(.tex = tex, .ptr = buf))) {
        free(buf);
        return NULL;
    }
    // libplacebo hands back the texture's memory order. For a "bgra8" texture swap to RGBA.
    if (tex->params.format->name[0] == 'b') {
        for (size_t i = 0; i < (size_t) w * h; i++) {
            uint8_t t = buf[4 * i];
            buf[4 * i] = buf[4 * i + 2];
            buf[4 * i + 2] = t;
        }
    }
    return buf;
}

pl_tex tu_canvas(const struct fp_env *env, int w, int h, const char *what)
{
    pl_fmt fmt = pl_find_named_fmt(env->gpu, "rgba16f");
    if (!fmt) {
        fprintf(stderr, "finish: no rgba16f format (%s)\n", what);
        return NULL;
    }
    return pl_tex_create(env->gpu, pl_tex_params(.w = w, .h = h, .format = fmt, .sampleable = true, .renderable = true,
                                                  .blit_src = true, .blit_dst = true, .host_readable = true,
                                                  .host_writable = true));
}

pl_tex tu_target8(const struct fp_env *env, int w, int h, bool storable, const char *what)
{
    pl_fmt fmt = pl_find_named_fmt(env->gpu, "rgba8");
    if (!fmt) {
        fprintf(stderr, "finish: no rgba8 format (%s)\n", what);
        return NULL;
    }
    return pl_tex_create(env->gpu, pl_tex_params(.w = w, .h = h, .format = fmt, .sampleable = true, .renderable = true,
                                                  .storable = storable && (fmt->caps & PL_FMT_CAP_STORABLE),
                                                  .blit_src = true, .blit_dst = true, .host_readable = true,
                                                  .host_writable = true));
}

void tu_mkdir_p(const char *path)
{
    char tmp[512];
    snprintf(tmp, sizeof(tmp), "%s", path);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/' || *p == '\\') {
            char c = *p;
            *p = 0;
            _mkdir(tmp);
            *p = c;
        }
    }
    _mkdir(tmp);
}
