// SPDX-License-Identifier: AGPL-3.0-only
// SliceResult (04 section 5): the finished slice. The numeric arrays live in a ResultStore that the numpy arrays
// share through a capsule, so they stay valid as long as any array does, after the result and the job are gone.
#pragma once

#include <nanobind/nanobind.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "issues.hpp"

namespace slicewright {

namespace nb = nanobind;

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

class SliceResult {
public:
    std::string              gcode_path;
    std::vector<std::string> objects;
    std::vector<Issue>       warnings;
    size_t                   layer_count = 0;
    double                   time_normal = 0, time_silent = 0;
    std::string              time_display;
    int                      threads = 0;  // size of the TBB arena the job ran in
    std::shared_ptr<ResultStore> store = std::make_shared<ResultStore>();

    ~SliceResult();

    // Runs without the GIL: touches no Python object. Returns an empty string on success, else the error text,
    // which the caller turns into an exception after it has the GIL back.
    std::string write_gcode(const std::string &path) const noexcept;
};

void bind_result(nb::module_ &m);

} // namespace slicewright
