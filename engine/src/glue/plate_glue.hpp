// SPDX-License-Identifier: AGPL-3.0-only
//
// The `.gcode.3mf` PlateData filling that the Orca CLI gets from GUI code (02 section 5.9). Orca-derived: ported
// from OrcaSlicer v2.4.2, src/libslic3r/../slic3r/GUI/PartPlate.cpp:6141-6228 (PartPlateList::store_to_3mf_structure),
// src/OrcaSlicer.cpp:6351-6540 (the per-plate additions: printer model id, nozzle diameters, filament type/colour/id)
// and :6959-7035 (the first-layer boxes of the plate), and Plater.cpp:10704 (first_layer_time). The writer itself is
// Orca's store_bbs_3mf (libslic3r/Format/bbs_3mf.cpp), used as is.
#pragma once

#include <memory>
#include <string>

#include "libslic3r/Format/bbs_3mf.hpp"
#include "libslic3r/GCode/GCodeProcessor.hpp"
#include "libslic3r/GCode/ThumbnailData.hpp"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"

namespace slicewright::glue {

// Everything store_bbs_3mf needs, captured at the end of a job while the Print and the processor result still exist.
// A SliceResult keeps one so that write_gcode_3mf works after the job is gone.
struct PlateSnapshot {
    Slic3r::Model                 model;       // a copy: the writer may modify it
    Slic3r::DynamicPrintConfig    config;      // the config the print was applied with (project_settings.config)
    std::unique_ptr<Slic3r::PlateData>     plate;
    std::unique_ptr<Slic3r::PlateBBoxData> bboxes;
    Slic3r::ThumbnailData         thumbnail;   // plate_1.png; invalid when none was provided
    bool                          bbl = false; // the G-code dialect (the writer reads the process-global flag)
    std::string                   gcode_path;
};

// `profiles_archive`: the wheel's profiles.zip, where the Bambu model id is looked up from `printer_model`.
std::shared_ptr<PlateSnapshot> make_plate_snapshot(const Slic3r::Print &print, const Slic3r::DynamicPrintConfig &cfg,
                                                   const Slic3r::Model &model, Slic3r::GCodeProcessorResult &result,
                                                   const std::string &gcode_path, const Slic3r::ThumbnailData *thumbnail,
                                                   const std::string &profiles_archive);

// Test hook: make_plate_snapshot throws while this is set (the failure path of a job: no 3MF data, the slice is kept).
void fail_plate_snapshots_for_tests(bool fail);

// Writes the file. Returns an empty string on success, else the error. Does not touch Python. The caller must hold the
// engine lock: the writer reads GCodeProcessor::s_IsBBLPrinter, which this sets from the snapshot.
std::string write_gcode_3mf(PlateSnapshot &snapshot, const std::string &path, const std::string &plate_name);

} // namespace slicewright::glue
