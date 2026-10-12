// Strict JSON parser, see perf1-json.h. Recursive descent; nesting is capped so a hostile file can't exhaust the stack.
#include "perf1-json.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define JSON_MAX_DEPTH 64
#define JSON_MAX_NUMBER_CHARS 127

struct jp {
    const char *s;
    size_t len, pos;
    char *err;
    size_t err_size;
    bool failed;
};

static void jp_fail(struct jp *p, const char *msg)
{
    if (p->failed)
        return;
    p->failed = true;
    size_t line = 1, col = 1;
    for (size_t i = 0; i < p->pos && i < p->len; i++) {
        if (p->s[i] == '\n') {
            line++;
            col = 1;
        } else {
            col++;
        }
    }
    if (p->err && p->err_size)
        snprintf(p->err, p->err_size, "line %zu, column %zu: %s", line, col, msg);
}

static void release_contents(struct json_value *v)
{
    if (!v)
        return;
    free(v->str);
    for (size_t i = 0; i < v->n; i++) {
        release_contents(&v->items[i]);
        if (v->keys)
            free(v->keys[i]);
    }
    free(v->items);
    free(v->keys);
    memset(v, 0, sizeof(*v));
}

void json_free(struct json_value *v)
{
    release_contents(v);
    free(v);
}

static void skip_ws(struct jp *p)
{
    while (p->pos < p->len) {
        char c = p->s[p->pos];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r')
            p->pos++;
        else
            break;
    }
}

static int hex4(struct jp *p, unsigned *out)
{
    if (p->pos + 4 > p->len) {
        jp_fail(p, "truncated \\u escape");
        return -1;
    }
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p->s[p->pos + (size_t) i];
        unsigned d;
        if (c >= '0' && c <= '9') d = (unsigned) (c - '0');
        else if (c >= 'a' && c <= 'f') d = (unsigned) (c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') d = (unsigned) (c - 'A' + 10);
        else {
            jp_fail(p, "bad hex digit in \\u escape");
            return -1;
        }
        v = v * 16 + d;
    }
    p->pos += 4;
    *out = v;
    return 0;
}

static size_t put_utf8(char *out, unsigned cp)
{
    if (cp < 0x80) {
        out[0] = (char) cp;
        return 1;
    }
    if (cp < 0x800) {
        out[0] = (char) (0xC0 | (cp >> 6));
        out[1] = (char) (0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char) (0xE0 | (cp >> 12));
        out[1] = (char) (0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char) (0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char) (0xF0 | (cp >> 18));
    out[1] = (char) (0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char) (0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char) (0x80 | (cp & 0x3F));
    return 4;
}

// Parses a string literal at p->pos (which holds the opening quote). Returns a malloc'd decoded string.
static char *parse_string(struct jp *p)
{
    p->pos++; // opening quote
    // The decoded form is never longer than the source text between the quotes.
    size_t cap = p->len - p->pos + 1, n = 0;
    char *out = malloc(cap);
    if (!out) {
        jp_fail(p, "out of memory");
        return NULL;
    }
    for (;;) {
        if (p->pos >= p->len) {
            jp_fail(p, "unterminated string");
            goto bad;
        }
        unsigned char c = (unsigned char) p->s[p->pos];
        if (c == '"') {
            p->pos++;
            out[n] = '\0';
            return out;
        }
        if (c < 0x20) {
            jp_fail(p, "control character in string");
            goto bad;
        }
        if (c != '\\') {
            out[n++] = (char) c;
            p->pos++;
            continue;
        }
        p->pos++;
        if (p->pos >= p->len) {
            jp_fail(p, "unterminated escape");
            goto bad;
        }
        char e = p->s[p->pos++];
        switch (e) {
        case '"': out[n++] = '"'; break;
        case '\\': out[n++] = '\\'; break;
        case '/': out[n++] = '/'; break;
        case 'b': out[n++] = '\b'; break;
        case 'f': out[n++] = '\f'; break;
        case 'n': out[n++] = '\n'; break;
        case 'r': out[n++] = '\r'; break;
        case 't': out[n++] = '\t'; break;
        case 'u': {
            unsigned cp;
            if (hex4(p, &cp))
                goto bad;
            if (cp >= 0xD800 && cp <= 0xDBFF) {
                unsigned lo;
                if (p->pos + 2 > p->len || p->s[p->pos] != '\\' || p->s[p->pos + 1] != 'u') {
                    jp_fail(p, "lone high surrogate");
                    goto bad;
                }
                p->pos += 2;
                if (hex4(p, &lo))
                    goto bad;
                if (lo < 0xDC00 || lo > 0xDFFF) {
                    jp_fail(p, "bad low surrogate");
                    goto bad;
                }
                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
            } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                jp_fail(p, "lone low surrogate");
                goto bad;
            }
            if (cp == 0) {
                jp_fail(p, "\\u0000 is not supported");
                goto bad;
            }
            n += put_utf8(out + n, cp);
            break;
        }
        default:
            jp_fail(p, "unknown escape");
            goto bad;
        }
    }
bad:
    free(out);
    return NULL;
}

