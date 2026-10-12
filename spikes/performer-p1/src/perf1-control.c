// Control channel: script and named pipe, see perf1-control.h.
#include "perf1-control.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <windows.h>

#include "perf1-json.h"

double now_s(void); // perf1-stats.c: the host clock the engine uses

enum sc_kind { SC_LOAD, SC_SEEK, SC_PAUSE, SC_RESUME, SC_STATUS, SC_STOP };

struct sentry {
    bool use_at;                  // "at" (film time) or "after" (seconds since the previous line)
    double when;
    enum sc_kind kind;
    double arg;                   // seek: film time
    struct cue_sheet *sheet;      // load: read at load time
    char *file;
    int line;
};

struct control {
    struct engine *engine;
    struct cue_store *store;
    double src_w, src_h;
    void (*on_stop)(void);

    struct sentry *entries;
    size_t n_entries, next;
    bool started;
    double last_fire;

    HANDLE pipe_thread, stop_event;
    wchar_t pipe_name[160];
    volatile LONG stopping;
};

struct control *control_create(struct engine *e, struct cue_store *store, double src_w, double src_h, void (*on_stop)(void))
{
    struct control *c = calloc(1, sizeof(*c));
    if (!c)
        return NULL;
    c->engine = e;
    c->store = store;
    c->src_w = src_w;
    c->src_h = src_h;
    c->on_stop = on_stop;
    c->stop_event = CreateEventW(NULL, TRUE, FALSE, NULL);
    return c;
}

static void jprint(char *out, size_t n, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(out, n, fmt, ap);
    va_end(ap);
}

// A message safe to put inside a JSON string.
static void json_text(char *dst, size_t n, const char *src)
{
    size_t k = 0;
    for (; *src && k + 2 < n; src++) {
        unsigned char ch = (unsigned char) *src;
        if (ch == '"')
            dst[k++] = '\'';
        else if (ch == '\\')
            dst[k++] = '/';
        else if (ch < 0x20)
            dst[k++] = ' ';
        else
            dst[k++] = (char) ch;
    }
    dst[k] = '\0';
}

static const char *state_name(int s)
{
    switch (s) {
    case ES_IDLE: return "idle";
    case ES_PLAYING: return "playing";
    case ES_PAUSED: return "paused";
    case ES_SEEK_PARK:
    case ES_SEEK_FILL: return "seeking";
    }
    return "?";
}

static void error_reply(char *reply, size_t n, const char *msg)
{
    char safe[240];
    json_text(safe, sizeof(safe), msg);
    jprint(reply, n, "{\"ok\":false,\"error\":\"%s\"}", safe);
}

// Publishes a loaded sheet (the store takes it on success).
static bool publish(struct control *c, struct cue_sheet *sheet, const char *label, double now, char *reply, size_t rn)
{
    char err[240] = "";
    int version = sheet->version;
    if (!cue_store_publish(c->store, sheet, err, sizeof(err))) {
        engine_note_load(c->engine, now, version, false, err);
        fprintf(stderr, "perf1: cue sheet %s refused: %s\n", label, err);
        cue_sheet_free(sheet);
        error_reply(reply, rn, err);
        return false;
    }
    engine_note_load(c->engine, now, version, true, label);
    printf("perf1: cue sheet version %d published (%s, %zu shots)\n", version, label, sheet->n_shots);
    if (sheet->ignored_keys)
        fprintf(stderr, "perf1: cue sheet %s: %u unknown key(s) ignored, the first is \"%s\"\n", label, sheet->ignored_keys,
                sheet->first_ignored_key);
    jprint(reply, rn, "{\"ok\":true,\"version\":%d,\"shots\":%zu}", version, sheet->n_shots);
    return true;
}

static void do_status(struct control *c, char *reply, size_t rn)
{
    struct engine_status st;
    engine_status(c->engine, &st);
    jprint(reply, rn,
           "{\"ok\":true,\"state\":\"%s\",\"epoch\":%d,\"playhead\":%.6f,\"version\":%d,\"versions\":%zu,"
           "\"swaps\":%u,\"seeks\":%u,\"restarts\":%u}",
           state_name(st.state), st.epoch, st.playhead, st.version, cue_store_count(c->store), st.swaps, st.seeks, st.restarts);
}

