// SPDX-License-Identifier: AGPL-3.0-only
// Private test hooks (04 section 11: names with a leading underscore are unversioned).
#include <nanobind/stl/string.h>

#include <nanobind/nanobind.h>

#include <string>

#include "errors.hpp"
#include "testing.hpp"
#include "../glue/plate_glue.hpp"

#include "libslic3r/Format/bbs_3mf.hpp"
#include "libslic3r/LocalesUtils.hpp"
#include "libslic3r/Model.hpp"

namespace slicewright {

using namespace Slic3r;

namespace {

// Opens a .3mf with Orca's own reader (load_bbs_3mf, the code Orca and Bambu Studio use to open a .gcode.3mf) and
// reports what it found: the round-trip check of the writer (M5 acceptance).
nb::dict load_3mf_summary(const std::string &path)
{
    CNumericLocalesSetter locales;
    DynamicPrintConfig config;
    ConfigSubstitutionContext subst(ForwardCompatibilitySubstitutionRule::EnableSilent);
    Model model;
    PlateDataPtrs plates;
    std::vector<Preset *> presets;
    bool is_bbl = false, is_orca = false;
    Semver version;
    bool ok = false;
    {
        nb::gil_scoped_release release;
        ok = load_bbs_3mf(path.c_str(), &config, &subst, &model, &plates, &presets, &is_bbl, &is_orca, &version, nullptr,
                          LoadStrategy::LoadModel | LoadStrategy::LoadConfig | LoadStrategy::LoadAuxiliary | LoadStrategy::Silence);
    }
    nb::dict out;
    out["ok"] = ok;
    out["is_bbl_3mf"] = is_bbl;
    out["is_orca_3mf"] = is_orca;
    out["objects"] = model.objects.size();
    nb::list names;
    for (const ModelObject *o : model.objects)
        names.append(nb::str(o->name.c_str()));
    out["object_names"] = names;
    out["config_keys"] = config.keys().size();
    nb::list plate_list;
    for (PlateData *p : plates) {
        nb::dict d;
        d["plate_index"] = p->plate_index;
        d["plate_name"] = nb::str(p->plate_name.c_str());
        d["gcode_file"] = nb::str(p->gcode_file.c_str());
        d["gcode_file_md5"] = nb::str(p->gcode_file_md5.c_str());
        d["thumbnail_file"] = nb::str(p->thumbnail_file.c_str());
        d["is_sliced_valid"] = p->is_sliced_valid;
        d["gcode_prediction"] = nb::str(p->gcode_prediction.c_str());
        d["gcode_weight"] = nb::str(p->gcode_weight.c_str());
        d["first_layer_time"] = nb::str(p->first_layer_time.c_str());
        d["printer_model_id"] = nb::str(p->printer_model_id.c_str());
        d["nozzle_diameters"] = nb::str(p->nozzle_diameters.c_str());
        d["toolpath_outside"] = p->toolpath_outside;
        d["is_support_used"] = p->is_support_used;
        d["is_label_object_enabled"] = p->is_label_object_enabled;
        d["objects_and_instances"] = p->objects_and_instances.size();
        nb::list filaments;
        for (const FilamentInfo &f : p->slice_filaments_info) {
            nb::dict fd;
            fd["id"] = f.id;
            fd["type"] = nb::str(f.type.c_str());
            fd["color"] = nb::str(f.color.c_str());
            fd["used_m"] = f.used_m;
            fd["used_g"] = f.used_g;
            filaments.append(fd);
        }
        d["filaments"] = filaments;
        nb::list maps;
        for (int m : p->filament_maps)
            maps.append(m);
        d["filament_maps"] = maps;
        plate_list.append(d);
    }
    out["plates"] = plate_list;
    release_PlateData_list(plates);
    return out;
}

} // namespace

void bind_testing(nb::module_ &m)
{
    m.def("_load_3mf_summary", &load_3mf_summary, nb::arg("path"));
    // While set, capturing the data for a .gcode.3mf fails at the end of every job (it must not fail the slice).
    m.def("_fail_plate_snapshots", [](bool fail) { glue::fail_plate_snapshots_for_tests(fail); }, nb::arg("fail"));
}

} // namespace slicewright
