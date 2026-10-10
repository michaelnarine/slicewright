// SPDX-License-Identifier: AGPL-3.0-only
//
// Arrange with exclude areas and the wipe tower as an obstacle (02 section 5.4 and 5.9). Orca-derived: ported from
// OrcaSlicer v2.4.2, src/OrcaSlicer.cpp:4695-4830 (the CLI's arrange path), PartPlateList::preprocess_exclude_areas
// (slic3r/GUI/PartPlate.cpp:5454) and PartPlate::estimate_wipe_tower_size / estimate_wipe_tower_polygon
// (PartPlate.cpp:2186-2306), which the CLI reaches through GUI code.
#pragma once

#include <string>
#include <vector>

#include "libslic3r/Model.hpp"
#include "libslic3r/PrintConfig.hpp"

namespace slicewright::glue {

struct Placed {
    size_t index = 0;
    double dx = 0, dy = 0;     // new minus old instance position (mm)
    double old_x = 0, old_y = 0, new_x = 0, new_y = 0;
    double rotation = 0;       // radians, about the instance centre
    bool   fitted = true;
};

// Arranges every instance of `model` on the bed `printable_area - bed_exclude_area`, treating the wipe tower as an
// obstacle when one will be generated. `spacing_mm < 0` means "not given" (Orca's CLI default: no extra distance, the
// objects' own brim widths apply). The model is read, not changed: positions are returned. Objects that do not fit
// have fitted == false.
std::vector<Placed> arrange_model(const Slic3r::Model &model, const Slic3r::DynamicPrintConfig &cfg, double spacing_mm,
                                  bool allow_rotation);

// The rectangle (bed frame, mm) of the first-layer wipe tower footprint Orca's GUI would reserve, or false when no
// tower will be generated. For tests and for arrange.
bool estimate_wipe_tower(const Slic3r::Model &model, const Slic3r::DynamicPrintConfig &cfg, double &x, double &y, double &w, double &depth);

} // namespace slicewright::glue
