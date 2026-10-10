// SPDX-License-Identifier: AGPL-3.0-only
#include "paint.hpp"

#include <algorithm>

#include "libslic3r/TriangleSelector.hpp"

namespace slicewright {

using namespace Slic3r;

namespace {

void apply_one(const ModelVolume &volume, const std::vector<uint8_t> &states, FacetsAnnotation &target)
{
    if (states.empty() || std::none_of(states.begin(), states.end(), [](uint8_t s) { return s != 0; }))
        return;  // nothing painted: leave the annotation empty so Orca's "has paint" checks stay false
    TriangleSelector selector(volume.mesh());  // same face indexing as the input
    for (size_t i = 0; i < states.size(); ++i)
        if (states[i])
            selector.set_facet(int(i), static_cast<EnforcerBlockerType>(states[i]));
    target.set(selector);
}

} // namespace

void apply_paint(ModelVolume &volume, const std::vector<uint8_t> &support, const std::vector<uint8_t> &seam,
                 const std::vector<uint8_t> &extruder)
{
    apply_one(volume, support, volume.supported_facets);
    apply_one(volume, seam, volume.seam_facets);
    apply_one(volume, extruder, volume.mmu_segmentation_facets);
}

} // namespace slicewright
