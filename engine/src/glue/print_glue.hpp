// SPDX-License-Identifier: AGPL-3.0-only
//
// CLI/GUI glue that Orca's slicing path needs and that lives outside libslic3r (02 section 5.9). Orca-derived code:
// everything in engine/src/glue/ is ported from OrcaSlicer v2.4.2 (AGPL-3.0-only) and says where from.
#pragma once

#include "libslic3r/Print.hpp"
#include "libslic3r/PrintConfig.hpp"

namespace slicewright::glue {

// OrcaSlicer.cpp:5987-6040 (the per-plate path of the CLI), applied to the config that goes to Print::apply:
//  * on a printer with several extruders and an automatic filament map mode, `extruder_ams_count` and the per-nozzle
//    filament info (four default slots per nozzle, the filament types taken in turn) are set up and handed to
//    Print::set_extruder_filament_info, which the auto-mapping reads (5987-6023);
//  * `filament_map` has one entry per filament, all 1 on a single-extruder printer (6027-6034);
//  * `nozzle_volume_type` has one entry per extruder, `Standard` where the config has none (6035-6040).
void prepare_print_config(Slic3r::DynamicPrintConfig &cfg, Slic3r::Print &print);

// OrcaSlicer.cpp:6046-6059: the Bambu dialect is chosen from `printer_model` (a vendor preset starts with "Bambu Lab").
bool is_bbl_vendor_preset(const Slic3r::DynamicPrintConfig &cfg);

// OrcaSlicer.cpp:6138-6139: the process-global tables that Brim.cpp:138 and the speed code read. Per job, after
// Print::apply, because they are rebuilt from the job's own config (`Model::extruderParamsMap`, `printSpeedMap`).
void set_global_tables(const Slic3r::DynamicPrintConfig &cfg, const Slic3r::Print &print);

// Number of filaments and of extruders the config describes.
size_t filament_count_of(const Slic3r::DynamicPrintConfig &cfg);
size_t extruder_count_of(const Slic3r::DynamicPrintConfig &cfg);

} // namespace slicewright::glue
