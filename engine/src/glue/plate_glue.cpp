// SPDX-License-Identifier: AGPL-3.0-only
// Ported from OrcaSlicer v2.4.2 (AGPL-3.0-only); see plate_glue.hpp for the source references.
#include "plate_glue.hpp"

#include <miniz.h>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <cstring>
#include <stdexcept>

#include "libslic3r/ProjectTask.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/LocalesUtils.hpp"

namespace slicewright::glue {

using namespace Slic3r;

namespace {

std::atomic<bool> g_fail_for_tests{false};

// "model_id" of the printer model entry (profiles/BBL/machine/<printer_model>.json). The CLI looks it up in
// <resources>/profiles/BBL/machine_full/, which does not exist at v2.4.2, so its output has an empty id there;
// the firmware compares the id with the printer's, so it is filled in from the model entry that does exist.
std::string lookup_model_id(const std::string &archive, const std::string &printer_model)
{
    if (archive.empty() || printer_model.empty())
        return {};
    mz_zip_archive zip;
    std::memset(&zip, 0, sizeof zip);
    if (!mz_zip_reader_init_file(&zip, archive.c_str(), 0))
        return {};
    std::string id;
    const std::string name = "profiles/BBL/machine/" + printer_model + ".json";
    size_t size = 0;
    if (void *data = mz_zip_reader_extract_file_to_heap(&zip, name.c_str(), &size, 0)) {
        try {
            const auto j = nlohmann::json::parse(static_cast<const char *>(data), static_cast<const char *>(data) + size);
            if (j.contains("model_id") && j["model_id"].is_string())
                id = j["model_id"].get<std::string>();
        } catch (const std::exception &) {
        }
        mz_free(data);
    }
    mz_zip_reader_end(&zip);
    return id;
}

} // namespace

std::shared_ptr<PlateSnapshot> make_plate_snapshot(const Print &print, const DynamicPrintConfig &cfg, const Model &model, GCodeProcessorResult &result,
                                                   const std::string &gcode_path, const ThumbnailData *thumbnail, const std::string &profiles_archive)
{
    if (g_fail_for_tests.load())
        throw std::runtime_error("plate snapshot failure forced by a test");
    auto snap = std::make_shared<PlateSnapshot>();
    snap->model = model;
    snap->config = cfg;
    snap->bbl = print.is_BBL_printer();
    snap->gcode_path = gcode_path;
    snap->plate = std::make_unique<PlateData>();
    PlateData &plate = *snap->plate;

    // --- PartPlateList::store_to_3mf_structure (PartPlate.cpp:6141-6228), one plate -----------------------------
    plate.filament_maps = print.get_filament_maps();
    plate.locked = false;
    plate.plate_index = 0;
    for (size_t o = 0; o < model.objects.size(); ++o)
        for (size_t i = 0; i < model.objects[o]->instances.size(); ++i)
            plate.objects_and_instances.emplace_back(int(o), int(i));
    plate.gcode_file = gcode_path;
    plate.is_sliced_valid = true;
    plate.gcode_prediction = std::to_string((int) result.print_statistics.modes[static_cast<size_t>(PrintEstimatedStatistics::ETimeMode::Normal)].time);
    plate.toolpath_outside = result.toolpath_outside;
    plate.timelapse_warning_code = result.timelapse_warning_code;
    plate.is_label_object_enabled = result.label_object_enabled;
    plate.limit_filament_maps = result.limit_filament_maps;
    plate.layer_filaments = result.layer_filaments;
    plate.filament_change_sequence = result.filament_change_sequence;
    plate.nozzle_change_sequence = result.nozzle_change_sequence;
    plate.optimal_assignment = result.optimal_assignment;
    plate.first_layer_time = std::to_string(result.initial_layer_time);  // Plater.cpp:10704
    {
        const PrintStatistics &ps = print.print_statistics();
        if (ps.total_weight != 0.0) {
            CNumericLocalesSetter locales_setter;
            char buf[64];
            std::snprintf(buf, sizeof buf, "%.2f", ps.total_weight);
            plate.gcode_weight = buf;
        }
        plate.is_support_used = print.is_support_used();
    }
    plate.parse_filament_info(&result);

    // --- the CLI's per-plate additions (OrcaSlicer.cpp:6406-6422) ----------------------------------------------
    const std::string printer_model = cfg.option<ConfigOptionString>("printer_model") ? cfg.option<ConfigOptionString>("printer_model")->value : "";
    plate.printer_model_id = lookup_model_id(profiles_archive, printer_model);
    if (const auto *nozzles = cfg.option<ConfigOptionFloats>("nozzle_diameter"))
        plate.nozzle_diameters = nozzles->serialize();
    const auto *colours = cfg.option<ConfigOptionStrings>("filament_colour");
    const auto *ids = cfg.option<ConfigOptionStrings>("filament_ids");
    for (FilamentInfo &info : plate.slice_filaments_info) {
        std::string displayed;
        DynamicPrintConfig copy = cfg;  // get_filament_type is non-const
        info.type = copy.get_filament_type(displayed, info.id);
        info.color = colours && size_t(info.id) < colours->values.size() ? colours->get_at(info.id) : "#FFFFFF";
        info.filament_id = ids && size_t(info.id) < ids->values.size() ? ids->get_at(info.id) : "";
    }

    // --- the first-layer boxes of the plate (OrcaSlicer.cpp:6959-7035) -----------------------------------------
    auto bboxes = std::make_unique<PlateBBoxData>();
    BoundingBoxf bbox_all;
    if (const auto *seq = cfg.option<ConfigOptionEnum<PrintSequence>>("print_sequence"))
        bboxes->is_seq_print = seq->value == PrintSequence::ByObject;
    bboxes->first_extruder = int(print.get_tool_ordering().first_extruder());
    if (const auto *bed = cfg.option<ConfigOptionEnum<BedType>>("curr_bed_type"))
        bboxes->bed_type = bed_type_to_gcode_string(bed->value);
    if (const auto *nozzles = cfg.option<ConfigOptionFloats>("nozzle_diameter"); nozzles && !nozzles->values.empty())
        bboxes->nozzle_diameter = float(nozzles->get_at(std::min<size_t>(size_t(bboxes->first_extruder), nozzles->values.size() - 1)));
    bboxes->first_layer_time = result.initial_layer_time;
    for (PrintObject *obj : print.objects()) {
        BBoxData data;
        const BoundingBox bb_scaled = obj->get_first_layer_bbox(data.area, data.layer_height, data.name);
        const auto bb = unscaled(bb_scaled);
        bbox_all.merge(bb);
        data.area *= (SCALING_FACTOR * SCALING_FACTOR);  // unscale the area
        data.id = int(obj->id().id);
        data.bbox = {bb.min.x(), bb.min.y(), bb.max.x(), bb.max.y()};
        bboxes->bbox_objs.emplace_back(std::move(data));
    }
    if (print.has_wipe_tower()) {
        const Points corners = print.first_layer_wipe_tower_corners();
        if (!corners.empty()) {  // when loading a gcode.3mf the tower info may not be there
            const BoundingBox bb_scaled(corners[0], corners[2]);
            const auto bb = unscaled(bb_scaled);
            bbox_all.merge(bb);
            BBoxData data;
            data.name = "wipe_tower";
            data.id = 1000;  // plate index + 1000
            data.bbox = {bb.min.x(), bb.min.y(), bb.max.x(), bb.max.y()};
            bboxes->bbox_objs.emplace_back(std::move(data));
        }
    }
    if (bbox_all.defined)
        bboxes->bbox_all = {bbox_all.min.x(), bbox_all.min.y(), bbox_all.max.x(), bbox_all.max.y()};
    for (const FilamentInfo &info : plate.slice_filaments_info) {
        bboxes->filament_ids.push_back(info.id);
        bboxes->filament_colors.push_back(info.color);
    }
    snap->bboxes = std::move(bboxes);

    if (thumbnail && thumbnail->is_valid())
        snap->thumbnail.load_from(const_cast<ThumbnailData &>(*thumbnail));
    return snap;
}

void fail_plate_snapshots_for_tests(bool fail) { g_fail_for_tests.store(fail); }

std::string write_gcode_3mf(PlateSnapshot &snap, const std::string &path, const std::string &plate_name)
{
    try {
        snap.plate->plate_name = plate_name;
        // The writer names objects by the G-code's own scheme for non-Bambu dialects and reads the global flag for it.
        GCodeProcessor::s_IsBBLPrinter = snap.bbl;

        std::vector<ThumbnailData *> thumbnails, no_light, top, pick, calibration;
        if (snap.thumbnail.is_valid())
            thumbnails.push_back(&snap.thumbnail);
        std::vector<PlateBBoxData *> bboxes{snap.bboxes.get()};

        StoreParams params;
        params.path = path.c_str();
        params.model = &snap.model;
        params.plate_data_list = {snap.plate.get()};
        params.config = &snap.config;
        params.thumbnail_data = thumbnails;
        params.no_light_thumbnail_data = no_light;
        params.top_thumbnail_data = top;
        params.pick_thumbnail_data = pick;
        params.calibration_thumbnail_data = calibration;
        params.id_bboxes = bboxes;
        // The CLI's strategy (OrcaSlicer.cpp:7352): with G-code, quiet, split model, loaded ids, shared meshes.
        params.strategy = SaveStrategy::Silence | SaveStrategy::WithGcode | SaveStrategy::SplitModel | SaveStrategy::UseLoadedId | SaveStrategy::ShareMesh;
        params.export_plate_idx = -1;
        if (!store_bbs_3mf(params))
            return "cannot write " + path + ": Orca's 3MF writer failed";
        return {};
    } catch (const std::exception &e) {
        return "cannot write " + path + ": " + e.what();
    }
}

} // namespace slicewright::glue
