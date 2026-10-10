// SPDX-License-Identifier: AGPL-3.0-only
// set_thumbnails (04 section 4.1): the add-on hands RGBA images, Orca's own code (GCode/Thumbnails.cpp) encodes them
// per the `thumbnails` config key and writes them into the G-code.
#pragma once

#include <nanobind/nanobind.h>

#include <vector>

#include "issues.hpp"

#include "libslic3r/GCode/ThumbnailData.hpp"
#include "libslic3r/PrintConfig.hpp"

namespace slicewright {

namespace nb = nanobind;

struct ThumbnailSet {
    // In Orca's convention: row 0 is the BOTTOM row (OpenGL read-back). The API takes row 0 at the top and flips.
    std::vector<Slic3r::ThumbnailData> images;
    bool provided = false;  // set_thumbnails was called with at least one image
};

// Validates and copies a list of uint8 (H, W, 4) arrays, row 0 at the top. TypeError for a wrong dtype or a non-array,
// ValueError for a wrong shape. Raises before anything is stored; the caller replaces its set on success.
ThumbnailSet parse_thumbnails(nb::handle images);

// The `thumbnails` entries (WxH/FORMAT) of the config that have no image of exactly that size, as thumbnail_missing
// warnings. Not used for Bambu printers (their G-code carries no thumbnails, GCode.cpp:2644-2659). Returns an error
// issue (code "validation", opt_key "thumbnails") when the key itself is invalid.
std::vector<Issue> check_thumbnails(const ThumbnailSet &set, const Slic3r::DynamicPrintConfig &cfg, bool bbl_printer);

// The callback Print::export_gcode wants: the image of exactly the requested size, or nothing.
Slic3r::ThumbnailsGeneratorCallback thumbnail_callback(const ThumbnailSet &set);

// The largest image (by pixel count), or nullptr: plate_1.png of a .gcode.3mf.
const Slic3r::ThumbnailData *largest_thumbnail(const ThumbnailSet &set);

} // namespace slicewright
