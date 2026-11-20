// SPDX-License-Identifier: AGPL-3.0-only
// SliceResult.stats (04 section 5.4): plain C++ values computed on the engine thread, turned into a dict on demand.
#pragma once

#include <array>
#include <string>
#include <utility>
#include <vector>

#include "store.hpp"

#include "libslic3r/GCode/GCodeProcessor.hpp"
#include "libslic3r/Print.hpp"

namespace slicewright {

struct Stats {
    using Pair = std::array<double, 2>;  // [normal, silent]
    double time_normal = 0, time_silent = 0, prepare_time = 0;
    std::vector<std::pair<std::string, Pair>> time_by_role, time_by_move_type;
    struct Filament { double mm = 0, cm3 = 0, g = 0, cost = 0; };
    std::vector<Filament> filaments;                                  // index = 0-based filament
    std::vector<std::pair<std::string, Pair>> used_per_role;          // role -> [m, g]
    std::vector<double> flush_g;
    int    filament_changes = 0, tool_changes = 0;
    size_t layer_count = 0;
    double travel_mm = 0;
    std::vector<std::pair<std::string, std::string>> display;
    int    threads = 0;
};

// `moves` is the converted SoA (the AoS list has been released by then). Sums `moves.time` by extrusion role (for
// extrusion moves) and by move type (for the rest), as the GUI does (GCodeViewer.cpp:1326); filament numbers come
// from the per-filament volumes with the diameter, density and cost the processor carries.
Stats compute_stats(const Slic3r::GCodeProcessorResult &res, const Slic3r::Print &print, const ResultStore &moves);

} // namespace slicewright
