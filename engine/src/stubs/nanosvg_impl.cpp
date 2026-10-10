// SPDX-License-Identifier: AGPL-3.0-only
// nanosvg's implementation is normally compiled into slic3r/GUI/BitmapCache.cpp, which we do not build.
// libslic3r's NSVGUtils.cpp (used by the 3MF reader) needs it.
#define NANOSVG_IMPLEMENTATION
#include "nanosvg/nanosvg.h"