static void run_kind(struct control *c, enum sc_kind kind, double arg, struct cue_sheet *sheet, const char *label, double now,
                     char *reply, size_t rn)
{
    switch (kind) {
    case SC_LOAD:
        publish(c, sheet, label, now, reply, rn);
        break;
    case SC_SEEK:
        if (engine_post(c->engine, CMD_SEEK, arg, now))
            jprint(reply, rn, "{\"ok\":true}");
        else
            error_reply(reply, rn, "command queue full");
        break;
    case SC_PAUSE:
    case SC_RESUME:
        if (engine_post(c->engine, kind == SC_PAUSE ? CMD_PAUSE : CMD_RESUME, 0.0, now))
            jprint(reply, rn, "{\"ok\":true}");
        else
            error_reply(reply, rn, "command queue full");
        break;
    case SC_STATUS:
        do_status(c, reply, rn);
        break;
    case SC_STOP:
        jprint(reply, rn, "{\"ok\":true}");
        if (c->on_stop)
            c->on_stop();
        break;
    }
}

// Names the command in a parsed object; false with an error reply if it is not one.
static bool command_kind(const struct json_value *cmd, enum sc_kind *kind, char *reply, size_t rn)
{
    const struct json_value *k = json_member(cmd, "cmd");
    if (!cmd || cmd->type != JSON_OBJECT || !k || k->type != JSON_STRING) {
        error_reply(reply, rn, "a command is an object with a \"cmd\" string");
        return false;
    }
    if (!strcmp(k->str, "load")) *kind = SC_LOAD;
    else if (!strcmp(k->str, "seek")) *kind = SC_SEEK;
    else if (!strcmp(k->str, "pause")) *kind = SC_PAUSE;
    else if (!strcmp(k->str, "resume")) *kind = SC_RESUME;
    else if (!strcmp(k->str, "status")) *kind = SC_STATUS;
    else if (!strcmp(k->str, "stop")) *kind = SC_STOP;
    else {
        error_reply(reply, rn, "unknown command (load, seek, pause, resume, status, stop)");
        return false;
    }
    return true;
}

void control_execute_text(struct control *c, const char *text, size_t len, double now, char *reply, size_t reply_size)
{
    char jerr[200];
    struct json_value *cmd = json_parse(text, len, jerr, sizeof(jerr));
    if (!cmd) {
        error_reply(reply, reply_size, jerr);
        return;
    }
    enum sc_kind kind;
    if (command_kind(cmd, &kind, reply, reply_size)) {
        double arg = 0.0;
        struct cue_sheet *sheet = NULL;
        const char *label = "";
        bool ok = true;
        if (kind == SC_SEEK) {
            const struct json_value *t = json_member(cmd, "t");
            if (!t || t->type != JSON_NUMBER || !isfinite(t->num) || t->num < 0.0) {
                error_reply(reply, reply_size, "seek needs \"t\": the film time in seconds (0 or more)");
                ok = false;
            } else {
                arg = t->num;
            }
        } else if (kind == SC_LOAD) {
            const struct json_value *f = json_member(cmd, "file");
            char err[240] = "";
            if (!f || f->type != JSON_STRING) {
                error_reply(reply, reply_size, "load needs \"file\": the path of a cue sheet");
                ok = false;
            } else if (!(sheet = cue_sheet_load_file(f->str, c->src_w, c->src_h, err, sizeof(err)))) {
                engine_note_load(c->engine, now, 0, false, err);
                fprintf(stderr, "perf1: %s\n", err);
                error_reply(reply, reply_size, err);
                ok = false;
            } else {
                label = f->str;
            }
        }
        if (ok)
            run_kind(c, kind, arg, sheet, label, now, reply, reply_size);
    }
    json_free(cmd);
}

