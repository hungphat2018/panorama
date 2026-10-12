// A small strict JSON reader, see pp-json.h.
#include "pp-json.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct parser {
    const char *text, *p;
    char *err;
    size_t errsz;
    int depth;
};

static void fail(struct parser *ps, const char *msg)
{
    if (ps->err && ps->err[0] == 0)
        snprintf(ps->err, ps->errsz, "JSON: %s at byte %zu", msg, (size_t) (ps->p - ps->text));
}

static void skip_ws(struct parser *ps)
{
    while (*ps->p == ' ' || *ps->p == '\t' || *ps->p == '\n' || *ps->p == '\r')
        ps->p++;
}

static struct pp_json *node(enum pp_json_type t)
{
    struct pp_json *j = calloc(1, sizeof(*j));
    if (j)
        j->type = t;
    return j;
}

static struct pp_json *parse_value(struct parser *ps);

static char *parse_string_raw(struct parser *ps)
{
    if (*ps->p != '"') {
        fail(ps, "expected a string");
        return NULL;
    }
    ps->p++;
    size_t cap = 16, n = 0;
    char *s = malloc(cap);
    if (!s)
        return NULL;
    while (*ps->p && *ps->p != '"') {
        char c = *ps->p++;
        if (c == '\\') {
            char e = *ps->p++;
            switch (e) {
            case '"': c = '"'; break;
            case '\\': c = '\\'; break;
            case '/': c = '/'; break;
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            default:
                free(s);
                fail(ps, "unsupported escape (ASCII files only)");
                return NULL;
            }
        } else if ((unsigned char) c < 0x20) {
            free(s);
            fail(ps, "control character in a string");
            return NULL;
        }
        if (n + 2 > cap) {
            cap *= 2;
            char *t = realloc(s, cap);
            if (!t) {
                free(s);
                return NULL;
            }
            s = t;
        }
        s[n++] = c;
    }
    if (*ps->p != '"') {
        free(s);
        fail(ps, "unterminated string");
        return NULL;
    }
    ps->p++;
    s[n] = 0;
    return s;
}

static struct pp_json *parse_number(struct parser *ps)
{
    char *end;
    double v = strtod(ps->p, &end);
    if (end == ps->p) {
        fail(ps, "expected a number");
        return NULL;
    }
    ps->p = end;
    struct pp_json *j = node(PP_J_NUM);
    if (j)
        j->num = v;
    return j;
}

static struct pp_json *parse_container(struct parser *ps, bool is_obj)
{
    char close = is_obj ? '}' : ']';
    ps->p++;
    struct pp_json *j = node(is_obj ? PP_J_OBJ : PP_J_ARR);
    if (!j)
        return NULL;
    if (++ps->depth > 64) {
        fail(ps, "nesting too deep");
        goto bad;
    }
    skip_ws(ps);
    if (*ps->p == close) {
        ps->p++;
        ps->depth--;
        return j;
    }
    for (;;) {
        skip_ws(ps);
        char *key = NULL;
        if (is_obj) {
            key = parse_string_raw(ps);
            if (!key)
                goto bad;
            skip_ws(ps);
            if (*ps->p != ':') {
                free(key);
                fail(ps, "expected ':'");
                goto bad;
            }
            ps->p++;
            skip_ws(ps);
        }
        struct pp_json *v = parse_value(ps);
        if (!v) {
            free(key);
            goto bad;
        }
        struct pp_json **items = realloc(j->items, (size_t) (j->n + 1) * sizeof(*items));
        char **keys = is_obj ? realloc(j->keys, (size_t) (j->n + 1) * sizeof(*keys)) : NULL;
        if (!items || (is_obj && !keys)) {
            pp_json_free(v);
            free(key);
            goto bad;
        }
        j->items = items;
        if (is_obj) {
            j->keys = keys;
            j->keys[j->n] = key;
        }
        j->items[j->n++] = v;
        skip_ws(ps);
        if (*ps->p == ',') {
            ps->p++;
            continue;
        }
        if (*ps->p == close) {
            ps->p++;
            ps->depth--;
            return j;
        }
        fail(ps, is_obj ? "expected ',' or '}'" : "expected ',' or ']'");
        goto bad;
    }
bad:
    pp_json_free(j);
    return NULL;
}

static struct pp_json *parse_value(struct parser *ps)
{
    skip_ws(ps);
    char c = *ps->p;
    if (c == '{')
        return parse_container(ps, true);
    if (c == '[')
        return parse_container(ps, false);
    if (c == '"') {
        char *s = parse_string_raw(ps);
        if (!s)
            return NULL;
        struct pp_json *j = node(PP_J_STR);
        if (!j) {
            free(s);
            return NULL;
        }
        j->str = s;
        return j;
    }
    if (!strncmp(ps->p, "true", 4)) {
        ps->p += 4;
        struct pp_json *j = node(PP_J_BOOL);
        if (j)
            j->boolean = true;
        return j;
    }
    if (!strncmp(ps->p, "false", 5)) {
        ps->p += 5;
        return node(PP_J_BOOL);
    }
    if (!strncmp(ps->p, "null", 4)) {
        ps->p += 4;
        return node(PP_J_NULL);
    }
    if (c == '-' || isdigit((unsigned char) c))
        return parse_number(ps);
    fail(ps, "unexpected character");
    return NULL;
}

struct pp_json *pp_json_parse(const char *text, char *err, size_t errsz)
{
    struct parser ps = { .text = text, .p = text, .err = err, .errsz = errsz };
    if (err && errsz)
        err[0] = 0;
    struct pp_json *j = parse_value(&ps);
    if (j) {
        skip_ws(&ps);
        if (*ps.p) {
            fail(&ps, "trailing text");
            pp_json_free(j);
            return NULL;
        }
    }
    return j;
}

void pp_json_free(struct pp_json *j)
{
    if (!j)
        return;
    for (int i = 0; i < j->n; i++) {
        pp_json_free(j->items[i]);
        if (j->keys)
            free(j->keys[i]);
    }
    free(j->items);
    free(j->keys);
    free(j->str);
    free(j);
}

const struct pp_json *pp_json_get(const struct pp_json *obj, const char *key)
{
    if (!obj || obj->type != PP_J_OBJ)
        return NULL;
    for (int i = 0; i < obj->n; i++)
        if (!strcmp(obj->keys[i], key))
            return obj->items[i];
    return NULL;
}

const struct pp_json *pp_json_at(const struct pp_json *arr, int i)
{
    return arr && arr->type == PP_J_ARR && i >= 0 && i < arr->n ? arr->items[i] : NULL;
}

double pp_json_num(const struct pp_json *j, double dflt)
{
    return j && j->type == PP_J_NUM ? j->num : dflt;
}

const char *pp_json_str(const struct pp_json *j, const char *dflt)
{
    return j && j->type == PP_J_STR ? j->str : dflt;
}

bool pp_json_bool(const struct pp_json *j, bool dflt)
{
    return j && j->type == PP_J_BOOL ? j->boolean : dflt;
}
