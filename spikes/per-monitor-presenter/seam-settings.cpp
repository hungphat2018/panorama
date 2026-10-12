// seam-settings.cpp: reading and writing the seam-settings JSON (see seam-settings.h).
#define _CRT_SECURE_NO_WARNINGS
#include "seam-settings.h"

#include <cstdio>
#include <cstdlib>
#include <functional>

namespace seam {

std::wstring local_stamp() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    wchar_t buf[32];
    swprintf(buf, 32, L"%04d%02d%02d-%02d%02d%02d", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
    return buf;
}

std::string local_iso() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    char buf[32];
    snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
    return buf;
}

bool make_dirs(const std::wstring& path) {
    for (size_t at = 0; at <= path.size(); ++at) {
        if (at != path.size() && path[at] != L'\\' && path[at] != L'/') continue;
        std::wstring part = path.substr(0, at);
        if (part.empty() || part.back() == L':') continue;  // a drive root
        if (!CreateDirectoryW(part.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS) {
            fprintf(stderr, "can't create %ls (error %lu)\n", part.c_str(), GetLastError());
            return false;
        }
    }
    return true;
}

namespace {

// {"DISPLAY1": v, "DISPLAY2": v}
void write_map(FILE* f, const char* key, const char* fmt, const std::function<double(const Monitor&)>& value) {
    fprintf(f, " \"%s\": {", key);
    for (size_t i = 0; i < g_monitors.size(); ++i) {
        fprintf(f, "%s\"%s\": ", i ? ", " : "", utf8(g_monitors[i]->label).c_str());
        fprintf(f, fmt, value(*g_monitors[i]));
    }
    fprintf(f, "},\n");
}

// The number after `"key":` at or after `from`, or false.
bool find_number(const std::string& text, const std::string& key, size_t from, double* out, size_t* at = nullptr) {
    size_t k = text.find("\"" + key + "\"", from);
    if (k == std::string::npos) return false;
    size_t colon = text.find(':', k);
    if (colon == std::string::npos) return false;
    const char* start = text.c_str() + colon + 1;
    char* end = nullptr;
    double v = strtod(start, &end);
    if (end == start) return false;
    *out = v;
    if (at) *at = k;
    return true;
}

}  // namespace

bool save_settings(const std::wstring& path, const Settings& s, const CalibrationInfo* cal) {
    size_t slash = path.find_last_of(L"\\/");
    if (slash != std::wstring::npos && !make_dirs(path.substr(0, slash))) return false;
    FILE* f = _wfopen(path.c_str(), L"w");
    if (!f) {
        fprintf(stderr, "can't write %ls\n", path.c_str());
        return false;
    }
    fprintf(f, "{\n \"gap_mm\": %.3f,\n \"right_screen_lower_mm\": %.3f,\n", s.gap_mm, s.height_mm);
    fprintf(f, " \"lag_ms\": {");
    for (size_t i = 0; i < g_monitors.size(); ++i)
        fprintf(f, "%s\"%s\": %.3f", i ? ", " : "", utf8(g_monitors[i]->label).c_str(), s.lag_ms[i]);
    fprintf(f, "},\n");
    write_map(f, "scan_fraction", "%.4f", [](const Monitor& m) { return double(m.clock.timing().active_h) / double(m.clock.timing().total_h); });
    write_map(f, "refresh_hz", "%.4f", [](const Monitor& m) { return m.clock.timing().refresh_hz; });
    fprintf(f, " \"rows_timing\": %s,\n", s.rows ? "true" : "false");
    fprintf(f, " \"version\": 2,\n");
    fprintf(f, " \"timing\": \"top-row times from the scan-line grid (D3DKMTGetScanLine); each row is drawn for the middle of the time its light stays up\",\n");
    write_map(f, "line_us", "%.4f", [](const Monitor& m) { return m.clock.timing().line_s * 1e6; });
    write_map(f, "scan_ms", "%.4f", [](const Monitor& m) { return m.clock.timing().scan_s * 1e3; });
    write_map(f, "blank_ms", "%.4f", [](const Monitor& m) { return m.clock.timing().blank_s * 1e3; });
    fprintf(f, " \"brightness_note\": \"set at the brightness used for matching (laptop backlight 44%%)\",\n");
    if (cal && cal->present) {
        fprintf(f, " \"calibration\": {\"status\": \"%s\", \"done\": [", cal->complete ? "complete" : "partial");
        for (size_t i = 0; i < cal->done.size(); ++i) fprintf(f, "%s\"%s\"", i ? ", " : "", cal->done[i].c_str());
        fprintf(f, "], \"rounds_per_parameter\": %d, \"time\": \"%s\", \"log\": \"%s\"},\n", cal->rounds, local_iso().c_str(),
                cal->log_name.c_str());
    }
    fprintf(f, " \"note\": \"lag is relative to the first (left) screen; widths assumed 344 mm (2560 px) and 476 mm (1920 px). "
               "Set at the brightness used for matching (laptop backlight 44%%).\"\n}\n");
    fclose(f);
    wprintf(L"saved %s\n", path.c_str());
    return true;
}

bool load_settings(const std::wstring& path, Settings* s) {
    FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) {
        fprintf(stderr, "can't read %ls\n", path.c_str());
        return false;
    }
    std::string text;
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) text.append(buf, n);
    fclose(f);
    double v;
    if (find_number(text, "gap_mm", 0, &v)) s->gap_mm = v;
    if (find_number(text, "right_screen_lower_mm", 0, &v)) s->height_mm = v;
    size_t lag_at = text.find("\"lag_ms\"");
    if (lag_at != std::string::npos) {
        size_t close = text.find('}', lag_at);
        std::string block = text.substr(lag_at, close == std::string::npos ? std::string::npos : close - lag_at);
        for (size_t i = 0; i < g_monitors.size(); ++i)
            if (find_number(block, utf8(g_monitors[i]->label), 0, &v)) s->lag_ms[i] = v;
    }
    return true;
}

}  // namespace seam