static bool parse_number(struct jp *p, struct json_value *out)
{
    size_t start = p->pos, i = p->pos;
    if (i < p->len && p->s[i] == '-')
        i++;
    if (i >= p->len || p->s[i] < '0' || p->s[i] > '9') {
        jp_fail(p, "bad number");
        return false;
    }
    if (p->s[i] == '0') {
        i++;
        if (i < p->len && p->s[i] >= '0' && p->s[i] <= '9') {
            jp_fail(p, "leading zero in number");
            return false;
        }
    } else {
        while (i < p->len && p->s[i] >= '0' && p->s[i] <= '9')
            i++;
    }
    if (i < p->len && p->s[i] == '.') {
        i++;
        if (i >= p->len || p->s[i] < '0' || p->s[i] > '9') {
            jp_fail(p, "digit expected after the decimal point");
            return false;
        }
        while (i < p->len && p->s[i] >= '0' && p->s[i] <= '9')
            i++;
    }
    if (i < p->len && (p->s[i] == 'e' || p->s[i] == 'E')) {
        i++;
        if (i < p->len && (p->s[i] == '+' || p->s[i] == '-'))
            i++;
        if (i >= p->len || p->s[i] < '0' || p->s[i] > '9') {
            jp_fail(p, "digit expected in the exponent");
            return false;
        }
        while (i < p->len && p->s[i] >= '0' && p->s[i] <= '9')
            i++;
    }
    size_t n = i - start;
    if (n > JSON_MAX_NUMBER_CHARS) {
        jp_fail(p, "number literal too long");
        return false;
    }
    char buf[JSON_MAX_NUMBER_CHARS + 1];
    memcpy(buf, p->s + start, n);
    buf[n] = '\0';
    double v = strtod(buf, NULL);
    if (!isfinite(v)) {
        jp_fail(p, "number out of range");
        return false;
    }
    out->type = JSON_NUMBER;
    out->num = v;
    p->pos = i;
    return true;
}

static bool match_literal(struct jp *p, const char *lit)
{
    size_t n = strlen(lit);
    if (p->pos + n <= p->len && !memcmp(p->s + p->pos, lit, n)) {
        p->pos += n;
        return true;
    }
    return false;
}

static bool parse_value(struct jp *p, int depth, struct json_value *out);

static bool grow(struct json_value *v, size_t *cap, bool with_keys)
{
    if (v->n < *cap)
        return true;
    size_t ncap = *cap ? *cap * 2 : 4;
    struct json_value *items = realloc(v->items, ncap * sizeof(*items));
    if (!items)
        return false;
    v->items = items;
    if (with_keys) {
        char **keys = realloc(v->keys, ncap * sizeof(*keys));
        if (!keys)
            return false;
        v->keys = keys;
    }
    *cap = ncap;
    return true;
}

static bool parse_array(struct jp *p, int depth, struct json_value *out)
{
    out->type = JSON_ARRAY;
    p->pos++; // [
    size_t cap = 0;
    skip_ws(p);
    if (p->pos < p->len && p->s[p->pos] == ']') {
        p->pos++;
        return true;
    }
    for (;;) {
        if (!grow(out, &cap, false)) {
            jp_fail(p, "out of memory");
            return false;
        }
        struct json_value *slot = &out->items[out->n];
        memset(slot, 0, sizeof(*slot));
        if (!parse_value(p, depth + 1, slot)) {
            release_contents(slot);
            return false;
        }
        out->n++;
        skip_ws(p);
        if (p->pos >= p->len) {
            jp_fail(p, "unterminated array");
            return false;
        }
        char c = p->s[p->pos++];
        if (c == ']')
            return true;
        if (c != ',') {
            p->pos--;
            jp_fail(p, "',' or ']' expected");
            return false;
        }
    }
}

static bool parse_object(struct jp *p, int depth, struct json_value *out)
{
    out->type = JSON_OBJECT;
    p->pos++; // {
    size_t cap = 0;
    skip_ws(p);
    if (p->pos < p->len && p->s[p->pos] == '}') {
        p->pos++;
        return true;
    }
    for (;;) {
        skip_ws(p);
        if (p->pos >= p->len || p->s[p->pos] != '"') {
            jp_fail(p, "member name (a string) expected");
            return false;
        }
        char *key = parse_string(p);
        if (!key)
            return false;
        for (size_t i = 0; i < out->n; i++)
            if (!strcmp(out->keys[i], key)) {
                free(key);
                jp_fail(p, "duplicate object key");
                return false;
            }
        skip_ws(p);
        if (p->pos >= p->len || p->s[p->pos] != ':') {
            free(key);
            jp_fail(p, "':' expected");
            return false;
        }
        p->pos++;
        if (!grow(out, &cap, true)) {
            free(key);
            jp_fail(p, "out of memory");
            return false;
        }
        struct json_value *slot = &out->items[out->n];
        memset(slot, 0, sizeof(*slot));
        if (!parse_value(p, depth + 1, slot)) {
            release_contents(slot);
            free(key);
            return false;
        }
        out->keys[out->n] = key;
        out->n++;
        skip_ws(p);
        if (p->pos >= p->len) {
            jp_fail(p, "unterminated object");
            return false;
        }
        char c = p->s[p->pos++];
        if (c == '}')
            return true;
        if (c != ',') {
            p->pos--;
            jp_fail(p, "',' or '}' expected");
            return false;
        }
    }
}