// ---- script ---------------------------------------------------------------------------------------------------

bool control_load_script(struct control *c, const char *path, char *err, size_t err_size)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        snprintf(err, err_size, "cannot open control script %s", path);
        return false;
    }
    char *line = malloc(1 << 20);
    int lineno = 0;
    bool ok = line != NULL;
    while (ok && fgets(line, 1 << 20, f)) {
        lineno++;
        char *s = line;
        while (*s == ' ' || *s == '\t')
            s++;
        if (!*s || *s == '\r' || *s == '\n' || *s == '#')
            continue;
        char jerr[200];
        struct json_value *cmd = json_parse(s, strlen(s), jerr, sizeof(jerr));
        char dummy[64];
        enum sc_kind kind;
        if (!cmd) {
            snprintf(err, err_size, "%s line %d: %s", path, lineno, jerr);
            ok = false;
            break;
        }
        struct sentry en = { .kind = SC_STATUS, .line = lineno };
        const struct json_value *at = json_member(cmd, "at"), *after = json_member(cmd, "after");
        if (!command_kind(cmd, &kind, dummy, sizeof(dummy))) {
            snprintf(err, err_size, "%s line %d: %s", path, lineno, dummy);
            ok = false;
        } else if ((at != NULL) == (after != NULL) || (at && (at->type != JSON_NUMBER || at->num < 0)) ||
                   (after && (after->type != JSON_NUMBER || after->num < 0))) {
            snprintf(err, err_size, "%s line %d: give exactly one of \"at\" (film time) or \"after\" (seconds), a number from 0", path, lineno);
            ok = false;
        } else {
            en.kind = kind;
            en.use_at = at != NULL;
            en.when = at ? at->num : after->num;
            if (kind == SC_SEEK) {
                const struct json_value *t = json_member(cmd, "t");
                if (!t || t->type != JSON_NUMBER || t->num < 0) {
                    snprintf(err, err_size, "%s line %d: seek needs \"t\"", path, lineno);
                    ok = false;
                } else {
                    en.arg = t->num;
                }
            } else if (kind == SC_LOAD) {
                const struct json_value *file = json_member(cmd, "file");
                char lerr[240] = "";
                if (!file || file->type != JSON_STRING) {
                    snprintf(err, err_size, "%s line %d: load needs \"file\"", path, lineno);
                    ok = false;
                } else if (!(en.sheet = cue_sheet_load_file(file->str, c->src_w, c->src_h, lerr, sizeof(lerr)))) {
                    snprintf(err, err_size, "%s line %d: %s", path, lineno, lerr);
                    ok = false;
                } else {
                    en.file = _strdup(file->str);
                }
            }
        }
        json_free(cmd);
        if (ok) {
            struct sentry *ne = realloc(c->entries, (c->n_entries + 1) * sizeof(*ne));
            if (!ne) {
                snprintf(err, err_size, "out of memory");
                ok = false;
            } else {
                c->entries = ne;
                c->entries[c->n_entries++] = en;
            }
        } else {
            cue_sheet_free(en.sheet);
        }
    }
    free(line);
    fclose(f);
    if (ok)
        printf("perf1: control script %s: %zu commands\n", path, c->n_entries);
    return ok;
}

void control_film_started(struct control *c, double now)
{
    c->started = true;
    c->last_fire = now;
}

void control_poll(struct control *c, double now)
{
    while (c->started && c->next < c->n_entries) {
        struct sentry *en = &c->entries[c->next];
        bool due = en->use_at ? engine_playhead(c->engine) >= en->when : now >= c->last_fire + en->when;
        if (!due)
            return;
        char reply[512];
        struct cue_sheet *sheet = en->sheet;
        en->sheet = NULL; // published or freed by the executor
        run_kind(c, en->kind, en->arg, sheet, en->file ? en->file : "", now, reply, sizeof(reply));
        printf("perf1: script line %d at %.3f s: %s\n", en->line, now - c->last_fire, reply);
        c->last_fire = now;
        c->next++;
    }
}

