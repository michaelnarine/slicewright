// SPDX-License-Identifier: AGPL-3.0-only
// Ported from OrcaSlicer v2.4.2, src/OrcaSlicer.cpp (AGPL-3.0-only); see print_glue.hpp for the line references.
#include "print_glue.hpp"

#include <algorithm>
#include <string>
#include <vector>

#include "libslic3r/Model.hpp"

namespace slicewright::glue {

using namespace Slic3r;

size_t filament_count_of(const DynamicPrintConfig &cfg)
{
    const auto *colours = cfg.option<ConfigOptionStrings>("filament_colour");
    return colours && !colours->values.empty() ? colours->values.size() : 1;
}

size_t extruder_count_of(const DynamicPrintConfig &cfg)
{
    const auto *nozzles = cfg.option<ConfigOptionFloats>("nozzle_diameter");
    return nozzles && !nozzles->values.empty() ? nozzles->values.size() : 1;
}

void prepare_print_config(DynamicPrintConfig &cfg, Print &print)
{
    const size_t filament_count = filament_count_of(cfg);
    const int    extruder_count = int(extruder_count_of(cfg));

    // OrcaSlicer.cpp:5987-6023
    if (extruder_count > 1) {
        FilamentMapMode map_mode = fmmAutoForFlush;
        if (const auto *opt = cfg.option<ConfigOptionEnum<FilamentMapMode>>("filament_map_mode"))
            map_mode = opt->value;
        if (map_mode < fmmManual) {
            // Defaults for the automatic mapping: per extruder four slots ("1#0|4#1"), colour white, the types in turn.
            std::vector<std::string> extruder_ams_count(extruder_count, "");
            std::vector<std::vector<DynamicPrintConfig>> extruder_filament_info(extruder_count, std::vector<DynamicPrintConfig>());
            int color_count = 0;
            const auto *filament_type = dynamic_cast<const ConfigOptionStrings *>(cfg.option("filament_type"));
            const std::vector<std::string> types = filament_type && !filament_type->values.empty() ? filament_type->vserialize()
                                                                                                    : std::vector<std::string>{"PLA"};
            for (int e = 0; e < extruder_count; ++e) {
                extruder_ams_count[e] = "1#0|4#1";
                for (int slot = 0; slot < 4; ++slot) {
                    DynamicPrintConfig temp;
                    temp.option<ConfigOptionStrings>("filament_colour", true)->values = {"#FFFFFFFF"};
                    temp.option<ConfigOptionStrings>("filament_type", true)->values = {types[color_count % types.size()]};
                    temp.option<ConfigOptionBools>("filament_is_support", true)->values = {0};
                    extruder_filament_info[e].push_back(std::move(temp));
                    ++color_count;
                }
            }
            cfg.option<ConfigOptionStrings>("extruder_ams_count", true)->values = extruder_ams_count;
            print.set_extruder_filament_info(extruder_filament_info);
        }
    }

    // OrcaSlicer.cpp:6027-6034
    std::vector<int> &filament_map = cfg.option<ConfigOptionInts>("filament_map", true)->values;
    if (filament_map.size() < filament_count)
        filament_map.resize(filament_count, 1);
    if (extruder_count == 1)
        for (size_t i = 0; i < filament_count; ++i)
            filament_map[i] = 1;

    // OrcaSlicer.cpp:6035-6040 (the CLI tests has("nozzle_volume_type"); a full config always has the key, so the
    // length is what decides here)
    // Built from the option definition, not taken from the config: the default instance inside full_print_config()
    // has a null keys_map for enum vectors (02 section 5.1), which crashes as soon as the value is serialised.
    {
        std::vector<int> values;
        if (const auto *existing = cfg.option<ConfigOptionEnumsGeneric>("nozzle_volume_type"))
            values = existing->values;
        if (values.size() < size_t(extruder_count))
            values.resize(extruder_count, int(nvtStandard));
        // Always replaced, even when long enough: the instance from the defaults would serialise to nothing.
        const ConfigOptionDef *def = print_config_def.get("nozzle_volume_type");
        auto *opt = static_cast<ConfigOptionEnumsGeneric *>(def->create_default_option());
        opt->values = std::move(values);
        cfg.set_key_value("nozzle_volume_type", opt);
    }
}

bool is_bbl_vendor_preset(const DynamicPrintConfig &cfg)
{
    const auto *opt = cfg.option<ConfigOptionString>("printer_model");
    const std::string model = opt ? opt->value : std::string();
    return !model.empty() && model.compare(0, 9, "Bambu Lab") == 0;
}

void set_global_tables(const DynamicPrintConfig &cfg, const Print &print)
{
    Model::setExtruderParams(cfg, int(filament_count_of(cfg)));
    Model::setPrintSpeedTable(cfg, print.config());
}

} // namespace slicewright::glue
