// SPDX-License-Identifier: AGPL-3.0-only
// The native buffers behind SliceResult.moves, .layers and .gcode_line_ends (04 sections 5.2 and 5.3).
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

namespace slicewright {

// A fixed-size, uninitialised buffer (std::vector would zero-fill tens of megabytes we are about to overwrite).
template <typename T> struct Buf {
    std::unique_ptr<T[]> p;
    size_t               n = 0;
    void alloc(size_t count)
    {
        n = count;
        p.reset(new T[count ? count : 1]);  // never null, so a zero-length array still has a valid pointer
    }
    T       *data() { return p.get(); }
    const T *data() const { return p.get(); }
};

// 04 sections 5.2 and 5.3. All arrays are K long (L for the layer ones); position is K x 3 and time K x 2.
struct ResultStore {
    size_t k = 0, layers = 0;
    Buf<float>    position, width, height, mm3_per_mm, feedrate, actual_feedrate, fan, temperature, pressure_advance, acceleration,
        jerk, time, print_z;
    Buf<uint8_t>  type, role, filament, nozzle, color_id;
    Buf<uint32_t> layer_id, gcode_line;
    Buf<int32_t>  object_id;
    Buf<float>    layer_z;
    Buf<uint32_t> layer_first, layer_last;
    Buf<uint64_t> line_ends;
};

} // namespace slicewright