// ---- named pipe -----------------------------------------------------------------------------------------------

// Waits for an overlapped operation or for the stop event. False when stopping.
static bool wait_io(struct control *c, HANDLE io_event)
{
    HANDLE h[2] = { io_event, c->stop_event };
    return WaitForMultipleObjects(2, h, FALSE, INFINITE) == WAIT_OBJECT_0;
}

static DWORD WINAPI pipe_thread_main(LPVOID arg)
{
    struct control *c = arg;
    HANDLE ev = CreateEventW(NULL, TRUE, FALSE, NULL);
    while (!c->stopping) {
        HANDLE pipe = CreateNamedPipeW(c->pipe_name, PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                                       PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1,
                                       65536, 65536, 0, NULL);
        if (pipe == INVALID_HANDLE_VALUE) {
            fprintf(stderr, "perf1: cannot create the control pipe (error %lu)\n", GetLastError());
            break;
        }
        OVERLAPPED ov = { .hEvent = ev };
        ResetEvent(ev);
        bool connected = ConnectNamedPipe(pipe, &ov) != 0;
        if (!connected) {
            DWORD e = GetLastError();
            if (e == ERROR_PIPE_CONNECTED) {
                connected = true;
            } else if (e == ERROR_IO_PENDING) {
                if (!wait_io(c, ev)) {
                    CancelIo(pipe);
                    CloseHandle(pipe);
                    break;
                }
                connected = true;
            }
        }
        while (connected && !c->stopping) {
            char buf[65536];
            DWORD got = 0;
            OVERLAPPED ro = { .hEvent = ev };
            ResetEvent(ev);
            BOOL r = ReadFile(pipe, buf, sizeof(buf) - 1, &got, &ro);
            if (!r) {
                DWORD e = GetLastError();
                if (e == ERROR_IO_PENDING) {
                    if (!wait_io(c, ev)) {
                        CancelIo(pipe);
                        break;
                    }
                    if (!GetOverlappedResult(pipe, &ro, &got, FALSE))
                        break; // the client went away
                } else if (e != ERROR_MORE_DATA) {
                    break;
                }
            }
            char reply[1024];
            if (r || GetLastError() != ERROR_MORE_DATA) {
                control_execute_text(c, buf, got, now_s(), reply, sizeof(reply));
            } else {
                error_reply(reply, sizeof(reply), "message longer than 64 KB");
            }
            OVERLAPPED wo = { .hEvent = ev };
            ResetEvent(ev);
            DWORD wrote = 0;
            if (!WriteFile(pipe, reply, (DWORD) strlen(reply), &wrote, &wo)) {
                if (GetLastError() != ERROR_IO_PENDING || !wait_io(c, ev))
                    break;
                GetOverlappedResult(pipe, &wo, &wrote, FALSE);
            }
        }
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
    }
    CloseHandle(ev);
    return 0;
}

bool control_start_pipe(struct control *c, const char *pipe_name)
{
    if (!MultiByteToWideChar(CP_UTF8, 0, pipe_name, -1, c->pipe_name + 9, 150))
        return false;
    memcpy(c->pipe_name, L"\\\\.\\pipe\\", 9 * sizeof(wchar_t));
    c->pipe_thread = CreateThread(NULL, 0, pipe_thread_main, c, 0, NULL);
    if (c->pipe_thread)
        printf("perf1: control pipe %ls\n", c->pipe_name);
    return c->pipe_thread != NULL;
}

void control_destroy(struct control *c)
{
    if (!c)
        return;
    c->stopping = 1;
    SetEvent(c->stop_event);
    if (c->pipe_thread) {
        WaitForSingleObject(c->pipe_thread, 3000);
        CloseHandle(c->pipe_thread);
    }
    CloseHandle(c->stop_event);
    for (size_t i = 0; i < c->n_entries; i++) {
        cue_sheet_free(c->entries[i].sheet);
        free(c->entries[i].file);
    }
    free(c->entries);
    free(c);
}
