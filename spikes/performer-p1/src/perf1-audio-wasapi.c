// The WASAPI sink: the default render device in shared mode, event driven, in the engine's mix format (float32). See
// perf1-audio-sink.h for the contract. The thread that calls these functions must have entered COM (the audio thread does).
#define COBJMACROS
#include "perf1-audio-sink.h"

#include <audioclient.h>
#include <mmdeviceapi.h>
#include <ks.h>
#include <ksmedia.h>
#include <propidl.h>
#include <stdio.h>
#include <stdlib.h>

// The interface ids, spelled out so the program links against nothing but ole32 (no uuid library).
static const GUID k_clsid_enumerator = { 0xBCDE0395, 0xE52F, 0x467C, { 0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E } };
static const GUID k_iid_enumerator = { 0xA95664D2, 0x9614, 0x4F35, { 0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6 } };
static const GUID k_iid_client = { 0x1CB9AD4C, 0xDBFA, 0x4C32, { 0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2 } };
static const GUID k_iid_render = { 0xF294ACFC, 0x3146, 0x4483, { 0xA7, 0xBF, 0xAD, 0xDC, 0xA7, 0xC2, 0x60, 0xE2 } };
static const GUID k_iid_clock = { 0xCD63314F, 0x3FBA, 0x4A1B, { 0x81, 0x2C, 0xEF, 0x96, 0x35, 0x87, 0x28, 0xE7 } };
static const GUID k_iid_volume = { 0x87CE5498, 0x68D6, 0x44E5, { 0x92, 0x15, 0x6D, 0xA4, 0x7E, 0xF8, 0x83, 0xD8 } };
static const GUID k_subtype_float = { 0x00000003, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71 } };
static const PROPERTYKEY k_friendly_name = { { 0xA45C254E, 0xDF1C, 0x4EFD, { 0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0 } }, 14 };

struct wasapi_dev {
    IMMDevice *device;
    IAudioClient *client;
    IAudioRenderClient *render;
    IAudioClock *clock;
    ISimpleAudioVolume *volume;
    UINT64 clock_freq;
    UINT64 pos_base;            // the clock's reading right after halt(): the position this stream's frame 0 sits at
    WAVEFORMATEX *mix;
};

// ---- operations ------------------------------------------------------------------------------------------------

static bool wasapi_start(struct audio_sink *s)
{
    struct wasapi_dev *d = s->impl;
    return SUCCEEDED(IAudioClient_Start(d->client));
}

static bool read_clock(struct audio_sink *s, UINT64 *pos, double *host_s)
{
    struct wasapi_dev *d = s->impl;
    UINT64 p = 0, qpc = 0;
    if (FAILED(IAudioClock_GetPosition(d->clock, &p, &qpc)))
        return false;
    *pos = p;
    *host_s = (double) qpc * 1e-7;      // 100 ns units of the performance counter
    return true;
}

static bool wasapi_halt(struct audio_sink *s)
{
    struct wasapi_dev *d = s->impl;
    IAudioClient_Stop(d->client);
    if (FAILED(IAudioClient_Reset(d->client)))
        return false;
    UINT64 p;
    double t;
    d->pos_base = read_clock(s, &p, &t) ? p : 0;
    return true;
}

static uint32_t wasapi_padding(struct audio_sink *s)
{
    struct wasapi_dev *d = s->impl;
    UINT32 pad = 0;
    if (FAILED(IAudioClient_GetCurrentPadding(d->client, &pad)))
        return s->capacity;         // treat a failing device as full: nothing is written
    return pad;
}

static float *wasapi_lock(struct audio_sink *s, uint32_t frames)
{
    struct wasapi_dev *d = s->impl;
    BYTE *data = NULL;
    if (FAILED(IAudioRenderClient_GetBuffer(d->render, frames, &data)))
        return NULL;
    return (float *) data;
}

