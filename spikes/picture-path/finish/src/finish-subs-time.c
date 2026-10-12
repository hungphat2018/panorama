// Pass 4: libass time for a film time. Its own file so the CPU-only unit tests can check it without libass.
#include <math.h>

#include "finish-passes.h"

long long fp_subs_ass_time_ms(double pts_s)
{
    // The largest whole millisecond not after pts. libass shows an event while start <= now < end, and events start
    // and end on whole milliseconds, so this is exactly "on for the first frame at or after its start, off for the first
    // frame at or after its end". The 1e-6 ms guard keeps a time such as 5.005 s (stored as 5.00499999999999989...)
    // from rounding down to 5004 ms.
    return (long long) floor(pts_s * 1000.0 + 1.0e-6);
}
