// SPDX-License-Identifier: AGPL-3.0-only
// Ported from OrcaSlicer v2.4.2 (AGPL-3.0-only); see arrange_glue.hpp for the source references.
#include "arrange_glue.hpp"

#include <algorithm>
#include <cmath>

#include "libslic3r/Arrange.hpp"
#include "libslic3r/BoundingBox.hpp"
#include "libslic3r/GCode/WipeTower.hpp"
#include "libslic3r/ModelArrange.hpp"
#include "libslic3r/libslic3r.h"

#include "print_glue.hpp"

namespace slicewright::glue {

using namespace Slic3r;

namespace {

// PartPlate.cpp:66-70.
constexpr float kTowerDefaultX = 165.f, kTowerDefaultY = 250.f;
constexpr float kI3TowerDefaultX = 0.f, kI3TowerDefaultY = 250.f;

double float_opt(const DynamicPrintConfig &cfg, const char *key, double fallback)
{
    const ConfigOption *o = cfg.option(key);
    return o ? o->getFloat() : fallback;
}

BoundingBoxf bed_box(const DynamicPrintConfig &cfg)
{
    BoundingBoxf box;
    if (const auto *area = cfg.option<ConfigOptionPoints>("printable_area"))
        for (const Vec2d &p : area->values)
            box.merge(p);
    return box;
}

bool by_object(const DynamicPrintConfig &cfg)
{
    const auto *seq = cfg.option<ConfigOptionEnum<PrintSequence>>("print_sequence");
    return seq && seq->value == PrintSequence::ByObject;
}

// PartPlate::estimate_wipe_tower_size (PartPlate.cpp:2186-2259), for a plate that holds every object of the model.
Vec3d estimate_tower_size(const Model &model, const DynamicPrintConfig &cfg, double w, double wipe_volume, int extruder_count, int plate_extruder_size)
{
    Vec3d size = Vec3d::Zero();
    double layer_height = float_opt(cfg, "layer_height", 0.08);
    if (plate_extruder_size == 0)
        return size;
    double max_height = 0;
    for (const ModelObject *mo : model.objects)
        max_height = std::max(max_height, mo->bounding_box_exact().size().z());
    size(2) = max_height;

    const auto *timelapse = cfg.option<ConfigOptionEnum<TimelapseType>>("timelapse_type");
    const auto *wrapping = cfg.option<ConfigOptionBool>("enable_wrapping_detection");
    const bool need_wipe_tower = (timelapse && timelapse->value == TimelapseType::tlSmooth) || (wrapping && wrapping->value);
    const double extra_spacing = float_opt(cfg, "prime_tower_infill_gap", 150) / 100.;
    const auto *rib_opt = cfg.option<ConfigOptionEnum<WipeTowerWallType>>("wipe_tower_wall_type");
    const bool use_rib_wall = rib_opt && rib_opt->value == WipeTowerWallType::wtwRib;
    double rib_width = float_opt(cfg, "wipe_tower_rib_width", 8);

    double filament_change_volume = 0.;
    {
        const auto *lengths = cfg.option<ConfigOptionFloats>("filament_change_length");
        const double length = lengths && !lengths->values.empty() ? *std::max_element(lengths->values.begin(), lengths->values.end()) : 0.;
        const auto *diameters = cfg.option<ConfigOptionFloats>("filament_diameter");
        const double diameter = diameters && !diameters->values.empty() ? *std::max_element(diameters->values.begin(), diameters->values.end()) : 1.75;
        filament_change_volume = length * PI * diameter * diameter / 4.;
    }
    double volume = wipe_volume * (extruder_count == 2 ? plate_extruder_size : (plate_extruder_size - 1));
    if (extruder_count == 2)
        volume += filament_change_volume * (plate_extruder_size / 2);

    double depth;
    if (use_rib_wall) {
        depth = std::sqrt(volume / layer_height * extra_spacing);
        if (need_wipe_tower || plate_extruder_size > 1) {
            const float min_depth = WipeTower::get_limit_depth_by_height(float(max_height));
            const double volume_depth = depth;
            depth = std::max(double(min_depth), depth);
            rib_width = std::min(rib_width, depth / 2);
            depth = rib_width / std::sqrt(2) + std::max(depth + float_opt(cfg, "wipe_tower_extra_rib_length", 0), volume_depth);
            size(0) = size(1) = depth;
        }
    } else {
        depth = volume / (layer_height * w) * extra_spacing;
        if (need_wipe_tower || depth > EPSILON) {
            const float min_depth = WipeTower::get_limit_depth_by_height(float(max_height));
            depth = std::max(double(min_depth), depth);
        }
        size(0) = w;
        size(1) = depth;
    }
    return size;
}

} // namespace

bool estimate_wipe_tower(const Model &model, const DynamicPrintConfig &cfg, double &x_out, double &y_out, double &w_out, double &depth_out)
{
    const size_t filaments = filament_count_of(cfg);
    const auto *enable = cfg.option<ConfigOptionBool>("enable_prime_tower");
    if (by_object(cfg) || filaments <= 1 || (enable && !enable->value))
        return false;  // OrcaSlicer.cpp:4706: a tower only for several filaments printed by layer

    const auto *printer_structure = cfg.option<ConfigOptionEnum<PrinterStructure>>("printer_structure");
    const bool i3 = printer_structure && printer_structure->value == PrinterStructure::psI3;
    const float tower_brim_width = float(float_opt(cfg, "prime_tower_brim_width", 3));
    const float tower_margin = float(WIPE_TOWER_MARGIN) + float(float_opt(cfg, "prime_tower_width", 35));
    // OrcaSlicer.cpp:4706-4740: the default position, raised to the margin, unless the config has a position
    float x = i3 ? kI3TowerDefaultX : kTowerDefaultX, y = i3 ? kI3TowerDefaultY : kTowerDefaultY;
    x = std::max(x, tower_margin);
    y = std::max(y, tower_margin);
    if (const auto *wx = cfg.option<ConfigOptionFloats>("wipe_tower_x"); wx && !wx->values.empty())
        x = float(wx->values.front());
    if (const auto *wy = cfg.option<ConfigOptionFloats>("wipe_tower_y"); wy && !wy->values.empty())
        y = float(wy->values.front());

    const double w = float_opt(cfg, "prime_tower_width", 35), v = float_opt(cfg, "prime_volume", 45);
    const int extruder_count = int(extruder_count_of(cfg));
    const Vec3d size = estimate_tower_size(model, cfg, w, v, extruder_count, int(filaments));
    float brim = tower_brim_width < 0 ? WipeTower::get_auto_brim_by_height(float(size.z())) : tower_brim_width;

    // PartPlate::estimate_wipe_tower_polygon (PartPlate.cpp:2261-2306): the position is clamped into the bed.
    const BoundingBoxf bed = bed_box(cfg);
    const float margin = float(WIPE_TOWER_MARGIN) + tower_brim_width;
    const float plate_w = float(bed.size().x()), plate_d = float(bed.size().y());
    x = std::clamp(x, margin, std::max(margin, plate_w - float(w) - margin - brim));
    y = std::clamp(y, margin, std::max(margin, plate_d - float(size.y()) - margin - brim));
    x_out = x - brim;
    y_out = y - brim;
    w_out = w + 2 * brim;
    depth_out = size.y() + 2 * brim;
    return true;
}

std::vector<Placed> arrange_model(const Model &model, const DynamicPrintConfig &cfg, double spacing_mm, bool allow_rotation)
{
    using namespace arrangement;
    ArrangeParams params;
    params.min_obj_distance = spacing_mm >= 0 ? scaled(spacing_mm) : 0;
    params.allow_rotations = allow_rotation;
    params.is_seq_print = by_object(cfg);
    params.printable_height = float(float_opt(cfg, "printable_height", 250));
    params.progressind = [](unsigned, std::string) {};
    params.parallel = true;
    if (const auto *structure = cfg.option<ConfigOptionEnum<PrinterStructure>>("printer_structure"))
        params.align_to_y_axis = structure->value == PrinterStructure::psI3;

    ArrangePolygons selected, unselected;
    std::vector<Placed> placed;
    for (size_t i = 0; i < model.objects.size(); ++i) {
        for (ModelInstance *instance : model.objects[i]->instances) {
            ArrangePolygon ap = get_instance_arrange_poly(instance, cfg);
            ap.itemid = int(selected.size());
            ap.name = model.objects[i]->name;
            Placed p;
            p.index = i;
            p.old_x = unscaled<double>(ap.translation.x());
            p.old_y = unscaled<double>(ap.translation.y());
            placed.push_back(p);
            selected.emplace_back(std::move(ap));
            break;  // v1: one instance per object
        }
    }

    // The wipe tower as an obstacle (OrcaSlicer.cpp:4706-4760).
    double tx, ty, tw, td;
    if (estimate_wipe_tower(model, cfg, tx, ty, tw, td)) {
        ArrangePolygon tower;
        tower.poly.contour = Polygon({{scaled(tx), scaled(ty)}, {scaled(tx + tw), scaled(ty)}, {scaled(tx + tw), scaled(ty + td)}, {scaled(tx), scaled(ty + td)}});
        tower.bed_idx = 0;
        tower.setter = nullptr;  // do not move the tower
        tower.name = "WipeTower";
        tower.is_virt_object = true;
        tower.is_wipe_tower = true;
        unselected.emplace_back(std::move(tower));
    }

    // Exclude areas: PartPlateList::preprocess_exclude_areas (PartPlate.cpp:5454-5510). Groups of four points are the
    // rectangles of the bed's exclude areas; they are virtual objects in `unselected` and, inflated by 1 mm, in
    // `excluded_regions` (OrcaSlicer.cpp:4761, 4789).
    auto add_excluded = [&](ArrangePolygons &out, float inflation) {
        const auto *excl = cfg.option<ConfigOptionPoints>("bed_exclude_area");
        if (!excl)
            return;
        for (size_t i = 0, group = 0; i + 3 < excl->values.size(); i += 4, ++group) {
            BoundingBoxf box;
            for (size_t k = 0; k < 4; ++k)
                box.merge(excl->values[i + k]);
            ArrangePolygon ap;
            ap.poly.contour = Polygon({{scaled(box.min.x()), scaled(box.min.y())}, {scaled(box.max.x()), scaled(box.min.y())},
                                       {scaled(box.max.x()), scaled(box.max.y())}, {scaled(box.min.x()), scaled(box.max.y())}});
            ap.translation = Vec2crd(0, 0);
            ap.rotation = 0.0f;
            ap.is_virt_object = true;
            ap.bed_idx = 0;
            ap.height = 1;
            ap.name = "ExcludedRegion" + std::to_string(group);
            ap.inflation = coord_t(inflation);
            out.emplace_back(std::move(ap));
        }
    };
    add_excluded(unselected, 0);
    update_arrange_params(params, &cfg, selected);
    update_selected_items_inflation(selected, &cfg, params);
    update_unselected_items_inflation(unselected, &cfg, params);
    update_selected_items_axis_align(selected, &cfg, params);
    add_excluded(params.excluded_regions, float(scaled(1.)));
    const Points bed = get_shrink_bedpts(&cfg, params);

    if (!selected.empty())
        arrangement::arrange(selected, unselected, bed, params);

    for (size_t i = 0; i < selected.size(); ++i) {
        const ArrangePolygon &ap = selected[i];
        Placed &p = placed[i];
        p.fitted = ap.bed_idx == 0;
        p.new_x = unscaled<double>(ap.translation.x());
        p.new_y = unscaled<double>(ap.translation.y());
        p.rotation = ap.rotation;
        p.dx = p.new_x - p.old_x;
        p.dy = p.new_y - p.old_y;
    }
    return placed;
}

} // namespace slicewright::glue
