# SPDX-License-Identifier: AGPL-3.0-only
#
# The nanobind module (02 section 4). The package `slicewright_engine` is a thin pure-Python layer
# (engine/python/slicewright_engine/__init__.py) over the extension `_native` (cp312-abi3, imports on Blender
# 5.1's Python 3.13). The layer exists because nanobind functions expose only (*args, **kwargs) to
# inspect.signature, and the contract suite checks signatures against the 04 stub. The build assembles
#   <build>/python/slicewright_engine/{__init__.py, _native.abi3.so, resources/, profiles.zip, LICENSE, ...}
# so that `PYTHONPATH=<build>/python` makes `import slicewright_engine` work; the wheel packs the same directory (M7).

find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module Development.SABIModule)
execute_process(COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
                OUTPUT_VARIABLE _nb_dir OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
list(APPEND CMAKE_PREFIX_PATH "${_nb_dir}")
find_package(nanobind CONFIG REQUIRED)

set(SLICEWRIGHT_ENGINE_VERSION "1.0.0.dev0" CACHE STRING "Engine package version (PEP 440)")
set(SLICEWRIGHT_SOURCE_URL "https://github.com/michaelnarine/slicewright/releases/tag/engine-v${SLICEWRIGHT_ENGINE_VERSION}"
    CACHE STRING "Where the corresponding source is published (compliance.md)")

# ---- build information for version() ------------------------------------------------------------------
file(STRINGS "${PROJECT_SOURCE_DIR}/ORCA_PIN" _pin_lines)
foreach (_l ${_pin_lines})
    if (_l MATCHES "^tag=(.+)$")
        set(SW_ORCA_TAG "${CMAKE_MATCH_1}")
    elseif (_l MATCHES "^commit=(.+)$")
        set(SW_ORCA_COMMIT "${CMAKE_MATCH_1}")
    endif ()
endforeach ()
set(SW_BUILD_COMPILER "${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
if (APPLE AND CMAKE_OSX_ARCHITECTURES MATCHES "arm64")
    set(SW_BUILD_PLATFORM "macos-arm64")
elseif (APPLE AND CMAKE_SYSTEM_PROCESSOR STREQUAL "arm64")
    set(SW_BUILD_PLATFORM "macos-arm64")
elseif (WIN32)
    set(SW_BUILD_PLATFORM "windows-x64")
else ()
    set(SW_BUILD_PLATFORM "linux-x64")
endif ()
string(TIMESTAMP SW_BUILD_DATE "%Y-%m-%d" UTC)   # honours SOURCE_DATE_EPOCH

# The applied patch series, in order, without the no-op tooling example.
file(GLOB _patch_files "${PROJECT_SOURCE_DIR}/patches/orca/*.patch")
list(SORT _patch_files)
set(SW_PATCHES "")
foreach (_p ${_patch_files})
    file(STRINGS "${_p}" _noop REGEX "^Noop: *true")
    if (NOT _noop)
        get_filename_component(_n "${_p}" NAME_WE)
        list(APPEND SW_PATCHES "${_n}")
    endif ()
endforeach ()

set(SW_DEPS "")
foreach (_d "boost=${Boost_VERSION}" "tbb=${TBB_VERSION}" "eigen=${Eigen3_VERSION}" "cgal=${CGAL_VERSION}"
            "zlib=${ZLIB_VERSION}" "png=${PNG_VERSION_STRING}" "nlopt=${NLopt_VERSION}" "cereal=${cereal_VERSION}")
    if (NOT _d MATCHES "=$")
        list(APPEND SW_DEPS "${_d}")
    endif ()
endforeach ()

# configure_file substitutes a list as "a;b;c", which is what version_info.hpp wants.
configure_file(version_info.hpp.in "${CMAKE_CURRENT_BINARY_DIR}/version_info.hpp" @ONLY)

# ---- the module -------------------------------------------------------------------------------------------
set(SW_PKG_DIR "${CMAKE_BINARY_DIR}/python/slicewright_engine")
nanobind_add_module(slicewright_module STABLE_ABI NB_STATIC
    module.cpp
    errors.cpp
    runtime.cpp
    config.cpp
    checks.cpp
    paint.cpp
    stats.cpp
    thumbnails.cpp
    testing.cpp
    result.cpp
    moves.cpp
    job.cpp
    ../glue/print_glue.cpp
    ../glue/arrange_glue.cpp
    ../glue/plate_glue.cpp
)
target_include_directories(slicewright_module PRIVATE "${CMAKE_CURRENT_BINARY_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(slicewright_module PRIVATE libslic3r_min)
set_target_properties(slicewright_module PROPERTIES
    OUTPUT_NAME "_native"
    LIBRARY_OUTPUT_DIRECTORY "${SW_PKG_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${SW_PKG_DIR}"
)
foreach (_cfg DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(slicewright_module PROPERTIES LIBRARY_OUTPUT_DIRECTORY_${_cfg} "${SW_PKG_DIR}")
endforeach ()

# Symbol hygiene (02 section 3.5): only PyInit__native leaves the module.
if (APPLE)
    set(_exports "${CMAKE_CURRENT_BINARY_DIR}/exports.list")
    file(WRITE "${_exports}" "_PyInit__native\n")
    target_link_options(slicewright_module PRIVATE "-Wl,-exported_symbols_list,${_exports}" -Wl,-dead_strip)
elseif (UNIX)
    set(_vs "${CMAKE_CURRENT_BINARY_DIR}/exports.map")
    file(WRITE "${_vs}" "{ global: PyInit__native; local: *; };\n")
    target_link_options(slicewright_module PRIVATE "-Wl,--version-script=${_vs}" -Wl,--exclude-libs,ALL -Wl,-Bsymbolic
                        -Wl,--gc-sections -Wl,-z,defs)
endif ()

# ---- package data -----------------------------------------------------------------------------------------
# resources/{info,flush} (Orca's resources dir, 02 section 4.2), profiles.zip, and the licence texts. The
# licence files are placeholders until release packaging (M7) generates the real THIRD_PARTY_LICENSES and NOTICE.
add_custom_command(
    OUTPUT "${SW_PKG_DIR}/profiles.zip"
    COMMAND "${Python_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/make_profiles_zip.py"
            "${ORCA_DIR}/resources/profiles" "${SW_PKG_DIR}/profiles.zip"
    DEPENDS "${PROJECT_SOURCE_DIR}/tools/make_profiles_zip.py"
    COMMENT "Packing profiles.zip")
# tab_layout.json: parsed from Orca's Tab.cpp plus the hand-maintained overrides (02 section 6), then checked
# against the option definitions in PrintConfig.cpp (a key on a page that is not in the schema fails the build).
add_custom_command(
    OUTPUT "${SW_PKG_DIR}/tab_layout.json"
    COMMAND "${Python_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/gen_tab_layout.py"
            --tab-cpp "${ORCA_DIR}/src/slic3r/GUI/Tab.cpp"
            --overrides "${PROJECT_SOURCE_DIR}/tools/tab_layout_overrides.json"
            --out "${SW_PKG_DIR}/tab_layout.json" --strict
    COMMAND "${Python_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/check_tab_layout.py"
            --layout "${SW_PKG_DIR}/tab_layout.json" --orca-tree "${ORCA_DIR}"
    DEPENDS "${PROJECT_SOURCE_DIR}/tools/gen_tab_layout.py" "${PROJECT_SOURCE_DIR}/tools/check_tab_layout.py"
            "${PROJECT_SOURCE_DIR}/tools/tab_layout_overrides.json" "${ORCA_DIR}/src/slic3r/GUI/Tab.cpp"
    COMMENT "Generating tab_layout.json")
add_custom_target(slicewright_package_data ALL
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${SW_PKG_DIR}/resources"
    COMMAND "${CMAKE_COMMAND}" -E copy_directory "${ORCA_DIR}/resources/info" "${SW_PKG_DIR}/resources/info"
    COMMAND "${CMAKE_COMMAND}" -E copy_directory "${ORCA_DIR}/resources/flush" "${SW_PKG_DIR}/resources/flush"
    COMMAND "${CMAKE_COMMAND}" -E copy "${PROJECT_SOURCE_DIR}/LICENSE" "${SW_PKG_DIR}/LICENSE"
    COMMAND "${CMAKE_COMMAND}" -E copy "${PROJECT_SOURCE_DIR}/python/slicewright_engine/__init__.py" "${SW_PKG_DIR}/__init__.py"
    DEPENDS "${SW_PKG_DIR}/profiles.zip" "${SW_PKG_DIR}/tab_layout.json"
    COMMENT "Copying package data")
add_dependencies(slicewright_module slicewright_package_data)
