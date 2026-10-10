// SPDX-License-Identifier: AGPL-3.0-only
// Per-face paint (04 section 2.4, 02 section 5.3): uint8 arrays become TriangleSelector states and then the
// volume's FacetsAnnotation, which is what Orca's support, seam and multi-material code reads.
#pragma once

#include <cstdint>
#include <vector>

#include "libslic3r/Model.hpp"

namespace slicewright {

// `support` and `seam`: 0 none, 1 enforcer, 2 blocker. `extruder`: 0 none, 1..16 the filament (Orca's
// EnforcerBlockerType Extruder1..Extruder16 are the same numbers). An empty vector means "not painted". Face i is
// the i-th triangle of the volume's mesh, which is the i-th triangle the caller passed (no reordering).
void apply_paint(Slic3r::ModelVolume &volume, const std::vector<uint8_t> &support, const std::vector<uint8_t> &seam,
                 const std::vector<uint8_t> &extruder);

} // namespace slicewright
