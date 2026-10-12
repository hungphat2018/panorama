// Result files, see pp-output.h.
#include "pp-output.h"

#include <direct.h>
#include <errno.h>
#include <string.h>

#include "pp-util.h"

bool pp_mkdirs(const char *dir)
{
    char path[512];
    snprintf(path, sizeof(path), "%s", dir);
    for (char *p = path + 1; *p; p++) {
        if (*p == '/' || *p == '\\') {
            char c = *p;
            *p = 0;
            if (_mkdir(path) != 0 && errno != EEXIST) {
                fprintf(stderr, "picture-path: cannot create %s\n", path);
                return false;
            }
            *p = c;
        }
    }
    if (_mkdir(path) != 0 && errno != EEXIST) {
        fprintf(stderr, "picture-path: cannot create %s\n", path);
        return false;
    }
    return true;
}

void pp_json_write_string(FILE *f, const char *s)
{
    fputc('"', f);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\')
            fputc('\\', f);
        if ((unsigned char) *s >= 0x20)
            fputc(*s, f);
    }
    fputc('"', f);
}

bool pp_output_write_canvas(const char *dir, const char *name, const struct pp_gpu *g, pl_tex canvas, const char *meta_json)
{
    if (!pp_mkdirs(dir))
        return false;
    int w = canvas->params.w, h = canvas->params.h;
    // libplacebo moves half-float textures through 32-bit float host buffers ("emulated" formats): the host texel is
    // the format's texel_size (16 bytes for rgba16f), so the file holds float32 RGBA whatever the GPU stores.
    size_t texel = canvas->params.format->texel_size;
    bool f32 = texel == 16;
    if (texel != 16 && texel != 8) {
        fprintf(stderr, "picture-path: unexpected canvas texel size %zu\n", texel);
        return false;
    }
    size_t bytes = (size_t) w * h * texel;
    void *buf = pp_xcalloc(bytes, 1);
    if (!pl_tex_download(g->gpu, pl_tex_transfer_params(.tex = canvas, .ptr = buf))) {
        fprintf(stderr, "picture-path: downloading the canvas of %s failed\n", name);
        free(buf);
        return false;
    }
    char path[600];
    snprintf(path, sizeof(path), "%s/%s.%s", dir, name, f32 ? "f32" : "f16");
    FILE *f = fopen(path, "wb");
    bool ok = f && fwrite(buf, 1, bytes, f) == bytes;
    if (f)
        fclose(f);
    free(buf);
    if (!ok) {
        fprintf(stderr, "picture-path: cannot write %s\n", path);
        return false;
    }
    snprintf(path, sizeof(path), "%s/%s.json", dir, name);
    f = fopen(path, "wb");
    if (!f) {
        fprintf(stderr, "picture-path: cannot write %s\n", path);
        return false;
    }
    fprintf(f, "{\"monitor\":\"%s\",\"width\":%d,\"height\":%d,\"format\":\"%s\",\"stored_as\":\"%s\",\"producer\":\"picture-path.exe\"%s%s}\n",
            name, w, h, f32 ? "rgba32f" : "rgba16f", canvas->params.format->name, meta_json && *meta_json ? "," : "",
            meta_json ? meta_json : "");
    fclose(f);
    return true;
}
