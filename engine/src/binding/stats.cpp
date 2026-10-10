// SPDX-License-Identifier: AGPL-3.0-only
#include "stats.hpp"

#include <cmath>
#include <cstdio>

#include "runtime.hpp"

namespace slicewright {

using namespace Slic3r;

namespace {

std::string fixed(double v, int decimals)
{
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.*f", decimals, v);
    return buf;
}

} // namespace

Stats compute_stats(const GCodeProcessorResult &res, const Print &print, const ResultStore &moves)
{
    Stats s;
    const auto &modes = res.print_statistics.modes;
    s.time_normal = modes[size_t(PrintEstimatedStatistics::ETimeMode::Normal)].time;
    s.time_silent = modes[size_t(PrintEstimatedStatistics::ETimeMode::Stealth)].time;
    s.prepare_time = modes[size_t(PrintEstimatedStatistics::ETimeMode::Normal)].prepare_time;

    // Time per role (extrusion moves) and per move type (everything else), summed from the per-move durations.
    constexpr size_t kRoles = 32, kTypes = 16;  // the enums() guarantees: role < 32, move type < 16
    double by_role[kRoles][2] = {}, by_type[kTypes][2] = {};
    double travel_mm = 0;
    const int extrude = int(EMoveType::Extrude), travel = int(EMoveType::Travel);
    for (size_t i = 0; i < moves.k; ++i) {
        const int type = moves.type.data()[i];
        const double t0 = moves.time.data()[2 * i], t1 = moves.time.data()[2 * i + 1];
        if (type == extrude && moves.role.data()[i] < kRoles) {
            by_role[moves.role.data()[i]][0] += t0;
            by_role[moves.role.data()[i]][1] += t1;
        } else if (type < int(kTypes)) {
            by_type[type][0] += t0;
            by_type[type][1] += t1;
        }
        if (type == travel && i > 0) {
            const float *a = moves.position.data() + 3 * (i - 1), *b = moves.position.data() + 3 * i;
            travel_mm += std::sqrt(double(b[0] - a[0]) * (b[0] - a[0]) + double(b[1] - a[1]) * (b[1] - a[1]) + double(b[2] - a[2]) * (b[2] - a[2]));
        }
    }
    for (size_t r = 0; r < kRoles; ++r)
        if (by_role[r][0] != 0 || by_role[r][1] != 0)
            s.time_by_role.push_back({role_name(int(r)), {by_role[r][0], by_role[r][1]}});
    for (size_t t = 0; t < kTypes; ++t)
        if (by_type[t][0] != 0 || by_type[t][1] != 0)
            s.time_by_move_type.push_back({move_type_name(int(t)), {by_type[t][0], by_type[t][1]}});
    s.travel_mm = travel_mm;

    // Per-filament material (the formulas of GCode.cpp:1914-1930): volume mm3 -> length, cm3, grams, cost.
    const auto &volumes = res.print_statistics.total_volumes_per_extruder;
    size_t n = std::max<size_t>(1, res.filaments_count);
    for (const auto &kv : volumes)
        n = std::max(n, kv.first + 1);
    s.filaments.assign(n, {});
    for (const auto &kv : volumes) {
        const size_t f = kv.first;
        const double volume = kv.second;
        const double diameter = f < res.filament_diameters.size() ? res.filament_diameters[f] : 1.75;
        const double density = f < res.filament_densities.size() ? res.filament_densities[f] : 1.24;
        const double cost_per_kg = f < res.filament_costs.size() ? res.filament_costs[f] : 0.;
        const double area = M_PI * (0.5 * diameter) * (0.5 * diameter);
        Stats::Filament &fil = s.filaments[f];
        fil.mm = area > 0 ? volume / area : 0.;
        fil.cm3 = volume * 0.001;
        fil.g = volume * density * 0.001;
        fil.cost = fil.g * cost_per_kg * 0.001;
    }
    for (const auto &kv : res.print_statistics.used_filaments_per_role)
        s.used_per_role.push_back({role_name(int(kv.first)), {kv.second.first, kv.second.second}});
    s.flush_g.assign(n, 0.);
    for (const auto &kv : res.print_statistics.flush_per_filament)
        if (kv.first < n)
            s.flush_g[kv.first] = kv.second * (kv.first < res.filament_densities.size() ? res.filament_densities[kv.first] : 1.24) * 0.001;

    s.filament_changes = int(res.print_statistics.total_filament_changes);
    s.tool_changes = int(res.print_statistics.total_extruder_changes);
    s.layer_count = moves.layers;

    // Orca's own display strings (print_statistics), for parity with what its GUI shows.
    const PrintStatistics &ps = print.print_statistics();
    s.display = {{"estimated_normal_print_time", ps.estimated_normal_print_time},
                 {"estimated_silent_print_time", ps.estimated_silent_print_time},
                 {"total_used_filament_mm", fixed(ps.total_used_filament, 2)},
                 {"total_extruded_volume_cm3", fixed(ps.total_extruded_volume * 0.001, 2)},
                 {"total_weight_g", fixed(ps.total_weight, 2)},
                 {"total_cost", fixed(ps.total_cost, 2)},
                 {"total_toolchanges", std::to_string(ps.total_toolchanges)}};
    return s;
}

} // namespace slicewright