static bool wasapi_unlock(struct audio_sink *s, uint32_t frames)
{
    struct wasapi_dev *d = s->impl;
    return SUCCEEDED(IAudioRenderClient_ReleaseBuffer(d->render, frames, 0));
}

static bool wasapi_position(struct audio_sink *s, uint64_t *frames, double *host_s)
{
    struct wasapi_dev *d = s->impl;
    UINT64 p;
    if (!read_clock(s, &p, host_s))
        return false;
    UINT64 rel = p > d->pos_base ? p - d->pos_base : 0;
    *frames = (uint64_t) ((double) rel * (double) s->rate / (double) d->clock_freq);
    return true;
}

static void wasapi_on_wake(struct audio_sink *s)
{
    (void) s;
}

static void wasapi_close(struct audio_sink *s)
{
    struct wasapi_dev *d = s->impl;
    if (d->client)
        IAudioClient_Stop(d->client);
    if (d->volume)
        ISimpleAudioVolume_Release(d->volume);
    if (d->clock)
        IAudioClock_Release(d->clock);
    if (d->render)
        IAudioRenderClient_Release(d->render);
    if (d->client)
        IAudioClient_Release(d->client);
    if (d->device)
        IMMDevice_Release(d->device);
    if (d->mix)
        CoTaskMemFree(d->mix);
    if (s->wake)
        CloseHandle(s->wake);
    free(d);
}

// ---- open ------------------------------------------------------------------------------------------------------

static void fail_msg(char *err, size_t n, const char *what, HRESULT hr)
{
    if (err && n)
        snprintf(err, n, "%s (0x%08lx)", what, (unsigned long) hr);
}

static void device_name(IMMDevice *dev, char *out, size_t n)
{
    snprintf(out, n, "default render device");
    IPropertyStore *ps = NULL;
    if (FAILED(IMMDevice_OpenPropertyStore(dev, STGM_READ, &ps)))
        return;
    PROPVARIANT pv;
    PropVariantInit(&pv);
    if (SUCCEEDED(IPropertyStore_GetValue(ps, &k_friendly_name, &pv)) && pv.vt == VT_LPWSTR && pv.pwszVal)
        WideCharToMultiByte(CP_UTF8, 0, pv.pwszVal, -1, out, (int) n, NULL, NULL);
    PropVariantClear(&pv);
    IPropertyStore_Release(ps);
}

// The mix format must be float32; fills rate, channels and the speaker mask.
static bool take_mix_format(struct audio_sink *s, const WAVEFORMATEX *w)
{
    bool is_float = w->wFormatTag == WAVE_FORMAT_IEEE_FLOAT;
    if (w->wFormatTag == WAVE_FORMAT_EXTENSIBLE && w->cbSize >= 22) {
        const WAVEFORMATEXTENSIBLE *x = (const WAVEFORMATEXTENSIBLE *) w;
        is_float = IsEqualGUID(&x->SubFormat, &k_subtype_float);
        s->chmask = x->dwChannelMask;
    }
    if (!is_float || w->wBitsPerSample != 32)
        return false;
    s->rate = (int) w->nSamplesPerSec;
    s->channels = w->nChannels;
    return true;
}

