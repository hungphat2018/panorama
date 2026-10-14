// The side store, player end, see perf1-ai-sides-store.h.
#include "perf1-ai-sides-store.h"

#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#define READ_TRIES 64

static bool fail(char *err, size_t n, const char *what)
{
    if (err && n)
        snprintf(err, n, "%s", what);
    return false;
}

static uint32_t load_acquire_u32(const volatile uint32_t *p)
{
    return atomic_load_explicit((const _Atomic uint32_t *) p, memory_order_acquire);
}

bool ai_store_open(struct ai_store *s, const char *path, char *err, size_t err_size)
{
    memset(s, 0, sizeof(*s));
    WCHAR wpath[MAX_PATH * 2];
    if (!MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, (int) (sizeof(wpath) / sizeof(wpath[0]))))
        return fail(err, err_size, "the path is not valid UTF-8 or too long");
    HANDLE f = CreateFileW(wpath, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE)
        return fail(err, err_size, GetLastError() == ERROR_FILE_NOT_FOUND ? "no store file yet" : "the store file cannot be opened");
    LARGE_INTEGER size;
    if (!GetFileSizeEx(f, &size) || size.QuadPart < (LONGLONG) AI_STORE_HEADER_BYTES) {
        CloseHandle(f);
        return fail(err, err_size, "the store file is shorter than its header (the worker is still creating it)");
    }
    HANDLE m = CreateFileMappingW(f, NULL, PAGE_READWRITE, 0, 0, NULL);
    if (!m) {
        CloseHandle(f);
        return fail(err, err_size, "the store file cannot be mapped");
    }
    uint8_t *base = MapViewOfFile(m, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    if (!base) {
        CloseHandle(m);
        CloseHandle(f);
        return fail(err, err_size, "the store file cannot be mapped into memory");
    }
    s->file = f;
    s->mapping = m;
    s->base = base;
    s->size = (uint64_t) size.QuadPart;
    const struct ai_layout_block *lay = (const struct ai_layout_block *) base;
    const char *bad = NULL;
    if (memcmp(lay->magic, AI_STORE_MAGIC, 8))
        bad = "not a side store (magic)";
    else if (lay->schema_major != AI_STORE_SCHEMA_MAJOR)
        bad = "a side store of another schema major";
    else if (lay->header_bytes != AI_STORE_HEADER_BYTES || lay->frame_rec_bytes != sizeof(struct ai_frame_rec) ||
             lay->shot_rec_bytes != sizeof(struct ai_shot_rec) || lay->canvas_rec_bytes != sizeof(struct ai_canvas_rec))
        bad = "record sizes differ from this reader's";
    else if (lay->file_bytes != s->size)
        bad = "the file size differs from the layout's";
    else if (lay->off_frames + (uint64_t) lay->max_frames * sizeof(struct ai_frame_rec) > s->size ||
             lay->off_shots + (uint64_t) lay->max_shots * sizeof(struct ai_shot_rec) > s->size ||
             lay->off_canvases + (uint64_t) lay->heap_slots * sizeof(struct ai_canvas_rec) > s->size ||
             lay->off_heap + (uint64_t) lay->heap_slots * lay->heap_slot_bytes > s->size)
        bad = "a table lies outside the file";
    if (bad) {
        ai_store_close(s);
        return fail(err, err_size, bad);
    }
    s->layout = lay;
    s->worker = (volatile struct ai_worker_block *) (base + 256);
    s->player = (volatile struct ai_player_block *) (base + 512);
    s->frames = (const struct ai_frame_rec *) (base + lay->off_frames);
    s->shots = (const struct ai_shot_rec *) (base + lay->off_shots);
    s->canvases = (const struct ai_canvas_rec *) (base + lay->off_canvases);
    s->heap = base + lay->off_heap;
    s->max_frames = lay->max_frames;
    s->max_shots = lay->max_shots;
    s->heap_slots = lay->heap_slots;
    s->heap_slot_bytes = lay->heap_slot_bytes;
    s->store_uid = lay->store_uid;
    return true;
}

void ai_store_close(struct ai_store *s)
{
    if (s->base)
        UnmapViewOfFile(s->base);
    if (s->mapping)
        CloseHandle((HANDLE) s->mapping);
    if (s->file)
        CloseHandle((HANDLE) s->file);
    memset(s, 0, sizeof(*s));
}

uint32_t ai_store_frames_published(const struct ai_store *s)
{
    uint32_t n = load_acquire_u32(&s->worker->frames_published);
    return n < s->max_frames ? n : s->max_frames;
}

uint32_t ai_store_shots_published(const struct ai_store *s)
{
    uint32_t n = load_acquire_u32(&s->worker->shots_published);
    return n < s->max_shots ? n : s->max_shots;
}

uint64_t ai_store_worker_beat(const struct ai_store *s)
{
    return atomic_load_explicit((const _Atomic uint64_t *) &s->worker->beat, memory_order_acquire);
}

int ai_store_find_frame(const struct ai_store *s, double pts, int hint)
{
    int n = (int) ai_store_frames_published(s);
    double key = pts + 1e-6;
    if (n <= 0 || s->frames[0].pts > key)
        return -1;
    if (hint >= 0 && hint < n && s->frames[hint].pts <= key) {
        int i = hint;
        for (int k = 0; k < 16 && i + 1 < n && s->frames[i + 1].pts <= key; k++)
            i++;
        if (i + 1 >= n || s->frames[i + 1].pts > key)
            return i;
    }
    int lo = 0, hi = n - 1; // the last row whose time is at most key: frames[lo].pts <= key holds throughout
    while (lo < hi) {
        int mid = lo + (hi - lo + 1) / 2;
        if (s->frames[mid].pts <= key)
            lo = mid;
        else
            hi = mid - 1;
    }
    return lo;
}

