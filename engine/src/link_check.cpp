// SPDX-License-Identifier: AGPL-3.0-only
// Forces a full link of libslic3r_min (see engine/CMakeLists.txt): the config machinery, the model readers
// (STL, 3MF, OBJ, AMF), the print pipeline, G-code export and the G-code processor all have to resolve.
// Running it only prints the Orca version and slices nothing; the real tests are the Catch2 subset.
#include <cstdio>
#include <string>

#include "libslic3r/libslic3r.h"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/GCode/GCodeProcessor.hpp"

using namespace Slic3r;

int main(int argc, char **argv)
{
    DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
    config.normalize_fdm();

    Model model;
    if (argc > 1) // never taken by CI; keeps the readers referenced
        model = Model::read_from_file(argv[1], nullptr, nullptr, LoadStrategy::AddDefaultInstances);

    Print print;
    print.apply(model, config);
    GCodeProcessorResult result;
    if (argc > 2)
        print.export_gcode(argv[2], &result, nullptr);

    std::printf("orca %s, %zu config options\n", SoftFever_VERSION, config.keys().size());
    return 0;
}
