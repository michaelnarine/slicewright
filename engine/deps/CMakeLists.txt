# SPDX-License-Identifier: AGPL-3.0-only
#
# Trimmed dependency superbuild for the slicewright engine (design 02 section 3.4).
#
# Drives OrcaSlicer's own deps/ superbuild (patched copy under engine/build/orca-src, see
# engine/tools/apply_patches.py) so that it builds only what a headless libslic3r needs:
#
#   cmake -S engine/deps -B engine/build/deps -G Ninja
#   cmake --build engine/build/deps
#
# Everything lands in <SLICEWRIGHT_DEPS_PREFIX>/usr/local (Orca appends usr/local to its DESTDIR);
# the engine's own CMake (M2) reads engine/build/deps/slicewright-deps.cmake for CMAKE_PREFIX_PATH.
# Sources are downloaded into SLICEWRIGHT_DEPS_DOWNLOAD_DIR, which CI caches and which doubles as
# the dependency source set for the corresponding-source tarball (compliance.md section 5).
cmake_minimum_required(VERSION 3.20)
project(slicewright_engine_deps NONE)

include(ExternalProject)

set(_engine_dir "${CMAKE_CURRENT_LIST_DIR}/..")
get_filename_component(_engine_dir "${_engine_dir}" ABSOLUTE)

# The recipe names under third_party/OrcaSlicer/deps/ that libslic3r needs (02 section 3.4).
# Verified against v2.4.2 by engine/deps/check_dep_list.py. GMP and MPFR are pulled in by CGAL.
# Not listed on purpose: Qhull (libslic3r links the in-tree deps_src/qhull), OCCT, OpenCV, OpenVDB,
# Draco, OpenSSL, CURL, FreeType, GLEW/GLFW/OpenCSG, Blosc/OpenEXR, wxWidgets.
set(SLICEWRIGHT_DEPS_LIST
    "Boost;TBB;Cereal;NLopt;Eigen;CGAL;PNG;ZLIB;EXPAT;JPEG;libnoise"
    CACHE STRING "Orca dependency recipes to build (passed as ORCA_DEPS_ONLY, patch 0007)")

set(SLICEWRIGHT_ORCA_SRC "${_engine_dir}/build/orca-src"
    CACHE PATH "Orca source with the patch series applied (tools/apply_patches.py)")
set(SLICEWRIGHT_DEPS_PREFIX "${CMAKE_BINARY_DIR}/prefix"
    CACHE PATH "Install root for the dependencies (they end up in <this>/usr/local)")
set(SLICEWRIGHT_DEPS_DOWNLOAD_DIR "${CMAKE_BINARY_DIR}/downloads"
    CACHE PATH "Where dependency source archives are downloaded (cache this in CI)")
set(SLICEWRIGHT_OSX_DEPLOYMENT_TARGET "11.2"
    CACHE STRING "macOS deployment target; Blender 5.1.2 is 11.2 (02 section 3.4); Orca's own default is 11.3")
set(SLICEWRIGHT_DEPS_CMAKE_ARGS ""
    CACHE STRING "Extra -D arguments forwarded to Orca's deps project (semicolon separated)")

# ---- Windows GMP/MPFR -------------------------------------------------------------------------
# Orca builds GMP/MPFR from source on Linux and macOS, but on MSVC its recipes copy prebuilt DLLs
# that are committed under deps/GMP/gmp and deps/MPFR/mpfr. Whether we build them ourselves with
# MSVC (ON, patch 0008) or vendor those DLLs through delvewheel (OFF) is decided by Phase 0 spike (c).
# TODO(spike-c): set the Windows default from the spike result (plan.md section 4, "Phase 0 (c)";
#   02 section 3.4; compliance.md section 4). Until then: OFF on MSVC (Orca's behaviour, works today),
#   and ON = "build from source" is not implemented on MSVC, so patch 0008 is missing.
if (MSVC)
    set(_gmp_source_default OFF)
else ()
    set(_gmp_source_default ON)
endif ()
option(SLICEWRIGHT_GMP_SOURCE "Build GMP and MPFR from source (always the case off Windows)" ${_gmp_source_default})

if (NOT MSVC AND NOT SLICEWRIGHT_GMP_SOURCE)
    message(WARNING "SLICEWRIGHT_GMP_SOURCE=OFF has no effect off Windows: Orca's GMP/MPFR recipes always build from source here.")
    set(SLICEWRIGHT_GMP_SOURCE ON)
endif ()
if (MSVC AND SLICEWRIGHT_GMP_SOURCE)
    message(FATAL_ERROR
        "SLICEWRIGHT_GMP_SOURCE=ON on MSVC needs patch 0008 (GMP/MPFR from source with MSVC), which is not "
        "written yet. TODO(spike-c): implement it if the spike chooses to build from source. "
        "Use -DSLICEWRIGHT_GMP_SOURCE=OFF to vendor Orca's prebuilt DLLs.")
