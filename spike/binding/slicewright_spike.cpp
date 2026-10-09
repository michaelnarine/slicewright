// SPDX-License-Identifier: AGPL-3.0-only
// Phase 0 spike binding: one function that slices an STL with flattened FFF profile JSON files.
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <chrono>
#include <cstdio>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "libslic3r/libslic3r.h"
#include "libslic3r/Utils.hpp"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/GCode/GCodeProcessor.hpp"

namespace nb = nanobind;
using namespace Slic3r;

namespace {

struct SliceOutcome {
    size_t layers = 0;
    size_t moves = 0;
    std::string time;
    double filament_mm = 0;
    double filament_g = 0;
    std::string gcode_path;
    double elapsed_ms = 0;
};

SliceOutcome do_slice(const std::string &stl_path, const std::vector<std::string> &profiles, const std::string &out_path)
{
    auto t0 = std::chrono::steady_clock::now();

    DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
    for (const std::string &p : profiles) {
        ConfigSubstitutionContext ctx(ForwardCompatibilitySubstitutionRule::EnableSilent);
        std::map<std::string, std::string> key_values;
        std::string reason;
        // Load into an EMPTY config and then apply. Deserialising straight into full_print_config() hits
        // coEnums options whose default instances were built without a keys_map (null), which segfaults in
        // ConfigOptionEnumsGeneric::deserialize. Empty configs create options via ConfigOptionDef (keys_map set),
        // which is also what OrcaSlicer's own CLI/GUI do.
        DynamicPrintConfig layer;
        int rc = layer.load_from_json(p, ctx, false, key_values, reason);
        if (rc != 0)
            throw std::runtime_error("cannot load profile " + p + ": " + reason);
        config.apply(layer, true);
    }
    config.normalize_fdm();

    Model model = Model::read_from_file(stl_path, nullptr, nullptr, LoadStrategy::AddDefaultInstances);

    // Bed centre from printable_area.
    Vec2d centre(0, 0);
    if (auto *pa = config.option<ConfigOptionPoints>("printable_area")) {
        BoundingBoxf bb(pa->values);
        centre = bb.center();
    }
    model.center_instances_around_point(centre);

    Print print;
    // Print::m_isBBLPrinter is never initialised in v2.4.2 (the CLI/GUI always assign it); leaving it is UB and
    // flips the G-code dialect (BBL-style FEATURE/CHANGE_LAYER comments, M486, M981). Set it like the CLI does.
    print.is_BBL_printer() = false;
    // Print::m_origin (Vec3d) is likewise uninitialised; garbage here produced X9223372036854775.807 coordinates
    // whenever the heap was not fresh (Blender GUI, -t 1). Model is centred on the bed already, so origin is zero.
    print.set_plate_origin(Vec3d::Zero());
    for (ModelObject *mo : model.objects) {
        mo->ensure_on_bed();
        print.auto_assign_extruders(mo);
    }
    print.apply(model, config);
    StringObjectException err = print.validate();
    if (!err.string.empty())
        throw std::runtime_error("print validation failed: " + err.string);
    print.set_status_silent();
    print.process();

    GCodeProcessorResult result;
    print.export_gcode(out_path, &result, nullptr);

    SliceOutcome o;
    for (const PrintObject *obj : print.objects())
        o.layers += obj->layer_count();
    o.moves = result.moves.size();
    o.time = print.print_statistics().estimated_normal_print_time;
    o.filament_mm = print.print_statistics().total_used_filament;
    o.filament_g = print.print_statistics().total_weight;
    o.gcode_path = out_path;
    o.elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    return o;
}

} // namespace

NB_MODULE(slicewright_engine, m)
{
    m.doc() = "Slicewright Phase 0 native spike (OrcaSlicer v2.4.2 libslic3r, in-process)";
    set_logging_level(1);

    m.def("version", []() { return std::string(SoftFever_VERSION); });

    m.def(
        "slice_stl",
        [](const std::string &stl, const std::vector<std::string> &profiles, const std::string &out) {
            SliceOutcome o;
            {
                nb::gil_scoped_release release;
                o = do_slice(stl, profiles, out);
            }
            nb::dict d;
            d["layers"] = o.layers;
            d["moves"] = o.moves;
            d["estimated_time"] = o.time;
            d["filament_mm"] = o.filament_mm;
            d["filament_g"] = o.filament_g;
            d["gcode"] = o.gcode_path;
            d["elapsed_ms"] = o.elapsed_ms;
            return d;
        },
        nb::arg("stl"), nb::arg("profiles"), nb::arg("gcode_out"));
}