// A copy of `size` bytes at `src` whose first word is its sequence counter, kept only if that counter was even and did not
// change across the copy (the Boehm seqlock reader).
static bool copy_under_counter(const void *src, void *dst, size_t size)
{
    const volatile uint32_t *seq = (const volatile uint32_t *) src;
    for (int t = 0; t < READ_TRIES; t++) {
        uint32_t s0 = load_acquire_u32(seq);
        if (s0 & 1u) {
            YieldProcessor();
            continue;
        }
        memcpy(dst, (const void *) src, size);
        atomic_thread_fence(memory_order_acquire);
        if (atomic_load_explicit((const _Atomic uint32_t *) seq, memory_order_relaxed) == s0)
            return true;
    }
    return false;
}

bool ai_store_read_frame(const struct ai_store *s, uint32_t i, struct ai_frame_rec *out)
{
    if (i >= ai_store_frames_published(s))
        return false;
    // the counter is the record's third word, not its first: copy the record, then check seq around it
    const struct ai_frame_rec *src = &s->frames[i];
    for (int t = 0; t < READ_TRIES; t++) {
        uint32_t s0 = load_acquire_u32(&src->seq);
        if (s0 & 1u) {
            YieldProcessor();
            continue;
        }
        memcpy(out, src, sizeof(*out));
        atomic_thread_fence(memory_order_acquire);
        if (atomic_load_explicit((const _Atomic uint32_t *) &src->seq, memory_order_relaxed) == s0)
            return true;
    }
    return false;
}

bool ai_store_read_shot(const struct ai_store *s, uint32_t i, struct ai_shot_rec *out)
{
    return i < ai_store_shots_published(s) && copy_under_counter(&s->shots[i], out, sizeof(*out));
}

bool ai_store_read_canvas(const struct ai_store *s, uint32_t slot, struct ai_canvas_rec *out)
{
    return slot < s->heap_slots && copy_under_counter(&s->canvases[slot], out, sizeof(*out));
}

bool ai_store_canvas_still(const struct ai_store *s, uint32_t slot, uint32_t gen)
{
    atomic_thread_fence(memory_order_acquire); // the pixel copy happens before this check
    return slot < s->heap_slots && load_acquire_u32(&s->canvases[slot].gen) == gen;
}

const uint8_t *ai_store_slot_pixels(const struct ai_store *s, uint32_t slot)
{
    return slot < s->heap_slots ? s->heap + (uint64_t) slot * s->heap_slot_bytes : NULL;
}

bool ai_store_read_page(const struct ai_store *s, uint32_t slot, const struct ai_canvas_rec *c, struct ai_patch_page *hdr,
                        struct ai_patch_rec **recs)
{
    *recs = NULL;
    if (slot >= s->heap_slots || c->kind != AI_KIND_PATCH_PAGE || c->state != AI_CANVAS_COMPLETE || (c->gen & 1u))
        return false;
    uint64_t table = sizeof(struct ai_patch_page) + (uint64_t) c->n_patches * sizeof(struct ai_patch_rec);
    if (c->n_patches == 0 || c->n_patches > 65536 || table > c->page_header_bytes || c->tex_w == 0 || c->tex_h == 0 ||
        c->tex_w > 16384 || c->tex_h > 16384 || c->row_bytes < c->tex_w * 4u || c->bytes != (uint64_t) c->tex_h * c->row_bytes ||
        (uint64_t) c->page_header_bytes + c->bytes > s->heap_slot_bytes)
        return false;
    const uint8_t *base = ai_store_slot_pixels(s, slot);
    struct ai_patch_rec *r = malloc((size_t) c->n_patches * sizeof(*r));
    if (!r)
        return false;
    memcpy(hdr, base, sizeof(*hdr));
    memcpy(r, base + sizeof(*hdr), (size_t) c->n_patches * sizeof(*r));
    bool ok = ai_store_canvas_still(s, slot, c->gen) && !memcmp(hdr->magic, AI_PAGE_MAGIC, 8) &&
              hdr->n_patches == c->n_patches && hdr->shot == c->shot && hdr->grid_w > 0 && hdr->grid_h > 0 &&
              hdr->grid_w <= 16384 && hdr->grid_h <= 16384;
    for (int i = 0; ok && i < 9; i++)
        ok = isfinite(hdr->grid_map[i]);
    for (uint32_t i = 0; ok && i < c->n_patches; i++) {
        const struct ai_patch_rec *p = &r[i];
        ok = (p->kind == AI_PATCH_PERSISTENT || p->kind == AI_PATCH_OVERLAY) && p->w > 0 && p->h > 0 &&
             (uint32_t) p->dst_x + p->w <= hdr->grid_w && (uint32_t) p->dst_y + p->h <= hdr->grid_h &&
             (uint32_t) p->src_x + p->w <= c->tex_w && (uint32_t) p->src_y + p->h <= c->tex_h &&
             (p->kind == AI_PATCH_PERSISTENT || p->until >= p->frame);
    }
    if (!ok) {
        free(r);
        return false;
    }
    *recs = r;
    return true;
}

volatile struct ai_player_block *ai_store_player_begin(struct ai_store *s)
{
    uint32_t q = s->player->seq | 1u; // odd: the worker drops what it copies now
    atomic_store_explicit((_Atomic uint32_t *) &s->player->seq, q, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    return s->player;
}

void ai_store_player_end(struct ai_store *s)
{
    uint32_t q = (s->player->seq | 1u) + 1u; // even again
    atomic_store_explicit((_Atomic uint32_t *) &s->player->seq, q, memory_order_release);
}
