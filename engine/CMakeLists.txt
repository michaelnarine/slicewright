# SPDX-License-Identifier: AGPL-3.0-only
#
# Slicewright engine root (02 section 3.1). This is NOT Orca's top-level CMakeLists.txt: it builds the
# patched libslic3r (SLIC3R_HEADLESS_MINIMAL=ON) from the tree that engine/tools/apply_patches.py prepares,
# against the dependencies that engine/deps builds, plus our stubs, and exposes it as `libslic3r_min`.
#
#   python engine/tools/apply_patches.py                       # -> engine/build/orca-src
#   cmake -S engine/deps -B engine/build/deps -G Ninja && cmake --build engine/build/deps
#   cmake -S engine -B engine/build/engine -G Ninja
#   cmake --build engine/build/engine --target slicewright_link_check
#
# macOS arm64 only for now (Windows and Linux are paused, plan.md section 4).
cmake_minimum_required(VERSION 3.20)
project(slicewright_engine LANGUAGES C CXX)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/cmake")

set(SLICEWRIGHT_ORCA_SRC "${CMAKE_CURRENT_LIST_DIR}/build/orca-src"
    CACHE PATH "Orca source with the patch series applied (tools/apply_patches.py)")
set(SLICEWRIGHT_DEPS_DIR "${CMAKE_CURRENT_LIST_DIR}/build/deps"
    CACHE PATH "Build directory of engine/deps (its slicewright-deps.cmake supplies CMAKE_PREFIX_PATH)")

if (NOT EXISTS "${SLICEWRIGHT_ORCA_SRC}/src/libslic3r/CMakeLists.txt")
    message(FATAL_ERROR "No patched Orca tree at ${SLICEWRIGHT_ORCA_SRC}. Run: python engine/tools/apply_patches.py "
                        "(or pass -DSLICEWRIGHT_ORCA_SRC=<dir>)")
endif ()
get_filename_component(ORCA_DIR "${SLICEWRIGHT_ORCA_SRC}" ABSOLUTE)
if (EXISTS "${SLICEWRIGHT_DEPS_DIR}/slicewright-deps.cmake")
    include("${SLICEWRIGHT_DEPS_DIR}/slicewright-deps.cmake")
endif ()

include(slicewright_orca_libslic3r)
add_subdirectory(src/stubs)

# What the binding and the tests link: libslic3r plus the symbols its excluded files used to provide.
add_library(libslic3r_min INTERFACE)
target_link_libraries(libslic3r_min INTERFACE libslic3r slicewright_stubs)

# A small executable whose only job is to force the whole link: it pulls the print pipeline, the config
# machinery and the 3MF/STL readers out of libslic3r, so every dependency has to resolve.
add_executable(slicewright_link_check src/link_check.cpp)
target_link_libraries(slicewright_link_check PRIVATE libslic3r_min)

option(SLICEWRIGHT_BUILD_MODULE "Build the nanobind module slicewright_engine" OFF)
if (SLICEWRIGHT_BUILD_MODULE)
    add_subdirectory(src/binding)
endif ()

option(SLICEWRIGHT_BUILD_TESTS"Build the Orca Catch2 subset (tests/CMakeLists.txt)" OFF)
if (SLICEWRIGHT_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif ()
