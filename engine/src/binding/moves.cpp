// SPDX-License-Identifier: AGPL-3.0-only
#include "moves.hpp"

#include <tbb/blocked_range.h>
#include <tbb/parallel_for.h>

#include <algorithm>
#include <atomic>
#include <cstdint>

#if defined(__APPLE__) || defined(__linux__)
#include <sys/mman.h>
#include <unistd.h>
#endif

namespace slicewright {

using namespace Slic3r;

namespace {

std::atomic<size_t> g_chunk{size_t(1) << 20};

// Hands the whole pages inside [a, b) back to the OS. The moves in them have been converted and are never read
// again (MoveVertex is trivially destructible), so the contents may be discarded.
void release_pages(const void *a, const void *b)
{
#if defined(__APPLE__) || defined(__linux__)
    const uintptr_t page = uintptr_t(sysconf(_SC_PAGESIZE));
    const uintptr_t lo = (uintptr_t(a) + page - 1) & ~(page - 1), hi = uintptr_t(b) & ~(page - 1);
    if (hi > lo) {
#ifdef __APPLE__
        madvise(reinterpret_cast<void *>(lo), hi - lo, MADV_FREE);
#else
        madvise(reinterpret_cast<void *>(lo), hi - lo, MADV_DONTNEED);
#endif
    }
#else
    (void) a;
    (void) b;
#endif
}

} // namespace

size_t move_chunk() { return g_chunk.load(); }
void   set_move_chunk(size_t n) { g_chunk.store(n ? n : (size_t(1) << 20)); }

void convert_moves(GCodeProcessorResult &res, const std::vector<int> &filament_maps, size_t object_count, ResultStore &out)
{
    using Move = GCodeProcessorResult::MoveVertex;
    const size_t k = res.moves.size();
    out.k = k;
    out.position.alloc(k * 3);
    out.time.alloc(k * 2);
    for (Buf<float> *b : {&out.width, &out.height, &out.mm3_per_mm, &out.feedrate, &out.actual_feedrate, &out.fan, &out.temperature,
                          &out.pressure_advance, &out.acceleration, &out.jerk, &out.print_z})
        b->alloc(k);
    for (Buf<uint8_t> *b : {&out.type, &out.role, &out.filament, &out.nozzle, &out.color_id})
        b->alloc(k);
    out.layer_id.alloc(k);
    out.gcode_line.alloc(k);
    out.object_id.alloc(k);

    // The filament -> nozzle table (0-based nozzle per 0-based filament; 255, "no filament", maps to nozzle 0).
    std::vector<uint8_t> nozzle_of(256, 0);
    for (size_t f = 0; f < filament_maps.size() && f < 255; ++f)
        nozzle_of[f] = uint8_t(std::max(0, filament_maps[f] - 1));

    // Layers: Orca's layer ids restart for by-object printing and the first moves (start G-code) carry id 0 with
    // the first real layer at 1, so the engine renumbers by runs of equal id: layer_id[i] indexes `layers`, the
    // ranges are contiguous and in order (04 section 5.3).
    std::vector<float>    layer_z;
    std::vector<uint32_t> layer_first;
    uint32_t              current = 0;
    unsigned              previous_orca_id = 0;

    const size_t chunk = std::max<size_t>(1, move_chunk());
    for (size_t begin = 0; begin < k; begin += chunk) {
        const size_t end = std::min(k, begin + chunk);
        tbb::parallel_for(tbb::blocked_range<size_t>(begin, end, 16384), [&](const tbb::blocked_range<size_t> &range) {
            for (size_t i = range.begin(); i < range.end(); ++i) {
                const Move &m = res.moves[i];
                out.position.data()[3 * i] = m.position.x();
                out.position.data()[3 * i + 1] = m.position.y();
                out.position.data()[3 * i + 2] = m.position.z();
                out.type.data()[i] = uint8_t(m.type);
                out.role.data()[i] = uint8_t(m.extrusion_role);
                out.filament.data()[i] = m.extruder_id;  // Orca's extruder_id is the filament; its -1 is already 255
                out.nozzle.data()[i] = nozzle_of[m.extruder_id];
                out.color_id.data()[i] = m.cp_color_id;
                out.width.data()[i] = m.width;
                out.height.data()[i] = m.height;
                out.mm3_per_mm.data()[i] = m.mm3_per_mm;
                out.feedrate.data()[i] = m.feedrate;
                out.actual_feedrate.data()[i] = m.actual_feedrate;
                out.fan.data()[i] = m.fan_speed;
                out.temperature.data()[i] = m.temperature;
                out.pressure_advance.data()[i] = m.pressure_advance;
                out.acceleration.data()[i] = m.acceleration;
                out.jerk.data()[i] = m.jerk;
                out.time.data()[2 * i] = m.time[0];
                out.time.data()[2 * i + 1] = m.time[1];
                out.print_z.data()[i] = m.print_z;
                const int label = m.object_label_id;
                out.object_id.data()[i] = (label > 0 && size_t(label) <= object_count) ? label - 1 : -1;
                out.gcode_line.data()[i] = m.gcode_id ? m.gcode_id : 1;  // 1-based; the initial move has no line, 1 is as good as any
            }
        });
        // Sequential pass for the layer runs.
        for (size_t i = begin; i < end; ++i) {
            const Move &m = res.moves[i];
            // print_z is only filled from the Z_HEIGHT tag, which not every dialect writes; an extrusion's own Z is
            // the layer's Z then (travels may be lifted by z-hop, so they do not count).
            const float z = m.print_z > 0.f ? m.print_z : (m.type == EMoveType::Extrude ? m.position.z() : 0.f);
            if (i == 0 || m.layer_id != previous_orca_id) {
                if (i != 0)
                    ++current;
                layer_first.push_back(uint32_t(i));
                layer_z.push_back(z);
            } else {
                layer_z.back() = std::max(layer_z.back(), z);
            }
            previous_orca_id = m.layer_id;
            out.layer_id.data()[i] = current;
        }
        release_pages(res.moves.data() + begin, res.moves.data() + end);
    }

    // A leading run that never leaves the bed (the start G-code, print_z 0) is not a layer of its own: fold it into
    // the first real layer, so layers[0].z is the first layer's height and z increases for normal printing.
    if (layer_z.size() > 1 && layer_z.front() <= 0.f) {
        layer_z.erase(layer_z.begin());
        layer_first.erase(layer_first.begin());  // the second run is the new layer 0 and starts at move 0 (below)
        tbb::parallel_for(tbb::blocked_range<size_t>(0, k, 65536), [&](const tbb::blocked_range<size_t> &range) {
            for (size_t i = range.begin(); i < range.end(); ++i)
                out.layer_id.data()[i] = out.layer_id.data()[i] ? out.layer_id.data()[i] - 1 : 0;
        });
    }

    // moves.print_z: Orca's value where it has one, else the Z of the move's layer.
    tbb::parallel_for(tbb::blocked_range<size_t>(0, k, 65536), [&](const tbb::blocked_range<size_t> &range) {
        for (size_t i = range.begin(); i < range.end(); ++i)
            if (out.print_z.data()[i] <= 0.f)
                out.print_z.data()[i] = layer_z[out.layer_id.data()[i]];
    });

    const size_t layers = layer_z.size();
    out.layers = layers;
    out.layer_z.alloc(layers);
    out.layer_first.alloc(layers);
    out.layer_last.alloc(layers);
    for (size_t l = 0; l < layers; ++l) {
        out.layer_z.data()[l] = layer_z[l];
        out.layer_first.data()[l] = l == 0 ? 0 : layer_first[l];
        out.layer_last.data()[l] = l + 1 < layers ? layer_first[l + 1] - 1 : uint32_t(k ? k - 1 : 0);
    }

    out.line_ends.alloc(res.lines_ends.size());
    for (size_t i = 0; i < res.lines_ends.size(); ++i)
        out.line_ends.data()[i] = uint64_t(res.lines_ends[i]);

    res.moves.clear();
    res.moves.shrink_to_fit();
}

} // namespace slicewright
