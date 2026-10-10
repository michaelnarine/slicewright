// SPDX-License-Identifier: AGPL-3.0-only
// GCodeProcessorResult (an array of structs, about 120 B per move) to the structure-of-arrays of 04 section 5.2,
// in chunks (02 section 5.7).
#pragma once

#include <cstddef>
#include <vector>

#include "result.hpp"

#include "libslic3r/GCode/GCodeProcessor.hpp"

namespace slicewright {

// Number of moves converted per chunk (1M by default). The processed pages of the AoS buffer are returned to the OS
// after every chunk, so peak memory is the SoA plus about one chunk of the AoS instead of both in full.
size_t move_chunk();
void   set_move_chunk(size_t n);  // 0 restores the default; test hook

// Fills `out` from `res.moves`, `res.lines_ends` and the filament -> nozzle map (1-based extruder per filament, as
// Print::get_filament_maps() returns it). `object_count` is the number of objects the job holds: a label id L > 0
// maps to object L - 1 (the engine gives instance i the label id i + 1), anything else to -1. `res.moves` is
// emptied. Runs on the engine thread, inside the job's TBB arena.
void convert_moves(Slic3r::GCodeProcessorResult &res, const std::vector<int> &filament_maps, size_t object_count, ResultStore &out);

} // namespace slicewright