static bool parse_value(struct jp *p, int depth, struct json_value *out)
{
    if (depth > JSON_MAX_DEPTH) {
        jp_fail(p, "nesting too deep");
        return false;
    }
    skip_ws(p);
    if (p->pos >= p->len) {
        jp_fail(p, "value expected");
        return false;
    }
    char c = p->s[p->pos];
    switch (c) {
    case '{': return parse_object(p, depth, out);
    case '[': return parse_array(p, depth, out);
    case '"':
        out->str = parse_string(p);
        out->type = JSON_STRING;
        return out->str != NULL;
    case 't':
        if (match_literal(p, "true")) { out->type = JSON_BOOL; out->num = 1; return true; }
        break;
    case 'f':
        if (match_literal(p, "false")) { out->type = JSON_BOOL; out->num = 0; return true; }
        break;
    case 'n':
        if (match_literal(p, "null")) { out->type = JSON_NULL; return true; }
        break;
    default:
        if (c == '-' || (c >= '0' && c <= '9'))
            return parse_number(p, out);
        break;
    }
    jp_fail(p, "value expected");
    return false;
}

struct json_value *json_parse(const char *text, size_t len, char *err, size_t err_size)
{
    struct jp p = { .s = text, .len = len, .err = err, .err_size = err_size };
    if (err && err_size)
        err[0] = '\0';
    if (len >= 3 && !memcmp(text, "\xEF\xBB\xBF", 3)) // byte order mark
        p.pos = 3;
    struct json_value *root = calloc(1, sizeof(*root));
    if (!root) {
        jp_fail(&p, "out of memory");
        return NULL;
    }
    if (!parse_value(&p, 0, root)) {
        json_free(root);
        return NULL;
    }
    skip_ws(&p);
    if (p.pos != p.len) {
        jp_fail(&p, "unexpected text after the value");
        json_free(root);
        return NULL;
    }
    return root;
}

const struct json_value *json_member(const struct json_value *obj, const char *key)
{
    if (!obj || obj->type != JSON_OBJECT)
        return NULL;
    for (size_t i = 0; i < obj->n; i++)
        if (!strcmp(obj->keys[i], key))
            return &obj->items[i];
    return NULL;
}

static bool clone_into(const struct json_value *src, struct json_value *dst)
{
    *dst = *src;
    dst->str = NULL;
    dst->items = NULL;
    dst->keys = NULL;
    dst->n = 0;
    if (src->str) {
        dst->str = strdup(src->str);
        if (!dst->str)
            return false;
    }
    if (!src->n)
        return true;
    dst->items = calloc(src->n, sizeof(*dst->items));
    if (!dst->items)
        return false;
    if (src->keys) {
        dst->keys = calloc(src->n, sizeof(*dst->keys));
        if (!dst->keys)
            return false;
    }
    for (size_t i = 0; i < src->n; i++) {
        if (src->keys) {
            dst->keys[i] = strdup(src->keys[i]);
            if (!dst->keys[i])
                return false;
        }
        dst->n = i + 1; // from here a failure frees exactly what exists (items are zero-initialised)
        if (!clone_into(&src->items[i], &dst->items[i]))
            return false;
    }
    return true;
}

struct json_value *json_clone(const struct json_value *v)
{
    if (!v)
        return NULL;
    struct json_value *c = calloc(1, sizeof(*c));
    if (!c)
        return NULL;
    if (!clone_into(v, c)) {
        json_free(c);
        return NULL;
    }
    return c;
}

bool json_is_int_in(const struct json_value *v, double lo, double hi)
{
    return v && v->type == JSON_NUMBER && v->num >= lo && v->num <= hi && v->num == floor(v->num);
}

void json_write_string(FILE *f, const char *s)
{
    fputc('"', f);
    for (const unsigned char *c = (const unsigned char *) (s ? s : ""); *c; c++) {
        switch (*c) {
        case '"': fputs("\\\"", f); break;
        case '\\': fputs("\\\\", f); break;
        case '\n': fputs("\\n", f); break;
        case '\r': fputs("\\r", f); break;
        case '\t': fputs("\\t", f); break;
        default:
            if (*c < 0x20)
                fprintf(f, "\\u%04x", *c);
            else
                fputc(*c, f);
        }
    }
    fputc('"', f);
}