endif ()
if (MSVC)
    # Record exactly what we would ship (compliance.md section 4: URL, version, hash).
    set(_prov "${SLICEWRIGHT_DEPS_PREFIX}/share/slicewright/prebuilt-gmp-mpfr.txt")
    set(_prov_text "Prebuilt GMP 6.2.1 / MPFR 4.2.2 DLLs taken from the pinned OrcaSlicer tree (deps/GMP/gmp, deps/MPFR/mpfr)\n")
    foreach (_dll
        deps/GMP/gmp/lib/win-x64/libgmp-10.dll
        deps/MPFR/mpfr/lib/win-x64/libmpfr-4.dll)
        if (EXISTS "${SLICEWRIGHT_ORCA_SRC}/${_dll}")
            file(SHA256 "${SLICEWRIGHT_ORCA_SRC}/${_dll}" _hash)
            string(APPEND _prov_text "${_dll} sha256=${_hash}\n")
        endif ()
    endforeach ()
    file(WRITE "${_prov}" "${_prov_text}")
endif ()

# ---- the patched Orca tree --------------------------------------------------------------------
if (NOT EXISTS "${SLICEWRIGHT_ORCA_SRC}/.slicewright-patched")
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    message(STATUS "Applying the Orca patch series to ${SLICEWRIGHT_ORCA_SRC}")
    execute_process(
        COMMAND "${Python3_EXECUTABLE}" "${_engine_dir}/tools/apply_patches.py" --work "${SLICEWRIGHT_ORCA_SRC}"
        RESULT_VARIABLE _rc)
    if (NOT _rc EQUAL 0)
        message(FATAL_ERROR "apply_patches.py failed (exit ${_rc}); is the OrcaSlicer submodule initialised? "
                            "git submodule update --init --depth 1")
    endif ()
endif ()
file(READ "${SLICEWRIGHT_ORCA_SRC}/deps/CMakeLists.txt" _orca_deps_cmake)
if (NOT _orca_deps_cmake MATCHES "ORCA_DEPS_ONLY")
    message(FATAL_ERROR "${SLICEWRIGHT_ORCA_SRC}/deps does not have patch 0007 (ORCA_DEPS_ONLY). "
                        "Run engine/tools/apply_patches.py --force.")
endif ()

# ---- the superbuild ---------------------------------------------------------------------------
set(_args
    -DORCA_DEPS_ONLY:STRING=${SLICEWRIGHT_DEPS_LIST}
    -DDESTDIR:PATH=${SLICEWRIGHT_DEPS_PREFIX}
    -DDEP_DOWNLOAD_DIR:PATH=${SLICEWRIGHT_DEPS_DOWNLOAD_DIR}
    -DCMAKE_BUILD_TYPE:STRING=Release
    -DCMAKE_POLICY_VERSION_MINIMUM:STRING=3.5)
if (APPLE)
    list(APPEND _args -DCMAKE_OSX_DEPLOYMENT_TARGET:STRING=${SLICEWRIGHT_OSX_DEPLOYMENT_TARGET})
    if (CMAKE_OSX_ARCHITECTURES)
        list(APPEND _args -DCMAKE_OSX_ARCHITECTURES:STRING=${CMAKE_OSX_ARCHITECTURES})
    endif ()
endif ()
foreach (_v CMAKE_C_COMPILER CMAKE_CXX_COMPILER CMAKE_C_COMPILER_LAUNCHER CMAKE_CXX_COMPILER_LAUNCHER)
    if (NOT "${${_v}}" STREQUAL "")
        list(APPEND _args -D${_v}:STRING=${${_v}})
    endif ()
endforeach ()
list(APPEND _args ${SLICEWRIGHT_DEPS_CMAKE_ARGS})

ExternalProject_Add(orca_deps
    SOURCE_DIR          "${SLICEWRIGHT_ORCA_SRC}/deps"
    BINARY_DIR          "${CMAKE_BINARY_DIR}/orca-deps-build"
    DOWNLOAD_COMMAND    ""
    UPDATE_COMMAND      ""
    CMAKE_GENERATOR     "${CMAKE_GENERATOR}"
    CMAKE_CACHE_ARGS    ${_args}
    INSTALL_COMMAND     ""
    USES_TERMINAL_CONFIGURE ON
    USES_TERMINAL_BUILD ON
    BUILD_ALWAYS        OFF)

# Configure the nested Orca project on its own (check_dep_list.py, CI): downloads and compiles nothing.
ExternalProject_Add_StepTargets(orca_deps configure)

# What the engine's CMake (M2) includes to find the dependencies.
file(WRITE "${CMAKE_BINARY_DIR}/slicewright-deps.cmake"
"# Generated by engine/deps/CMakeLists.txt
set(SLICEWRIGHT_DEPS_PREFIX_PATH \"${SLICEWRIGHT_DEPS_PREFIX}/usr/local\")
list(APPEND CMAKE_PREFIX_PATH \"\${SLICEWRIGHT_DEPS_PREFIX_PATH}\")
set(SLICEWRIGHT_DEPS_LIST \"${SLICEWRIGHT_DEPS_LIST}\")
set(SLICEWRIGHT_GMP_SOURCE ${SLICEWRIGHT_GMP_SOURCE})
")

message(STATUS "slicewright deps: ${SLICEWRIGHT_DEPS_LIST}")
message(STATUS "slicewright deps: prefix ${SLICEWRIGHT_DEPS_PREFIX}/usr/local, downloads ${SLICEWRIGHT_DEPS_DOWNLOAD_DIR}")
