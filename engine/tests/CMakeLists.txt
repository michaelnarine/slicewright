# SPDX-License-Identifier: AGPL-3.0-only
#
# The Orca test subset (02 section 8, item 1), built against libslic3r_min. Orca's own tests/CMakeLists.txt
# is not used: it always builds the SLA, GUI-utility and (at newer tags) STEP and Draco tests. We list
# the sources ourselves from the patched Orca tree, so no Orca file has to change.
#
# Left out on purpose, with the reason:
#   libslic3r/test_bambu_networking.cpp   needs slic3r/Utils (the GUI library)
#   libslic3r/test_hollowing.cpp          needs OpenVDB (SLA hollowing is stubbed out, patch 0004)
#   libslic3r/test_png_io.cpp             disabled upstream
#   sla_print/, slic3rutils/, libnest2d/  SLA, GUI utilities, and an unrelated libnest2d test app
# Tests that no longer exist at v2.4.2 (test_seam_placer, test_multifilament, test_helpers.cpp) are simply
# absent from the tree.
set(ORCA_TESTS_DIR "${ORCA_DIR}/tests")

add_subdirectory("${ORCA_TESTS_DIR}/catch2" catch2)
if (MSVC)
    target_compile_definitions(Catch2WithMain PRIVATE DO_NOT_USE_WMAIN)
endif ()

file(TO_NATIVE_PATH "${ORCA_TESTS_DIR}/data" _test_data_dir)
add_library(slicewright_test_common INTERFACE)
target_compile_definitions(slicewright_test_common INTERFACE TEST_DATA_DIR=R"\(${_test_data_dir}\)" CATCH_CONFIG_FAST_COMPILE)
target_include_directories(slicewright_test_common INTERFACE "${ORCA_TESTS_DIR}")
if (APPLE)
    target_link_libraries(slicewright_test_common INTERFACE "-liconv -framework IOKit" "-framework CoreFoundation" -lc++)
endif ()

set(_libslic3r_sources
    libslic3r_tests.cpp
    test_3mf.cpp
    test_aabbindirect.cpp
    test_appconfig.cpp
    test_arachne_walls.cpp
    test_calib.cpp
    test_clipper_offset.cpp
    test_clipper_utils.cpp
    test_config.cpp
    test_elephant_foot_compensation.cpp
    test_geometry.cpp
    test_indexed_triangle_set.cpp
    test_marchingsquares.cpp
    test_meshboolean.cpp
    test_mutable_polygon.cpp
    test_mutable_priority_queue.cpp
    test_optimizers.cpp
    test_placeholder_parser.cpp
    test_polygon.cpp
    test_preset_bundle_loading.cpp
    test_preset_setting_id.cpp
    test_stl.cpp
    test_timeutils.cpp
    test_voronoi.cpp
)
list(TRANSFORM _libslic3r_sources PREPEND "${ORCA_TESTS_DIR}/libslic3r/")
add_executable(orca_libslic3r_tests ${_libslic3r_sources} "${ORCA_TESTS_DIR}/libnest2d/printer_parts.cpp")
target_link_libraries(orca_libslic3r_tests PRIVATE slicewright_test_common libslic3r_min Catch2::Catch2WithMain)

set(_fff_sources
    fff_print_tests.cpp
    test_data.cpp
    test_extrusion_entity.cpp
    test_fill.cpp
    test_flow.cpp
    test_gcode.cpp
    test_gcode_timing.cpp
    test_gcodewriter.cpp
    test_model.cpp
    test_print.cpp
    test_printgcode.cpp
    test_printobject.cpp
    test_skirt_brim.cpp
    test_support_material.cpp
    test_trianglemesh.cpp
)
list(TRANSFORM _fff_sources PREPEND "${ORCA_TESTS_DIR}/fff_print/")
add_executable(orca_fff_print_tests ${_fff_sources})
target_link_libraries(orca_fff_print_tests PRIVATE slicewright_test_common libslic3r_min Catch2::Catch2WithMain)

# One scenario is excluded: "Placeholder parser coFloatsOrPercents vector access" dereferences the null
# result of option<ConfigOptionFloatsOrPercentsNullable>("small_perimeter_speed"), but at v2.4.2 that
# option is a scalar coFloatOrPercent (PrintConfig.cpp), so the test is ahead of the source it ships
# with and crashes with SIGSEGV on the unpatched tree too. Re-check it on every Orca bump.
add_test(NAME orca_libslic3r
         COMMAND orca_libslic3r_tests "~Scenario: Placeholder parser coFloatsOrPercents vector access")
add_test(NAME orca_fff_print COMMAND orca_fff_print_tests)
set_tests_properties(orca_libslic3r orca_fff_print PROPERTIES TIMEOUT 1800)