struct audio_sink *audio_sink_wasapi_open(double volume, char *err, size_t err_size)
{
    struct audio_sink *s = calloc(1, sizeof(*s));
    struct wasapi_dev *d = calloc(1, sizeof(*d));
    IMMDeviceEnumerator *en = NULL;
    HRESULT hr;
    if (!s || !d)
        goto fail;
    s->impl = d;
    s->kind = "wasapi";
    hr = CoCreateInstance(&k_clsid_enumerator, NULL, CLSCTX_ALL, &k_iid_enumerator, (void **) &en);
    if (FAILED(hr)) {
        fail_msg(err, err_size, "no audio device enumerator", hr);
        goto fail;
    }
    hr = IMMDeviceEnumerator_GetDefaultAudioEndpoint(en, eRender, eConsole, &d->device);
    if (FAILED(hr)) {
        fail_msg(err, err_size, "no default render device", hr);
        goto fail;
    }
    device_name(d->device, s->name, sizeof(s->name));
    // event driven, shared; a buffer of about 40 ms (the engine may give more), else the engine's own choice (0); the volume
    // of this stream is not persisted
    REFERENCE_TIME def_period = 0, min_period = 0;
    static const REFERENCE_TIME k_durations[2] = { 400000, 0 };
    for (int attempt = 0; attempt < 2; attempt++) {
        if (d->client)
            IAudioClient_Release(d->client);
        if (d->mix)
            CoTaskMemFree(d->mix);
        d->client = NULL;
        d->mix = NULL;
        hr = IMMDevice_Activate(d->device, &k_iid_client, CLSCTX_ALL, NULL, (void **) &d->client);
        if (FAILED(hr) || FAILED(hr = IAudioClient_GetMixFormat(d->client, &d->mix))) {
            fail_msg(err, err_size, "cannot open the device's audio client", hr);
            goto fail;
        }
        if (!take_mix_format(s, d->mix)) {
            snprintf(err, err_size, "the device's mix format is not 32-bit float (tag 0x%x, %u bits)", d->mix->wFormatTag,
                     d->mix->wBitsPerSample);
            goto fail;
        }
        IAudioClient_GetDevicePeriod(d->client, &def_period, &min_period);
        hr = IAudioClient_Initialize(d->client, AUDCLNT_SHAREMODE_SHARED,
                                     AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_NOPERSIST, k_durations[attempt], 0,
                                     d->mix, NULL);
        if (SUCCEEDED(hr))
            break;
    }
    if (FAILED(hr)) {
        fail_msg(err, err_size, "IAudioClient::Initialize failed", hr);
        goto fail;
    }
    s->wake = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!s->wake || FAILED(hr = IAudioClient_SetEventHandle(d->client, s->wake))) {
        fail_msg(err, err_size, "cannot set the audio event", hr);
        goto fail;
    }
    UINT32 cap = 0;
    REFERENCE_TIME lat = 0;
    IAudioClient_GetBufferSize(d->client, &cap);
    IAudioClient_GetStreamLatency(d->client, &lat);
    s->capacity = cap;
    s->period = (uint32_t) ((double) (def_period > 0 ? def_period : 100000) * 1e-7 * (double) s->rate + 0.5);
    s->stream_latency_s = (double) lat * 1e-7;
    if (FAILED(hr = IAudioClient_GetService(d->client, &k_iid_render, (void **) &d->render)) ||
        FAILED(hr = IAudioClient_GetService(d->client, &k_iid_clock, (void **) &d->clock))) {
        fail_msg(err, err_size, "cannot get the render client or the audio clock", hr);
        goto fail;
    }
    IAudioClock_GetFrequency(d->clock, &d->clock_freq);
    if (!d->clock_freq)
        d->clock_freq = (UINT64) s->rate;
    if (SUCCEEDED(IAudioClient_GetService(d->client, &k_iid_volume, (void **) &d->volume)))
        ISimpleAudioVolume_SetMasterVolume(d->volume, (float) (volume < 0.0 ? 0.0 : volume > 1.0 ? 1.0 : volume), NULL);
    s->start = wasapi_start;
    s->halt = wasapi_halt;
    s->padding = wasapi_padding;
    s->lock = wasapi_lock;
    s->unlock = wasapi_unlock;
    s->position = wasapi_position;
    s->on_wake = wasapi_on_wake;
    s->close = wasapi_close;
    IMMDeviceEnumerator_Release(en);
    if (!wasapi_halt(s)) {          // a clean start: position base known
        wasapi_close(s);
        free(s);
        if (err && err_size)
            snprintf(err, err_size, "IAudioClient::Reset failed");
        return NULL;
    }
    return s;
fail:
    if (en)
        IMMDeviceEnumerator_Release(en);
    if (s && d)
        wasapi_close(s);
    free(s);
    return NULL;
}
