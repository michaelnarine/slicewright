// SPDX-License-Identifier: AGPL-3.0-only
#include "checks.hpp"

#include "libslic3r/BoundingBox.hpp"
#include "libslic3r/ClipperUtils.hpp"
#include "libslic3r/Polygon.hpp"

namespace slicewright {

using namespace Slic3r;

size_t filament_count(const DynamicPrintConfig &cfg)
{
    const auto *colours = cfg.option<ConfigOptionStrings>("filament_colour");
    return colours && !colours->values.empty() ? colours->values.size() : 1;
}

namespace {

constexpr double kTolMm = 0.05;  // an object flush with the bed edge (arrange output, rounding) is inside

Polygon scaled_polygon(const Pointfs &pts)
{
    Polygon p;
    for (const Vec2d &v : pts)
        p.points.emplace_back(scaled(v.x()), scaled(v.y()));
    return p;
}

} // namespace

std::vector<Issue> check_paint_range(const std::vector<std::string> &names, const std::vector<uint8_t> &max_face_extruder,
                                     const DynamicPrintConfig &cfg)
{
    std::vector<Issue> out;
    const size_t limit = std::min<size_t>(16, filament_count(cfg));
    for (size_t i = 0; i < max_face_extruder.size() && i < names.size(); ++i)
        if (max_face_extruder[i] > limit)
            out.push_back({"error", "paint_out_of_range",
                           "Object '" + names[i] + "' paints faces with filament " + std::to_string(max_face_extruder[i]) + ", but only " +
                               std::to_string(limit) + (limit == 1 ? " filament is" : " filaments are") +
                               " configured (Orca would silently ignore the paint).",
                           "filament_colour", names[i]});
    return out;
}

std::vector<Issue> check_bed_and_height(const Model &model, const std::vector<std::string> &names, const DynamicPrintConfig &cfg)
{
    std::vector<Issue> out;
    const auto *area_opt = cfg.option<ConfigOptionPoints>("printable_area");
    const auto *excl = cfg.option<ConfigOptionPoints>("bed_exclude_area");
    const auto *height = cfg.option<ConfigOptionFloat>("printable_height");

    Polygons bed;
    if (area_opt && area_opt->values.size() >= 3)
        bed = offset(Polygons{scaled_polygon(area_opt->values)}, float(scaled(kTolMm)));
    // Exclude areas come in groups of four points, each group a rectangle (PartPlate::calc_bounding_box).
    Polygons excluded;
    if (excl) {
        for (size_t i = 0; i + 3 < excl->values.size(); i += 4) {
            BoundingBoxf box;
            for (size_t k = 0; k < 4; ++k)
                box.merge(excl->values[i + k]);
            Polygon rect = scaled(box).polygon();
            Polygons shrunk = offset(Polygons{rect}, -float(scaled(kTolMm)));
            append(excluded, std::move(shrunk));
        }
    }

    for (size_t i = 0; i < model.objects.size(); ++i) {
        const ModelObject *mo = model.objects[i];
        if (mo->instances.empty())
            continue;
        const std::string &name = i < names.size() ? names[i] : mo->name;
        bool outside = false;
        if (!bed.empty()) {
            Polygon hull = mo->convex_hull_2d(mo->instances.front()->get_matrix());
            if (hull.points.size() >= 3) {
                Polygons outside_part = diff(Polygons{hull}, bed);
                outside = area(outside_part) > scaled(0.01) * scaled(0.01);
                if (!outside && !excluded.empty())
                    outside = area(intersection(Polygons{hull}, excluded)) > scaled(0.01) * scaled(0.01);
            }
        }
        if (outside)
            out.push_back({"error", "object_outside_bed",
                           "Object '" + name + "' is outside the printable area (printable_area minus bed_exclude_area).",
                           "printable_area", name});
        if (height && height->value > 0) {
            const BoundingBoxf3 bb = mo->instance_bounding_box(0);
            if (bb.defined && bb.size().z() > height->value + 1e-3)
                out.push_back({"error", "object_too_tall",
                               "Object '" + name + "' is " + std::to_string(bb.size().z()) + " mm tall; the printer's printable_height is " +
                                   std::to_string(height->value) + " mm.",
                               "printable_height", name});
        }
    }
    return out;
}

} // namespace slicewright
